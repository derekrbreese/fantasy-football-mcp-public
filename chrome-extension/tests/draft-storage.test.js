const test = require('node:test');
const assert = require('node:assert/strict');

const {
  createDraftStorage,
  isTabBlockedByReset,
} = require('../draft-storage.js');

function fakeExtensionStorage(initial = {}) {
  const data = { ...initial };
  const setCalls = [];
  const removeCalls = [];
  return {
    data,
    removeCalls,
    setCalls,
    async storageGet(key) {
      if (key === null) return { ...data };
      return { [key]: data[key] };
    },
    async storageSet(values) {
      await Promise.resolve();
      setCalls.push(values);
      Object.assign(data, values);
    },
    async storageRemove(key) {
      await Promise.resolve();
      removeCalls.push(key);
      for (const item of Array.isArray(key) ? key : [key]) delete data[item];
    },
  };
}

function queuedOperationLock() {
  const tails = new Map();
  function request(name, operation) {
    const preceding = tails.get(name) || Promise.resolve();
    const current = preceding.then(operation);
    tails.set(name, current.catch(() => undefined));
    return current;
  }
  return {
    run(sessionKey, operation) { return request(`session:${sessionKey}`, operation); },
    runGlobal(operation) { return request('legacy-storage', operation); },
  };
}

function createTestDraftStorage(api, operationLock = queuedOperationLock()) {
  return createDraftStorage(api, { operationLock });
}

test('concurrent league writes use independent keys and cannot clobber each other', async () => {
  const api = fakeExtensionStorage();
  const firstTab = createTestDraftStorage(api);
  const secondTab = createTestDraftStorage(api);
  const leagueA = { sessionKey: 'f1:league-a', picks: [{ pickNumber: 1 }] };
  const leagueB = { sessionKey: 'f1:league-b', picks: [{ pickNumber: 7 }] };

  await Promise.all([
    firstTab.setSession('f1:league-a', leagueA),
    secondTab.setSession('f1:league-b', leagueB),
  ]);

  assert.equal(await firstTab.getSession('f1:league-a'), leagueA);
  assert.equal(await firstTab.getSession('f1:league-b'), leagueB);
});

test('legacy aggregate sessions remain readable without aggregate rewrites', async () => {
  const legacy = { sessionKey: 'f1:legacy', picks: [{ pickNumber: 1 }] };
  const api = fakeExtensionStorage({ yahooDraftRecorderSessions: { 'f1:legacy': legacy } });
  const storage = createTestDraftStorage(api);

  assert.equal(await storage.getSession('f1:legacy'), legacy);
  assert.deepEqual(await storage.listSessions(), { 'f1:legacy': legacy });

  const updated = { ...legacy, picks: [{ pickNumber: 1 }, { pickNumber: 2 }] };
  await storage.setSession('f1:legacy', updated);
  assert.equal(await storage.getSession('f1:legacy'), updated);
  assert.deepEqual(api.data.yahooDraftRecorderSessions, { 'f1:legacy': legacy });
});

test('per-league tombstone prevents cleared legacy session from reappearing', async () => {
  const legacy = { sessionKey: 'f1:legacy', picks: [{ pickNumber: 1 }], updatedAt: '2026-08-01T00:00:00.000Z' };
  const storage = createTestDraftStorage(fakeExtensionStorage({
    yahooDraftRecorderSessions: { 'f1:legacy': legacy },
  }));

  await storage.clearSession('f1:legacy', '2026-08-01T00:01:00.000Z');
  assert.equal(await storage.getSession('f1:legacy'), null);
  assert.deepEqual(await storage.listSessions(), {});
});

test('reset cleanup preserves unrelated extension data such as imported-profile UI state', async () => {
  const sessionKey = 'f1:10547893';
  const profileUiState = { leagueId: '10547893', source: 'DraftSheets-2026.xlsx' };
  const api = fakeExtensionStorage({
    [`yahooDraftRecorderSession:${encodeURIComponent(sessionKey)}`]: {
      sessionKey,
      picks: [{ pickNumber: 1 }],
      updatedAt: '2026-09-01T23:15:00.000Z',
    },
    yahooDraftProfileUiState: profileUiState,
  });
  const storage = createTestDraftStorage(api);

  await storage.clearSession(sessionKey, '2026-09-01T23:16:00.000Z');

  assert.deepEqual(api.data.yahooDraftProfileUiState, profileUiState);
  assert.equal(await storage.getSession(sessionKey), null);
});

test('server-accepted reset atomically clears draft and repair state before reset journal', async () => {
  const sessionKey = 'f1:10547893';
  const encoded = encodeURIComponent(sessionKey);
  const accepted = {
    schemaVersion: 1,
    state: 'accepted',
    sessionKey,
    expectedGeneratedAt: '2026-09-01T23:15:00.000Z',
    draft: { sport: 'f1', leagueId: '10547893', teamId: '6', sessionKey },
    resetAt: '2026-09-01T23:16:00.000Z',
  };
  const api = fakeExtensionStorage({
    [`yahooDraftRecorderSession:${encoded}`]: {
      ...accepted.draft,
      updatedAt: accepted.expectedGeneratedAt,
      picks: [{ pickNumber: 1 }],
    },
    [`yahooDraftRecorderPendingRepair:${encoded}`]: { state: 'intent' },
    [`yahooDraftRecorderPendingReset:${encoded}`]: accepted,
  });
  const storage = createTestDraftStorage(api);

  await storage.finalizeReset(sessionKey, accepted.resetAt);

  assert.deepEqual(api.setCalls[0], {
    [`yahooDraftRecorderSession:${encoded}`]: null,
    [`yahooDraftRecorderSessionDeleted:${encoded}`]: { clearedAt: accepted.resetAt },
    [`yahooDraftRecorderPendingRepair:${encoded}`]: null,
  });
  assert.equal(await storage.getSession(sessionKey), null);
  assert.equal(await storage.getResetAt(sessionKey), accepted.resetAt);
  assert.equal(await storage.getPendingRepair(sessionKey), null);
  assert.equal(await storage.getPendingReset(sessionKey), accepted);

  await storage.clearPendingReset(sessionKey);
  assert.equal(await storage.getPendingReset(sessionKey), null);
});

