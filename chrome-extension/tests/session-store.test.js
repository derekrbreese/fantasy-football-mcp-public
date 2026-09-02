const test = require('node:test');
const assert = require('node:assert/strict');
const { sessionToAgentContext } = require('../agent-context.js');
const { analyzeLedger } = require('../ledger-health.js');

const {
  commitDraftRepair,
  createDurableRepairCoordinator,
  createDurableResetCoordinator,
  prepareAutomaticAuthoritativeUpdate,
  repairDraftSession,
  sameDraftIdentity,
  updateDraftSessionFromSecondaryObservations,
  updateDraftSessionFromAuthoritativeLedger,
  updateDraftSession,
} = require('../session-store.js');

const RESET_METADATA = {
  sport: 'f1', leagueId: '10547893', teamId: '6', sessionKey: 'f1:10547893',
};

test('exact active draft identity includes team ID, not only sport and league', () => {
  assert.equal(sameDraftIdentity(RESET_METADATA, { ...RESET_METADATA }), true);
  assert.equal(sameDraftIdentity(RESET_METADATA, { ...RESET_METADATA, teamId: '7' }), false);
  assert.equal(sameDraftIdentity(RESET_METADATA, { ...RESET_METADATA, sessionKey: 'f1:other' }), false);
});

function resetHarness(overrides = {}) {
  let pending = overrides.pending || null;
  const operations = [];
  const coordinator = createDurableResetCoordinator({
    readPending: async () => pending,
    writePending: async (record) => {
      operations.push(`write:${record.state}`);
      pending = record;
    },
    clearPending: async () => {
      operations.push('clear-journal');
      pending = null;
    },
    resetServer: async (snapshot) => {
      operations.push(`server:${snapshot.updatedAt}`);
      return {
        status: 'ok',
        sessionKey: RESET_METADATA.sessionKey,
        resetAt: '2026-09-01T23:16:00.000Z',
        profilePreserved: true,
      };
    },
    finalizeReset: async (_sessionKey, resetAt) => operations.push(`finalize:${resetAt}`),
    ...overrides,
  });
  return { coordinator, getPending: () => pending, operations };
}

test('successful reset journals intent, resets server, then finalizes browser at server time', async () => {
  const existing = {
    ...RESET_METADATA,
    updatedAt: '2026-09-01T23:15:00.000Z',
    picks: [{ pickNumber: 1 }],
  };
  const { coordinator, getPending, operations } = resetHarness();
  await coordinator.begin(existing);
  const result = await coordinator.reconcile();

  assert.equal(result.ok, true);
  assert.equal(result.profilePreserved, true);
  assert.equal(getPending(), null);
  assert.deepEqual(operations, [
    'write:intent',
    'server:2026-09-01T23:15:00.000Z',
    'write:accepted',
    'finalize:2026-09-01T23:16:00.000Z',
    'clear-journal',
  ]);
});

test('reset intent rejects a mismatched session identity before journaling', async () => {
  const { coordinator, getPending, operations } = resetHarness();
  await assert.rejects(
    coordinator.begin({
      ...RESET_METADATA,
      leagueId: 'other',
      updatedAt: '2026-09-01T23:15:00.000Z',
    }),
    /identity/i,
  );
  assert.equal(getPending(), null);
  assert.deepEqual(operations, []);
});

test('server reset failure preserves exact browser state and error', async () => {
  const existing = {
    ...RESET_METADATA,
    updatedAt: '2026-09-01T23:15:00.000Z',
    picks: [{ pickNumber: 1 }],
  };
  let pending;
  let finalizeCalls = 0;
  const { coordinator } = resetHarness({
    readPending: async () => pending,
    writePending: async (record) => { pending = record; },
    resetServer: async () => { throw new Error('Draft changed; rescan before reset.'); },
    finalizeReset: async () => { finalizeCalls += 1; },
  });
  await coordinator.begin(existing);
  const result = await coordinator.reconcile();

  assert.equal(result.ok, false);
  assert.match(result.error, /Draft changed; rescan before reset\./);
  assert.equal(pending.state, 'intent');
  assert.equal(finalizeCalls, 0);
});

test('draft-changed conflict clears reset intent so a rescan can refresh the snapshot', async () => {
  const existing = {
    ...RESET_METADATA,
    updatedAt: '2026-09-01T23:15:00.000Z',
    picks: [{ pickNumber: 1 }],
  };
  let pending;
  let finalizeCalls = 0;
  const coordinator = createDurableResetCoordinator({
    readPending: async () => pending,
    writePending: async (record) => { pending = record; },
    clearPending: async () => { pending = null; },
    resetServer: async () => {
      const error = new Error('Draft changed; rescan before reset.');
      error.status = 409;
      throw error;
    },
    finalizeReset: async () => { finalizeCalls += 1; },
  });
  await coordinator.begin(existing);
  const result = await coordinator.reconcile();

  assert.equal(result.ok, false);
  assert.equal(result.retryAfterRescan, true);
  assert.equal(pending, null);
  assert.equal(finalizeCalls, 0);
});

test('missing server session clears reset intent so a forced resync can recover', async () => {
  const existing = {
    ...RESET_METADATA,
    updatedAt: '2026-09-01T23:15:00.000Z',
    picks: [{ pickNumber: 1 }],
  };
  let pending;
  const coordinator = createDurableResetCoordinator({
    readPending: async () => pending,
    writePending: async (record) => { pending = record; },
    clearPending: async () => { pending = null; },
    resetServer: async () => {
      const error = new Error('Exact live draft session not found');
      error.status = 404;
      throw error;
    },
    finalizeReset: async () => assert.fail('404 must not clear local state'),
  });
  await coordinator.begin(existing);
  const result = await coordinator.reconcile();

  assert.equal(result.ok, false);
  assert.equal(result.retryAfterRescan, true);
  assert.equal(pending, null);
});

test('invalid reset acknowledgement cannot clear browser state', async () => {
  const existing = {
    ...RESET_METADATA,
    updatedAt: '2026-09-01T23:15:00.000Z',
    picks: [{ pickNumber: 1 }],
  };
  let pending;
  let finalizeCalls = 0;
  const { coordinator } = resetHarness({
    readPending: async () => pending,
    writePending: async (record) => { pending = record; },
    resetServer: async () => ({
      status: 'ok',
      sessionKey: 'f1:different',
      resetAt: '2026-09-01T23:16:00.000Z',
      profilePreserved: true,
    }),
    finalizeReset: async () => { finalizeCalls += 1; },
  });
  await coordinator.begin(existing);
  const result = await coordinator.reconcile();

  assert.equal(result.ok, false);
  assert.match(result.error, /invalid reset acknowledgement/i);
  assert.equal(pending.state, 'intent');
  assert.equal(finalizeCalls, 0);
});

