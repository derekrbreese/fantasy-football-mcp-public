const test = require('node:test');
const assert = require('node:assert/strict');

const {
  createDraftStorage,
  createSessionOperationLock,
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

function queuedLockManager() {
  const tails = new Map();
  return {
    request(name, operation) {
      const preceding = tails.get(name) || Promise.resolve();
      const current = preceding.then(operation);
      tails.set(name, current.catch(() => undefined));
      return current;
    },
  };
}

test('concurrent league writes use independent keys and cannot clobber each other', async () => {
  const api = fakeExtensionStorage();
  const firstTab = createDraftStorage(api);
  const secondTab = createDraftStorage(api);
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
  const storage = createDraftStorage(api);

  assert.equal(await storage.getSession('f1:legacy'), legacy);
  assert.deepEqual(await storage.listSessions(), { 'f1:legacy': legacy });

  const updated = { ...legacy, picks: [{ pickNumber: 1 }, { pickNumber: 2 }] };
  await storage.setSession('f1:legacy', updated);
  assert.equal(await storage.getSession('f1:legacy'), updated);
  assert.deepEqual(api.data.yahooDraftRecorderSessions, { 'f1:legacy': legacy });
});

test('per-league tombstone prevents cleared legacy session from reappearing', async () => {
  const legacy = { sessionKey: 'f1:legacy', picks: [{ pickNumber: 1 }], updatedAt: '2026-08-01T00:00:00.000Z' };
  const storage = createDraftStorage(fakeExtensionStorage({
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
  const storage = createDraftStorage(api);

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
  const storage = createDraftStorage(api);

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

test('same-league scan and repair operations serialize across tabs', async () => {
  const lockManager = queuedLockManager();
  const firstTab = createSessionOperationLock(lockManager);
  const secondTab = createSessionOperationLock(lockManager);
  const operations = [];
  let releaseFirst;
  const firstGate = new Promise((resolve) => { releaseFirst = resolve; });

  const scan = firstTab.run('f1:league-a', async () => {
    operations.push('scan-start');
    await firstGate;
    operations.push('scan-end');
  });
  const repair = secondTab.run('f1:league-a', async () => operations.push('repair'));
  const otherLeague = secondTab.run('f1:league-b', async () => operations.push('other-league'));

  await otherLeague;
  assert.deepEqual(operations, ['scan-start', 'other-league']);
  releaseFirst();
  await Promise.all([scan, repair]);
  assert.deepEqual(operations, ['scan-start', 'other-league', 'scan-end', 'repair']);
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
    const storage = createDraftStorage(api, { lockManager: queuedLockManager() });

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
  const lockManager = queuedLockManager();
  const firstPopup = createDraftStorage(api, { lockManager });
  const secondPopup = createDraftStorage(api, { lockManager });

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
  const scanTab = createDraftStorage(api);
  const popup = createDraftStorage(api, { lockManager: queuedLockManager() });
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
