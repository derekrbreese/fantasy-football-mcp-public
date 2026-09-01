const test = require('node:test');
const assert = require('node:assert/strict');
const { sessionToAgentContext } = require('../agent-context.js');
const { analyzeLedger } = require('../ledger-health.js');

const {
  commitDraftRepair,
  createDurableRepairCoordinator,
  prepareAutomaticAuthoritativeUpdate,
  repairDraftSession,
  updateDraftSessionFromAuthoritativeLedger,
  updateDraftSession,
} = require('../session-store.js');

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
  assert.deepEqual(context.picks.map((pick) => pick.pickNumber), [1, 2, 2, 4, 5, undefined]);
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