test('accepted reset survives reload and retries browser finalization without another POST', async () => {
  const existing = {
    ...RESET_METADATA,
    updatedAt: '2026-09-01T23:15:00.000Z',
    picks: [{ pickNumber: 1 }],
  };
  let pending;
  let serverCalls = 0;
  let finalizeCalls = 0;
  const dependencies = {
    readPending: async () => pending,
    writePending: async (record) => { pending = record; },
    clearPending: async () => { pending = null; },
    resetServer: async () => {
      serverCalls += 1;
      return {
        status: 'ok',
        sessionKey: RESET_METADATA.sessionKey,
        resetAt: '2026-09-01T23:16:00.000Z',
        profilePreserved: true,
      };
    },
    finalizeReset: async () => {
      finalizeCalls += 1;
      if (finalizeCalls === 1) throw new Error('storage unavailable');
    },
  };
  const firstPopup = createDurableResetCoordinator(dependencies);
  await firstPopup.begin(existing);
  const result = await firstPopup.reconcile();

  assert.equal(result.ok, false);
  assert.equal(result.serverAccepted, true);
  assert.equal(pending.state, 'accepted');
  assert.match(result.error, /server reset the draft.*retry/i);

  const reloadedPopup = createDurableResetCoordinator(dependencies);
  const retry = await reloadedPopup.reconcile();
  assert.equal(retry.ok, true);
  assert.equal(serverCalls, 1);
  assert.equal(finalizeCalls, 2);
  assert.equal(pending, null);
});

test('accepted-marker failure leaves intent so an idempotent server retry can recover', async () => {
  const existing = {
    ...RESET_METADATA,
    updatedAt: '2026-09-01T23:15:00.000Z',
    picks: [{ pickNumber: 1 }],
  };
  let pending;
  let writeCalls = 0;
  let serverCalls = 0;
  const dependencies = {
    readPending: async () => pending,
    writePending: async (record) => {
      writeCalls += 1;
      if (writeCalls === 2) throw new Error('marker unavailable');
      pending = record;
    },
    clearPending: async () => { pending = null; },
    resetServer: async () => {
      serverCalls += 1;
      return {
        status: 'ok',
        sessionKey: RESET_METADATA.sessionKey,
        resetAt: '2026-09-01T23:16:00.000Z',
        profilePreserved: true,
      };
    },
    finalizeReset: async () => undefined,
  };
  const coordinator = createDurableResetCoordinator(dependencies);
  await coordinator.begin(existing);
  const result = await coordinator.reconcile();
  assert.equal(result.ok, false);
  assert.equal(pending.state, 'intent');
  assert.match(result.error, /durable marker/i);

  const retry = await createDurableResetCoordinator(dependencies).reconcile();
  assert.equal(retry.ok, true);
  assert.equal(serverCalls, 2);
  assert.equal(pending, null);
});

for (const currentPickNumber of [null, 21]) {
  test(`automatic authoritative scan cannot replace saved pick 50 with visible pick 20 (current pick ${currentPickNumber || 'unavailable'})`, () => {
    const metadata = { sport: 'f1', leagueId: 'league-a', teamId: '1', sessionKey: 'f1:league-a' };
    const existing = {
      ...metadata,
      numberedLedgerAuthoritative: true,
      picks: Array.from({ length: 50 }, (_, index) => ({ pickNumber: index + 1 })),
      updatedAt: '2026-08-01T00:00:00.000Z',
    };
    const visible = Array.from({ length: 20 }, (_, index) => ({ pickNumber: index + 1 }));

    const result = prepareAutomaticAuthoritativeUpdate(
      existing,
      metadata,
      visible,
      [],
      '2026-08-01T00:01:00.000Z',
      { currentPickNumber },
    );

    assert.equal(result.ok, false);
    assert.equal(result.reason, 'downward-prefix');
    assert.equal(result.session, existing);
    assert.match(result.error, /pick 50.*pick 20/i);
    assert.match(result.error, /Full rescan & repair/);
  });
}

test('automatic authoritative scan may advance the saved ledger', () => {
  const metadata = { sport: 'f1', leagueId: 'league-a', teamId: '1', sessionKey: 'f1:league-a' };
  const existing = {
    ...metadata,
    numberedLedgerAuthoritative: true,
    picks: Array.from({ length: 20 }, (_, index) => ({ pickNumber: index + 1 })),
  };
  const visible = Array.from({ length: 21 }, (_, index) => ({ pickNumber: index + 1 }));

  const result = prepareAutomaticAuthoritativeUpdate(
    existing,
    metadata,
    visible,
    [],
    '2026-08-01T00:01:00.000Z',
  );

  assert.equal(result.ok, true);
  assert.equal(result.session.picks.at(-1).pickNumber, 21);
});

test('same-scan secondary conflicts remain visible after establishing an authoritative baseline', () => {
  const metadata = { sport: 'f1', leagueId: 'league-a', teamId: '1', sessionKey: 'f1:league-a' };
  const result = prepareAutomaticAuthoritativeUpdate(
    undefined,
    metadata,
    [{
      pickNumber: 1,
      player: 'Jahmyr Gibbs',
      position: 'RB',
      nflTeam: 'DET',
      fantasyTeam: 'Team 1',
    }],
    [
      { pickNumber: 1, player: 'J. GIBBS', position: 'RB', nflTeam: 'DET', fantasyTeam: 'Team 1' },
      { pickNumber: 1, player: 'P. Nacua', position: 'WR', nflTeam: 'LAR', fantasyTeam: 'Team 2' },
      { pickNumber: 2, player: 'Future race', position: 'WR', nflTeam: 'MIA', fantasyTeam: 'Team 3' },
    ],
    '2026-08-01T00:01:00.000Z',
    { currentPickNumber: 2 },
  );

  assert.equal(result.ok, true);
  assert.deepEqual(result.session.picks.map((pick) => pick.pickNumber), [1, 1]);
  assert.equal(result.session.picks[0].player, 'Jahmyr Gibbs');
  assert.deepEqual(analyzeLedger(result.session.picks).duplicatePickNumbers, [1]);
});