test('tabs loaded before reset stay blocked until explicitly authorized or reloaded', () => {
  const resetAt = '2026-09-01T23:16:00.123456Z';
  assert.equal(isTabBlockedByReset('2026-09-01T23:15:00.000Z', resetAt, null), true);
  assert.equal(isTabBlockedByReset('2026-09-01T23:15:00.000Z', resetAt, resetAt), false);
  assert.equal(isTabBlockedByReset('2026-09-01T23:16:00.124Z', resetAt, null), false);
  assert.equal(isTabBlockedByReset('not-a-time', resetAt, null), true);
});

test('models Firefox content-script Web Locks rejecting after an async operation starts', async () => {
  let operationRuns = 0;
  const inaccessibleThenable = new Proxy({}, {
    get(_target, property) {
      if (property === 'then') {
        throw new Error('Permission denied to access property "then"');
      }
      return undefined;
    },
  });
  const firefoxContentScriptLocks = {
    request(_name, operation) {
      const running = operation();
      assert.equal(typeof running?.then, 'function');
      running.catch(() => undefined);
      return inaccessibleThenable;
    },
  };
  const returned = firefoxContentScriptLocks.request('unsafe-page-realm-lock', async () => {
    operationRuns += 1;
    return 'completed in the extension compartment';
  });

  await assert.rejects(
    Promise.resolve(returned),
    /Permission denied to access property "then"/,
  );
  assert.equal(operationRuns, 1, 'the mutation can start before Firefox reports failure');
});

for (const pendingState of ['intent', 'accepted']) {
  test(`clear atomically tombstones the league and removes ${pendingState} repair state`, async () => {
    const sessionKey = 'f1:league-a';
    const api = fakeExtensionStorage({
      [`yahooDraftRecorderSession:${encodeURIComponent(sessionKey)}`]: { sessionKey, picks: [{ pickNumber: 1 }] },
      [`yahooDraftRecorderPendingRepair:${encodeURIComponent(sessionKey)}`]: {
        schemaVersion: 1,
        state: pendingState,
        sessionKey,
        session: { sessionKey, picks: [{ pickNumber: 1 }] },
      },
      yahooDraftRecorderSessions: { [sessionKey]: { sessionKey, picks: [{ pickNumber: 1 }] } },
    });
    const storage = createTestDraftStorage(api);

    await storage.clearSession(sessionKey, '2026-08-01T00:01:00.000Z');

    assert.deepEqual(api.setCalls[0], {
      [`yahooDraftRecorderSession:${encodeURIComponent(sessionKey)}`]: null,
      [`yahooDraftRecorderSessionDeleted:${encodeURIComponent(sessionKey)}`]: {
        clearedAt: '2026-08-01T00:01:00.000Z',
      },
      [`yahooDraftRecorderPendingRepair:${encodeURIComponent(sessionKey)}`]: null,
    });
    assert.equal(await storage.getSession(sessionKey), null);
    assert.equal(await storage.getPendingRepair(sessionKey), null);
    assert.equal('yahooDraftRecorderSessions' in api.data, false);
  });
}

test('concurrent clears remove only their leagues from the legacy aggregate', async () => {
  const untouched = { sessionKey: 'f1:league-c', picks: [{ pickNumber: 9 }] };
  const api = fakeExtensionStorage({
    yahooDraftRecorderSessions: {
      'f1:league-a': { sessionKey: 'f1:league-a', picks: [{ pickNumber: 1 }] },
      'f1:league-b': { sessionKey: 'f1:league-b', picks: [{ pickNumber: 2 }] },
      'f1:league-c': untouched,
    },
  });
  const operationLock = queuedOperationLock();
  const firstPopup = createTestDraftStorage(api, operationLock);
  const secondPopup = createTestDraftStorage(api, operationLock);

  await Promise.all([
    firstPopup.clearSession('f1:league-a'),
    secondPopup.clearSession('f1:league-b'),
  ]);

  assert.deepEqual(api.data.yahooDraftRecorderSessions, { 'f1:league-c': untouched });
});

test('a scan read before clear cannot resurrect its stale session afterward', async () => {
  const sessionKey = 'f1:league-a';
  const api = fakeExtensionStorage({
    [`yahooDraftRecorderSession:${encodeURIComponent(sessionKey)}`]: {
      sessionKey,
      picks: [{ pickNumber: 1 }],
      updatedAt: '2026-08-01T00:00:00.000Z',
    },
  });
  const scanTab = createTestDraftStorage(api);
  const popup = createTestDraftStorage(api);
  const staleRead = await scanTab.getSession(sessionKey);

  await popup.clearSession(sessionKey, '2026-08-01T00:02:00.000Z');
  await scanTab.setSession(sessionKey, {
    ...staleRead,
    picks: [...staleRead.picks, { pickNumber: 2 }],
    updatedAt: '2026-08-01T00:01:00.000Z',
  });

  assert.equal(await scanTab.getSession(sessionKey), null);
  assert.deepEqual(await scanTab.listSessions(), {});

  const laterScan = { sessionKey, picks: [{ pickNumber: 1 }], updatedAt: '2026-08-01T00:03:00.000Z' };
  await scanTab.setSession(sessionKey, laterScan);
  assert.equal(await popup.getSession(sessionKey), laterScan);
});