test('secondary observations cannot fill a missing number in the same authoritative scan', () => {
  const metadata = { sport: 'f1', leagueId: 'league-a', teamId: '1', sessionKey: 'f1:league-a' };
  const result = prepareAutomaticAuthoritativeUpdate(
    undefined,
    metadata,
    [
      { pickNumber: 1, player: 'Player 1' },
      { pickNumber: 2, player: 'Player 2' },
      { pickNumber: 4, player: 'Player 4' },
    ],
    [{ pickNumber: 3, player: 'Panel Player 3' }],
    '2026-08-01T00:01:00.000Z',
    { currentPickNumber: 5 },
  );

  assert.equal(result.ok, true);
  assert.deepEqual(result.session.picks.map((pick) => pick.pickNumber), [1, 2, 4]);
  assert.deepEqual(analyzeLedger(result.session.picks).missingPickNumbers, [3]);
});

test('equivalent panel and last-pick observations dedupe after an authoritative baseline', () => {
  const metadata = { sport: 'f1', leagueId: 'league-a', teamId: '1', sessionKey: 'f1:league-a' };
  const existing = {
    ...metadata,
    numberedLedgerAuthoritative: true,
    picks: [{ pickNumber: 1, player: 'J. Gibbs', position: 'RB', nflTeam: 'DET', fantasyTeam: 'Team 1' }],
  };

  const updated = updateDraftSession(existing, metadata, [
    { pickNumber: 2, player: 'B. Robinson', position: 'RB', nflTeam: 'ATL', fantasyTeam: 'Team 4', isUserPick: false },
    { pickNumber: 2, player: 'B. Robinson', position: 'RB', nflTeam: 'ATL', fantasyTeam: 'Your Team', isUserPick: true },
  ], '2026-08-01T00:01:00.000Z');

  assert.deepEqual(updated.picks.map((pick) => pick.pickNumber), [1, 2]);
  assert.equal(updated.picks[1].isUserPick, true);
  assert.equal(updated.picks[1].fantasyTeam, 'Your Team');
});

test('initialed Picks-panel names match full authoritative names only with position and NFL team', () => {
  const metadata = { sport: 'f1', leagueId: 'league-a', teamId: '1', sessionKey: 'f1:league-a' };
  const existing = {
    ...metadata,
    numberedLedgerAuthoritative: true,
    picks: [{
      pickNumber: 1,
      player: 'Jahmyr Gibbs',
      position: 'RB',
      nflTeam: 'DET',
      fantasyTeam: 'Team 4',
    }],
  };

  const compatible = updateDraftSession(existing, metadata, [{
    pickNumber: 1,
    player: 'J. GIBBS',
    position: 'RB',
    nflTeam: 'DET',
    fantasyTeam: 'Your Team',
    isUserPick: true,
  }], '2026-08-01T00:01:00.000Z');
  assert.equal(compatible.picks.length, 1);
  assert.equal(compatible.picks[0].player, 'Jahmyr Gibbs');
  assert.equal(compatible.picks[0].isUserPick, true);

  const incompatible = updateDraftSession(existing, metadata, [{
    pickNumber: 1,
    player: 'J. GIBBS',
    position: 'WR',
    nflTeam: 'DET',
    fantasyTeam: 'Your Team',
  }], '2026-08-01T00:01:00.000Z');
  assert.equal(incompatible.picks.length, 2);
  assert.deepEqual(analyzeLedger(incompatible.picks).duplicatePickNumbers, [1]);
});

test('conflicting same-number secondary identities remain duplicates and fail closed', () => {
  const metadata = { sport: 'f1', leagueId: 'league-a', teamId: '1', sessionKey: 'f1:league-a' };
  const existing = {
    ...metadata,
    numberedLedgerAuthoritative: true,
    picks: [{ pickNumber: 1, player: 'J. Gibbs', position: 'RB', nflTeam: 'DET', fantasyTeam: 'Team 1' }],
  };

  const updated = updateDraftSession(existing, metadata, [
    { pickNumber: 2, player: 'B. Robinson', position: 'RB', nflTeam: 'ATL', fantasyTeam: 'Team 2' },
    { pickNumber: 2, player: 'P. Nacua', position: 'WR', nflTeam: 'LAR', fantasyTeam: 'Team 3' },
  ], '2026-08-01T00:01:00.000Z');

  assert.deepEqual(updated.picks.map((pick) => pick.pickNumber), [1, 2, 2]);
  assert.deepEqual(analyzeLedger(updated.picks).duplicatePickNumbers, [2]);
});

test('secondary conflicts cannot replace an authoritative pick and remain blocking duplicates', () => {
  const metadata = { sport: 'f1', leagueId: 'league-a', teamId: '1', sessionKey: 'f1:league-a' };
  const existing = {
    ...metadata,
    numberedLedgerAuthoritative: true,
    picks: [{ pickNumber: 1, player: 'Authoritative Player', fantasyTeam: 'Team 1' }],
  };

  const updated = updateDraftSession(existing, metadata, [
    { pickNumber: 1, player: 'Conflicting Panel Player', fantasyTeam: 'Team 9' },
    { pickNumber: 2, player: 'New Panel Player', position: 'RB', nflTeam: 'ATL', fantasyTeam: 'Team 2' },
  ], '2026-08-01T00:01:00.000Z');

  assert.deepEqual(updated.picks.map((pick) => pick.player), [
    'Authoritative Player',
    'Conflicting Panel Player',
    'New Panel Player',
  ]);
  assert.deepEqual(analyzeLedger(updated.picks).duplicatePickNumbers, [1]);
});

test('overlapping Picks-panel windows accumulate without duplicating their overlap', () => {
  const metadata = { sport: 'f1', leagueId: 'league-a', teamId: '1', sessionKey: 'f1:league-a' };
  const baseline = {
    ...metadata,
    numberedLedgerAuthoritative: true,
    picks: [{ pickNumber: 1, player: 'Player 1', fantasyTeam: 'Team 1' }],
  };
  const first = updateDraftSession(baseline, metadata, [
    { pickNumber: 2, player: 'Player 2', fantasyTeam: 'Team 2' },
    { pickNumber: 3, player: 'Player 3', fantasyTeam: 'Team 3' },
  ], '2026-08-01T00:01:00.000Z');
  const second = updateDraftSession(first, metadata, [
    { pickNumber: 3, player: 'Player 3', fantasyTeam: 'Team 3' },
    { pickNumber: 4, player: 'Player 4', fantasyTeam: 'Team 4' },
  ], '2026-08-01T00:02:00.000Z');

  assert.deepEqual(second.picks.map((pick) => pick.pickNumber), [1, 2, 3, 4]);
});

test('secondary capture cannot certify a pick that was missing from the saved ledger', () => {
  const metadata = { sport: 'f1', leagueId: 'league-a', teamId: '1', sessionKey: 'f1:league-a' };
  const existing = {
    ...metadata,
    numberedLedgerAuthoritative: true,
    authoritativeCaptureBlocked: false,
    picks: [
      { pickNumber: 1, player: 'Player 1' },
      { pickNumber: 3, player: 'Player 3' },
    ],
  };

  const updated = updateDraftSessionFromSecondaryObservations(
    existing,
    metadata,
    [{ pickNumber: 2, player: 'Panel Player 2' }],
    '2026-08-01T00:01:00.000Z',
  );

  assert.deepEqual(updated.picks.map((pick) => pick.pickNumber), [1, 2, 3]);
  assert.equal(updated.authoritativeCaptureBlocked, true);
});

test('filling a gap created by an earlier out-of-order panel append remains blocked', () => {
  const metadata = { sport: 'f1', leagueId: 'league-a', teamId: '1', sessionKey: 'f1:league-a' };
  const baseline = {
    ...metadata,
    numberedLedgerAuthoritative: true,
    authoritativeCaptureBlocked: false,
    picks: Array.from({ length: 5 }, (_unused, index) => ({
      pickNumber: index + 1,
      player: `Player ${index + 1}`,
    })),
  };
  const outOfOrder = updateDraftSessionFromSecondaryObservations(
    baseline,
    metadata,
    [{ pickNumber: 7, player: 'Player 7' }],
    '2026-08-01T00:01:00.000Z',
  );
  assert.deepEqual(analyzeLedger(outOfOrder.picks).missingPickNumbers, [6]);
  assert.equal(outOfOrder.authoritativeCaptureBlocked, false);

  const filled = updateDraftSessionFromSecondaryObservations(
    outOfOrder,
    metadata,
    [{ pickNumber: 6, player: 'Player 6' }],
    '2026-08-01T00:02:00.000Z',
  );
  assert.equal(analyzeLedger(filled.picks).isComplete, true);
  assert.equal(filled.authoritativeCaptureBlocked, true);
});

test('strictly next secondary pick after a complete baseline stays unblocked', () => {
  const metadata = { sport: 'f1', leagueId: 'league-a', teamId: '1', sessionKey: 'f1:league-a' };
  const existing = {
    ...metadata,
    numberedLedgerAuthoritative: true,
    authoritativeCaptureBlocked: false,
    picks: [
      { pickNumber: 1, player: 'Player 1' },
      { pickNumber: 2, player: 'Player 2' },
    ],
  };

  const updated = updateDraftSessionFromSecondaryObservations(
    existing,
    metadata,
    [{ pickNumber: 3, player: 'Player 3' }],
    '2026-08-01T00:01:00.000Z',
  );

  assert.deepEqual(updated.picks.map((pick) => pick.pickNumber), [1, 2, 3]);
  assert.equal(updated.authoritativeCaptureBlocked, false);
});

test('numbered panel-only capture persists observations but sets a durable blocker', () => {
  const metadata = { sport: 'f1', leagueId: 'league-a', teamId: '1', sessionKey: 'f1:league-a' };
  const session = updateDraftSessionFromSecondaryObservations(
    undefined,
    metadata,
    [{ pickNumber: 1, player: 'J. Gibbs', position: 'RB', nflTeam: 'DET', fantasyTeam: 'Team 1' }],
    '2026-08-01T00:01:00.000Z',
  );

  assert.equal(session.picks.length, 1);
  assert.equal(session.authoritativeCaptureBlocked, true);

  const laterPanelScan = updateDraftSessionFromSecondaryObservations(
    session,
    metadata,
    [{ pickNumber: 2, player: 'B. Robinson', position: 'RB', nflTeam: 'ATL', fantasyTeam: 'Team 2' }],
    '2026-08-01T00:02:00.000Z',
  );
  assert.equal(laterPanelScan.authoritativeCaptureBlocked, true);
});

test('panel-only capture preserves incompatible same-number observations as duplicates', () => {
  const metadata = { sport: 'f1', leagueId: 'league-a', teamId: '1', sessionKey: 'f1:league-a' };
  const first = updateDraftSessionFromSecondaryObservations(
    undefined,
    metadata,
    [{ pickNumber: 1, player: 'J. Gibbs', position: 'RB', nflTeam: 'DET', fantasyTeam: 'Team 1' }],
    '2026-08-01T00:01:00.000Z',
  );
  const conflicted = updateDraftSessionFromSecondaryObservations(
    first,
    metadata,
    [{ pickNumber: 1, player: 'P. Nacua', position: 'WR', nflTeam: 'LAR', fantasyTeam: 'Team 2' }],
    '2026-08-01T00:02:00.000Z',
  );

  assert.equal(conflicted.numberedLedgerAuthoritative, undefined);
  assert.equal(conflicted.authoritativeCaptureBlocked, true);
  assert.deepEqual(conflicted.picks.map((pick) => pick.player), ['J. Gibbs', 'P. Nacua']);
  assert.deepEqual(analyzeLedger(conflicted.picks).duplicatePickNumbers, [1]);
});

test('verified Round-by-Round scan can clear a panel-only capture blocker', () => {
  const metadata = { sport: 'f1', leagueId: 'league-a', teamId: '1', sessionKey: 'f1:league-a' };
  const panelOnly = updateDraftSessionFromSecondaryObservations(
    undefined,
    metadata,
    [{ pickNumber: 1, player: 'J. Gibbs', position: 'RB', nflTeam: 'DET', fantasyTeam: 'Team 1' }],
    '2026-08-01T00:01:00.000Z',
  );
  const verified = prepareAutomaticAuthoritativeUpdate(
    panelOnly,
    metadata,
    panelOnly.picks,
    [],
    '2026-08-01T00:02:00.000Z',
    { currentPickNumber: 2 },
  );

  assert.equal(verified.ok, true);
  assert.equal(verified.session.numberedLedgerAuthoritative, true);
  assert.equal(verified.session.authoritativeCaptureBlocked, false);
});

test('Round-by-Round can establish only the authoritative marker with otherwise identical state', () => {
  const metadata = { sport: 'f1', leagueId: 'league-a', teamId: '1', sessionKey: 'f1:league-a' };
  const panelOnly = updateDraftSessionFromSecondaryObservations(
    undefined,
    metadata,
    [{ pickNumber: 1, player: 'J. Gibbs', position: 'RB', nflTeam: 'DET', fantasyTeam: 'Team 1' }],
    '2026-08-01T00:01:00.000Z',
  );
  const result = prepareAutomaticAuthoritativeUpdate(
    panelOnly,
    metadata,
    panelOnly.picks,
    [],
    '2026-08-01T00:02:00.000Z',
    { currentPickNumber: null },
  );

  assert.equal(result.ok, true);
  assert.deepEqual(result.session.picks, panelOnly.picks);
  assert.equal(result.session.authoritativeCaptureBlocked, true);
  assert.equal(panelOnly.numberedLedgerAuthoritative, undefined);
  assert.equal(result.session.numberedLedgerAuthoritative, true);
});

test('repair establishes a baseline so later panel picks append without reblocking', () => {
  const metadata = { sport: 'f1', leagueId: 'league-a', teamId: '1', sessionKey: 'f1:league-a' };
  const panelOnly = updateDraftSessionFromSecondaryObservations(
    undefined,
    metadata,
    [{ pickNumber: 1, player: 'Incorrect observation', fantasyTeam: 'Team 1' }],
    '2026-08-01T00:01:00.000Z',
  );
  const repaired = repairDraftSession(
    panelOnly,
    metadata,
    [{ pickNumber: 1, player: 'J. Gibbs', position: 'RB', nflTeam: 'DET', fantasyTeam: 'Team 1' }],
    '2026-08-01T00:02:00.000Z',
  );

  assert.equal(repaired.ok, true);
  assert.equal(repaired.session.numberedLedgerAuthoritative, true);
  assert.equal(repaired.session.authoritativeCaptureBlocked, false);

  const laterPanelScan = updateDraftSessionFromSecondaryObservations(
    repaired.session,
    metadata,
    [{ pickNumber: 2, player: 'B. Robinson', position: 'RB', nflTeam: 'ATL', fantasyTeam: 'Team 2' }],
    '2026-08-01T00:03:00.000Z',
  );
  assert.equal(laterPanelScan.authoritativeCaptureBlocked, false);
  assert.deepEqual(laterPanelScan.picks.map((pick) => pick.pickNumber), [1, 2]);
});

test('safe authoritative scan clears a durable capture-integrity blocker', () => {
  const metadata = { sport: 'f1', leagueId: 'league-a', teamId: '1', sessionKey: 'f1:league-a' };
  const existing = {
    ...metadata,
    authoritativeCaptureBlocked: true,
    picks: [{ pickNumber: 1, player: 'Player 1' }],
  };

  const result = prepareAutomaticAuthoritativeUpdate(
    existing,
    metadata,
    [{ pickNumber: 1, player: 'Player 1' }],
    [],
    '2026-08-01T00:01:00.000Z',
    { currentPickNumber: 2 },
  );

  assert.equal(result.ok, true);
  assert.equal(result.session.authoritativeCaptureBlocked, false);
});

test('coherent scan without current-pick evidence preserves an existing capture blocker', () => {
  const metadata = { sport: 'f1', leagueId: 'league-a', teamId: '1', sessionKey: 'f1:league-a' };
  const picks = Array.from({ length: 158 }, (_unused, index) => ({
    pickNumber: index + 1,
    player: `Player ${index + 1}`,
  }));
  const existing = {
    ...metadata,
    authoritativeCaptureBlocked: true,
    picks,
  };

  const result = prepareAutomaticAuthoritativeUpdate(
    existing,
    metadata,
    picks,
    [],
    '2026-08-01T00:01:00.000Z',
    { currentPickNumber: null },
  );

  assert.equal(result.ok, true);
  assert.equal(result.session.authoritativeCaptureBlocked, true);
});

test('coherent scan without current-pick evidence preserves an absent capture state', () => {
  const metadata = { sport: 'f1', leagueId: 'league-a', teamId: '1', sessionKey: 'f1:league-a' };
  const existing = {
    ...metadata,
    picks: [{ pickNumber: 1, player: 'Player 1' }],
  };

  const result = prepareAutomaticAuthoritativeUpdate(
    existing,
    metadata,
    existing.picks,
    [],
    '2026-08-01T00:01:00.000Z',
    { currentPickNumber: null },
  );

  assert.equal(result.ok, true);
  assert.equal(Object.hasOwn(result.session, 'authoritativeCaptureBlocked'), false);
});

test('positive matching current-pick evidence clears an existing capture blocker', () => {
  const metadata = { sport: 'f1', leagueId: 'league-a', teamId: '1', sessionKey: 'f1:league-a' };
  const existing = {
    ...metadata,
    authoritativeCaptureBlocked: true,
    picks: [{ pickNumber: 1, player: 'Player 1' }],
  };

  const result = prepareAutomaticAuthoritativeUpdate(
    existing,
    metadata,
    existing.picks,
    [],
    '2026-08-01T00:01:00.000Z',
    { currentPickNumber: 2 },
  );

  assert.equal(result.ok, true);
  assert.equal(result.session.authoritativeCaptureBlocked, false);
});

test('explicit repair clears a durable capture-integrity blocker', () => {
  const metadata = { sport: 'f1', leagueId: 'league-a', teamId: '1', sessionKey: 'f1:league-a' };
  const existing = {
    ...metadata,
    authoritativeCaptureBlocked: true,
    picks: [{ pickNumber: 1, player: 'Old Player' }],
  };

  const result = repairDraftSession(
    existing,
    metadata,
    [{ pickNumber: 1, player: 'Correct Player' }],
    '2026-08-01T00:01:00.000Z',
  );

  assert.equal(result.ok, true);
  assert.equal(result.session.authoritativeCaptureBlocked, false);
});

test('automatic authoritative scan blocks parsed picks at or beyond Yahoo current pick', () => {
  const metadata = { sport: 'f1', leagueId: 'league-a', teamId: '1', sessionKey: 'f1:league-a' };
  const existing = { ...metadata, picks: [{ pickNumber: 1 }] };

  const result = prepareAutomaticAuthoritativeUpdate(
    existing,
    metadata,
    [{ pickNumber: 1 }, { pickNumber: 2 }],
    [],
    '2026-08-01T00:01:00.000Z',
    { currentPickNumber: 2 },
  );

  assert.equal(result.ok, false);
  assert.equal(result.reason, 'current-pick-mismatch');
  assert.equal(result.session, existing);
  assert.match(result.error, /ends at pick 2.*currently on pick 2/i);
});

test('current-pick mismatch can conservatively merge newly observed rows', () => {
  const metadata = { sport: 'f1', leagueId: 'league-a', teamId: '1', sessionKey: 'f1:league-a' };
  const existing = {
    ...metadata,
    picks: Array.from({ length: 158 }, (_unused, index) => ({
      pickNumber: index + 1,
      player: `Player ${index + 1}`,
    })),
  };
  const visible = Array.from({ length: 160 }, (_unused, index) => ({
    pickNumber: index + 1,
    player: `Player ${index + 1}`,
  }));
  const guarded = prepareAutomaticAuthoritativeUpdate(
    existing,
    metadata,
    visible,
    [],
    '2026-08-01T00:01:00.000Z',
    { currentPickNumber: 160 },
  );

  assert.equal(guarded.ok, false);
  assert.equal(guarded.reason, 'current-pick-mismatch');
  const merged = updateDraftSession(
    existing,
    metadata,
    visible.slice(158),
    '2026-08-01T00:01:00.000Z',
  );
  assert.equal(merged.picks.length, 160);
  assert.equal(merged.picks.at(-1).pickNumber, 160);
});

test('creates a draft session and timestamps newly observed picks', () => {
  const session = updateDraftSession(
    undefined,
    { sport: 'f1', leagueId: '12345678', teamId: '6', sessionKey: 'f1:12345678' },
    [{ pickNumber: 1, player: 'Ja’Marr Chase' }],
    '2026-08-01T00:00:00.000Z',
  );

  assert.deepEqual(session, {
    sport: 'f1',
    leagueId: '12345678',
    teamId: '6',
    sessionKey: 'f1:12345678',
    picks: [
      {
        pickNumber: 1,
        player: 'Ja’Marr Chase',
        recordedAt: '2026-08-01T00:00:00.000Z',
      },
    ],
    updatedAt: '2026-08-01T00:00:00.000Z',
  });
});

test('keeps the original timestamp when a later scan enriches a pick', () => {
  const existing = {
    sessionKey: 'f1:12345678',
    picks: [
      {
        pickNumber: 1,
        player: 'Ja’Marr Chase',
        recordedAt: '2026-08-01T00:00:00.000Z',
      },
    ],
  };

  const session = updateDraftSession(
    existing,
    { sport: 'f1', leagueId: '12345678', teamId: '6', sessionKey: 'f1:12345678' },
    [{ pickNumber: 1, player: 'Ja’Marr Chase', position: 'WR' }],
    '2026-08-01T00:01:00.000Z',
  );

  assert.equal(session.picks[0].recordedAt, '2026-08-01T00:00:00.000Z');
  assert.equal(session.picks[0].position, 'WR');
  assert.equal(session.updatedAt, '2026-08-01T00:01:00.000Z');
});

test('prefers matching Yahoo player keys and preserves unequal-key observations as duplicates', () => {
  const metadata = {
    sport: 'f1', leagueId: '12345678', teamId: '6', sessionKey: 'f1:12345678',
  };
  const existing = {
    ...metadata,
    numberedLedgerAuthoritative: true,
    picks: [{
      pickNumber: 1,
      player: 'B. Robinson Jr.',
      position: 'RB',
      nflTeam: 'WAS',
      fantasyTeam: 'Team 1',
      playerKey: '461.p.33536',
      recordedAt: '2026-08-01T00:00:00.000Z',
    }],
  };

  const sameKey = updateDraftSessionFromSecondaryObservations(
    existing,
    metadata,
    [{
      pickNumber: 1,
      player: 'Brian Robinson Jr.',
      position: 'RB',
      nflTeam: 'WAS',
      fantasyTeam: 'Team 1',
      playerKey: '461.p.33536',
    }],
    '2026-08-01T00:01:00.000Z',
  );
  assert.equal(sameKey.picks.length, 1);
  assert.equal(sameKey.picks[0].recordedAt, '2026-08-01T00:00:00.000Z');

  const unequalKey = updateDraftSessionFromSecondaryObservations(
    existing,
    metadata,
    [{
      pickNumber: 1,
      player: 'B. Robinson Jr.',
      position: 'RB',
      nflTeam: 'WAS',
      fantasyTeam: 'Team 1',
      playerKey: '461.p.99999',
    }],
    '2026-08-01T00:01:00.000Z',
  );
  assert.equal(unequalKey.picks.length, 2);
  assert.deepEqual(
    unequalKey.picks.map((pick) => pick.playerKey),
    ['461.p.33536', '461.p.99999'],
  );
});

test('authoritative rescans retain a saved Yahoo player key when the matching row omits it', () => {
  const metadata = {
    sport: 'f1', leagueId: '12345678', teamId: '6', sessionKey: 'f1:12345678',
  };
  const existing = {
    ...metadata,
    numberedLedgerAuthoritative: true,
    picks: [{
      pickNumber: 1,
      player: 'Brian Robinson Jr.',
      position: 'RB',
      nflTeam: 'WAS',
      fantasyTeam: 'Team 1',
      playerKey: '461.p.33536',
      recordedAt: '2026-08-01T00:00:00.000Z',
    }],
  };

  const withoutKey = updateDraftSessionFromAuthoritativeLedger(
    existing,
    metadata,
    [{
      pickNumber: 1,
      player: 'Brian Robinson Jr.',
      position: 'RB',
      nflTeam: 'WAS',
      fantasyTeam: 'Team 1',
    }],
    [],
    '2026-08-01T00:01:00.000Z',
  );

  assert.equal(withoutKey.picks[0].playerKey, '461.p.33536');
  assert.equal(withoutKey.picks[0].recordedAt, '2026-08-01T00:00:00.000Z');

  const withDifferentValidKey = updateDraftSessionFromAuthoritativeLedger(
    existing,
    metadata,
    [{
      pickNumber: 1,
      player: 'Brian Robinson Jr.',
      position: 'RB',
      nflTeam: 'WAS',
      fantasyTeam: 'Team 1',
      playerKey: '461.p.99999',
    }],
    [],
    '2026-08-01T00:01:00.000Z',
  );

  assert.equal(withDifferentValidKey.picks[0].playerKey, '461.p.99999');
});

test('authoritative scan preserves duplicate rows and gaps in server-bound picks until repair', () => {
  const metadata = { sport: 'f1', leagueId: '12345678', teamId: '6', sessionKey: 'f1:12345678' };
  const session = updateDraftSessionFromAuthoritativeLedger(
    {
      ...metadata,
      picks: [
        { pickNumber: 3, player: 'Stale numbered pick' },
        { player: 'C. Olave', fantasyTeam: 'Team 5', position: 'WR' },
      ],
    },
    metadata,
    [
      { pickNumber: 1, player: 'J. Chase', fantasyTeam: 'Team 1' },
      { pickNumber: 2, player: 'B. Robinson', fantasyTeam: 'Team 2', position: 'RB', nflTeam: 'ATL' },
      { pickNumber: 2, player: 'Duplicate row', fantasyTeam: 'Team 9' },
      { pickNumber: 4, player: 'J. Jefferson', fantasyTeam: 'Team 4' },
    ],
    [
      { pickNumber: 99, player: 'Non-authoritative numbered row', fantasyTeam: 'Team 10' },
      { player: 'B. Robinson', fantasyTeam: 'Team 2', position: 'RB', nflTeam: 'ATL' },
      { player: 'C. Olave', fantasyTeam: 'Team 5', position: 'WR', nflTeam: 'NO' },
    ],
    '2026-08-01T00:02:00.000Z',
  );

  const afterOrdinaryScan = updateDraftSession(
    session,
    metadata,
    [
      { pickNumber: 2, player: 'Collapsed-looking observation', fantasyTeam: 'Team 2' },
      { pickNumber: 5, player: 'New live pick', fantasyTeam: 'Team 5' },
    ],
    '2026-08-01T00:02:30.000Z',
  );
  const context = sessionToAgentContext(afterOrdinaryScan, '2026-08-01T00:02:31.000Z');
  assert.deepEqual(context.picks.map((pick) => pick.pickNumber), [1, 2, 2, 2, 4, 5, undefined]);
  assert.equal(context.picks.at(-1).player, 'C. Olave');
  const blockedHealth = analyzeLedger(context.picks);
  assert.deepEqual(blockedHealth.missingPickNumbers, [3]);
  assert.deepEqual(blockedHealth.duplicatePickNumbers, [2]);
  assert.equal(blockedHealth.unnumberedPicks.length, 1);

  const repaired = repairDraftSession(afterOrdinaryScan, metadata, [
    { pickNumber: 1, player: 'J. Chase' },
    { pickNumber: 2, player: 'B. Robinson' },
    { pickNumber: 3, player: 'C. Olave' },
    { pickNumber: 4, player: 'J. Jefferson' },
    { pickNumber: 5, player: 'New live pick' },
  ], '2026-08-01T00:03:00.000Z');
  assert.equal(repaired.ok, true);
  assert.equal(analyzeLedger(repaired.session.picks).isComplete, true);
});

test('full repair replaces stale picks with a complete authoritative ledger', () => {
  const existing = {
    sessionKey: 'f1:12345678',
    picks: [
      { pickNumber: 1, player: 'Old first pick' },
      { pickNumber: 1, player: 'Duplicate first pick' },
      { player: 'Unnumbered banner pick' },
    ],
  };
  const metadata = { sport: 'f1', leagueId: '12345678', teamId: '6', sessionKey: 'f1:12345678' };
  const result = repairDraftSession(existing, metadata, [
    { pickNumber: 1, player: 'J. Chase' },
    { pickNumber: 2, player: 'B. Robinson' },
  ], '2026-08-01T00:02:00.000Z');

  assert.equal(result.ok, true);
  assert.deepEqual(result.session.picks.map((pick) => pick.pickNumber), [1, 2]);
});

test('full repair refuses a partial ledger and preserves saved picks', () => {
  const existing = { sessionKey: 'f1:12345678', picks: [{ pickNumber: 1, player: 'Saved pick' }] };
  const metadata = { sport: 'f1', leagueId: '12345678', teamId: '6', sessionKey: 'f1:12345678' };
  const result = repairDraftSession(existing, metadata, [
    { pickNumber: 1, player: 'J. Chase' },
    { pickNumber: 3, player: 'J. Jefferson' },
  ], '2026-08-01T00:02:00.000Z');

  assert.equal(result.ok, false);
  assert.equal(result.session, existing);
  assert.deepEqual(result.health.missingPickNumbers, [2]);
});

test('full repair can stage removal of a phantom saved pick', () => {
  const existing = {
    sessionKey: 'f1:12345678',
    picks: [
      { pickNumber: 1, player: 'Saved first pick' },
      { pickNumber: 2, player: 'Saved second pick' },
      { pickNumber: 3, player: 'Saved third pick' },
    ],
    updatedAt: '2026-08-01T00:01:00.000Z',
  };
  const metadata = { sport: 'f1', leagueId: '12345678', teamId: '6', sessionKey: 'f1:12345678' };
  const result = repairDraftSession(existing, metadata, [
    { pickNumber: 1, player: 'Visible first pick' },
    { pickNumber: 2, player: 'Visible second pick' },
  ], '2026-08-01T00:02:00.000Z');

  assert.equal(result.ok, true);
  assert.deepEqual(result.session.picks.map((pick) => pick.pickNumber), [1, 2]);
  assert.deepEqual(existing.picks.map((pick) => pick.player), [
    'Saved first pick',
    'Saved second pick',
    'Saved third pick',
  ]);
});

test('failed repair sync leaves the exact saved session unchanged and does not persist', async () => {
  const existing = {
    sessionKey: 'f1:12345678',
    picks: [
      { pickNumber: 1, player: 'Saved first pick' },
      { pickNumber: 2, player: 'Phantom pick' },
    ],
  };
  const metadata = { sport: 'f1', leagueId: '12345678', teamId: '6', sessionKey: 'f1:12345678' };
  let persistCalls = 0;

  const result = await commitDraftRepair(
    existing,
    metadata,
    [{ pickNumber: 1, player: 'Saved first pick' }],
    '2026-08-01T00:02:00.000Z',
    async () => { throw new Error('server rejected repair'); },
    async () => { persistCalls += 1; },
    { currentPickNumber: 2 },
  );

  assert.equal(result.ok, false);
  assert.equal(result.session, existing);
  assert.equal(persistCalls, 0);
  assert.match(result.error, /server rejected repair/);
  assert.deepEqual(existing.picks.map((pick) => pick.player), ['Saved first pick', 'Phantom pick']);
});

test('successful repair sync completes before local persistence', async () => {
  const existing = { sessionKey: 'f1:12345678', picks: [{ pickNumber: 2, player: 'Phantom pick' }] };
  const metadata = { sport: 'f1', leagueId: '12345678', teamId: '6', sessionKey: 'f1:12345678' };
  const operations = [];

  const result = await commitDraftRepair(
    existing,
    metadata,
    [{ pickNumber: 1, player: 'Authoritative pick' }],
    '2026-08-01T00:02:00.000Z',
    async (session) => { operations.push(`sync:${session.picks.length}`); },
    async (session) => { operations.push(`persist:${session.picks.length}`); },
    { currentPickNumber: 2 },
  );

  assert.equal(result.ok, true);
  assert.deepEqual(operations, ['sync:1', 'persist:1']);
});

test('downward repair without live current-pick evidence stops before server sync', async () => {
  const existing = {
    sessionKey: 'f1:12345678',
    picks: [{ pickNumber: 1 }, { pickNumber: 2 }, { pickNumber: 3 }],
  };
  const metadata = { sport: 'f1', leagueId: '12345678', teamId: '6', sessionKey: 'f1:12345678' };
  let syncCalls = 0;
  let persistCalls = 0;

  const result = await commitDraftRepair(
    existing,
    metadata,
    [{ pickNumber: 1 }, { pickNumber: 2 }],
    '2026-08-01T00:02:00.000Z',
    async () => { syncCalls += 1; },
    async () => { persistCalls += 1; },
    { currentPickNumber: null },
  );

  assert.equal(result.ok, false);
  assert.equal(result.session, existing);
  assert.equal(syncCalls, 0);
  assert.equal(persistCalls, 0);
  assert.match(result.error, /live current pick is unavailable/);
});

test('durable accepted repair survives reload and reconciles before another tab scans', async () => {
  let pending = null;
  let storedSession = { sessionKey: 'f1:league-a', picks: [{ pickNumber: 1 }, { pickNumber: 2 }] };
  const repaired = { sessionKey: 'f1:league-a', picks: [{ pickNumber: 1 }] };
  const operations = [];
  let persistAttempts = 0;
  const dependencies = {
    readPending: async () => pending,
    writePending: async (record) => { pending = record; },
    clearPending: async () => { pending = null; },
    syncRepair: async (session) => operations.push(`repair-sync:${session.picks.length}`),
    persistSession: async (session) => {
      persistAttempts += 1;
      operations.push(`persist:${persistAttempts}:${session.picks.length}`);
      if (persistAttempts === 1) throw new Error('browser write failed');
      storedSession = session;
    },
  };

  const firstTab = createDurableRepairCoordinator(dependencies);
  await firstTab.begin('f1:league-a', repaired);
  const firstResult = await firstTab.reconcile();
  assert.equal(firstResult.ok, false);
  assert.equal(pending.state, 'accepted');
  assert.deepEqual(storedSession.picks.map((pick) => pick.pickNumber), [1, 2]);

  const reloadedOtherTab = createDurableRepairCoordinator(dependencies);
  const secondResult = await reloadedOtherTab.runAfterReconcile(async () => operations.push('auto-scan'));
  assert.equal(secondResult.ok, true);
  assert.equal(pending, null);
  assert.equal(storedSession, repaired);
  assert.deepEqual(operations, [
    'repair-sync:1',
    'persist:1:1',
    'persist:2:1',
    'auto-scan',
  ]);
});

test('successful durable repair advances the proven synced revision used by reset', async () => {
  let pending = null;
  let storedSession = null;
  const repaired = {
    ...RESET_METADATA,
    updatedAt: '2026-09-01T23:20:00.000Z',
    lastSyncedAt: '2026-09-01T23:15:00.000Z',
    picks: [{ pickNumber: 1 }],
  };
  const coordinator = createDurableRepairCoordinator({
    readPending: async () => pending,
    writePending: async (record) => { pending = record; },
    clearPending: async () => { pending = null; },
    syncRepair: async () => undefined,
    persistSession: async (session) => { storedSession = session; },
  });

  await coordinator.begin(RESET_METADATA.sessionKey, repaired);
  const result = await coordinator.reconcile();

  assert.equal(result.ok, true);
  assert.equal(storedSession.lastSyncedAt, repaired.updatedAt);
});

test('repair intent written after reset cleanup is discarded without stale sync or deadlock', async () => {
  let pending = null;
  let syncCalls = 0;
  let persistCalls = 0;
  const staleRepair = {
    ...RESET_METADATA,
    updatedAt: '2026-09-01T23:15:00.000Z',
    picks: [{ pickNumber: 1 }],
  };
  const coordinator = createDurableRepairCoordinator({
    readPending: async () => pending,
    writePending: async (record) => { pending = record; },
    clearPending: async () => { pending = null; },
    isSessionReset: async (session) => session.updatedAt === staleRepair.updatedAt,
    syncRepair: async () => { syncCalls += 1; },
    persistSession: async () => { persistCalls += 1; },
  });

  await coordinator.begin(RESET_METADATA.sessionKey, staleRepair);
  const result = await coordinator.reconcile();

  assert.equal(result.ok, true);
  assert.equal(result.discardedByReset, true);
  assert.equal(pending, null);
  assert.equal(syncCalls, 0);
  assert.equal(persistCalls, 0);
});

test('durable repair intent suppresses stale work and retries marked sync after reload', async () => {
  let pending = null;
  const operations = [];
  const dependencies = {
    readPending: async () => pending,
    writePending: async (record) => { pending = record; },
    clearPending: async () => { pending = null; },
    syncRepair: async () => operations.push('repair-sync'),
    persistSession: async () => operations.push('persist'),
  };
  const firstTab = createDurableRepairCoordinator(dependencies);
  await firstTab.begin('f1:league-a', { sessionKey: 'f1:league-a', picks: [{ pickNumber: 1 }] });

  const otherTab = createDurableRepairCoordinator(dependencies);
  assert.equal(await otherTab.hasPending(), true);
  const result = await otherTab.runAfterReconcile(async () => operations.push('normal-sync'));
  assert.equal(result.ok, true);
  assert.deepEqual(operations, ['repair-sync', 'persist', 'normal-sync']);
});

test('reload safely retries repair when recording server acceptance failed', async () => {
  let pending = null;
  let pendingWrites = 0;
  const operations = [];
  const dependencies = {
    readPending: async () => pending,
    writePending: async (record) => {
      pendingWrites += 1;
      if (pendingWrites === 2) throw new Error('accepted marker write failed');
      pending = record;
    },
    clearPending: async () => { pending = null; },
    syncRepair: async () => operations.push('repair-sync'),
    persistSession: async () => operations.push('persist'),
  };
  const firstTab = createDurableRepairCoordinator(dependencies);
  await firstTab.begin('f1:league-a', { sessionKey: 'f1:league-a', picks: [{ pickNumber: 1 }] });

  const firstResult = await firstTab.reconcile();
  assert.equal(firstResult.ok, false);
  assert.equal(pending.state, 'intent');
  assert.deepEqual(operations, ['repair-sync']);

  const reloadedTab = createDurableRepairCoordinator(dependencies);
  const secondResult = await reloadedTab.reconcile();
  assert.equal(secondResult.ok, true);
  assert.equal(pending, null);
  assert.deepEqual(operations, ['repair-sync', 'repair-sync', 'persist']);
});
