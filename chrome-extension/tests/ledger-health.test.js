const test = require('node:test');
const assert = require('node:assert/strict');

const {
  analyzeLedger,
  evaluateAuthoritativeLedgerScan,
  formatLedgerIssues,
  formatRepairFailure,
  mergeVisibleLedgerHealth,
  validateStableCurrentPick,
  validateDownwardRepairEvidence,
  validateLedgerAgainstCurrentPick,
} = require('../ledger-health.js');

function numberedSnapshots(count) {
  return Array.from({ length: count }, (_unused, index) => ({
    pickText: String(index + 1),
  }));
}

function parsedPrefix(completedCount, apparentCount) {
  return Array.from({ length: apparentCount }, (_unused, index) => (
    index < completedCount ? { pickNumber: index + 1 } : null
  ));
}

test('ignores aligned pre-rendered future rows at Yahoo current pick and beyond', () => {
  const result = evaluateAuthoritativeLedgerScan(
    {
      ok: true,
      tableCount: 1,
      apparentRowCount: 162,
      snapshots: numberedSnapshots(162),
    },
    parsedPrefix(158, 162),
    159,
  );

  assert.equal(result.error, null);
  assert.equal(result.authoritativePicks.length, 158);
  assert.equal(result.health.highestPickNumber, 158);
  assert.deepEqual(result.unparsedCompletedPickNumbers, []);
  assert.equal(result.unparsedStructuralRowCount, 0);
  assert.equal(result.ignoredFutureRowCount, 4);
});

test('blocks the same unparsed rows when Yahoo says they are completed', () => {
  const result = evaluateAuthoritativeLedgerScan(
    {
      ok: true,
      tableCount: 1,
      apparentRowCount: 162,
      snapshots: numberedSnapshots(162),
    },
    parsedPrefix(158, 162),
    163,
  );

  assert.equal(result.authoritativePicks, null);
  assert.deepEqual(result.unparsedCompletedPickNumbers, [159, 160, 161, 162]);
  assert.equal(result.unparsedStructuralRowCount, 0);
  assert.equal(result.ignoredFutureRowCount, 0);
  assert.match(result.error, /159, 160, 161, 162/);
});

test('blocks unparsed numbered rows when Yahoo current pick is unavailable', () => {
  const result = evaluateAuthoritativeLedgerScan(
    {
      ok: true,
      tableCount: 1,
      apparentRowCount: 162,
      snapshots: numberedSnapshots(162),
    },
    parsedPrefix(158, 162),
    null,
  );

  assert.equal(result.authoritativePicks, null);
  assert.deepEqual(result.unparsedCompletedPickNumbers, [159, 160, 161, 162]);
  assert.equal(result.ignoredFutureRowCount, 0);
});

test('never ignores an unnumbered malformed row alongside numbered future rows', () => {
  const snapshots = [
    { pickText: '1' },
    { cellShape: 'td:4' },
    { pickText: '3' },
    { pickText: '4' },
  ];
  const result = evaluateAuthoritativeLedgerScan(
    { ok: true, tableCount: 1, apparentRowCount: 4, snapshots },
    [{ pickNumber: 1 }, null, null, null],
    2,
  );

  assert.equal(result.authoritativePicks, null);
  assert.deepEqual(result.unparsedCompletedPickNumbers, []);
  assert.equal(result.unparsedStructuralRowCount, 1);
  assert.equal(result.ignoredFutureRowCount, 2);
  assert.match(result.error, /1 row had no safe positive pick number/);
});

test('future-row exemption requires normal shape and digits-only positive pick text', () => {
  const result = evaluateAuthoritativeLedgerScan(
    {
      ok: true,
      tableCount: 1,
      apparentRowCount: 3,
      snapshots: [
        { pickText: '1' },
        { pickText: '2', cellShape: 'role-cell:3' },
        { pickText: '3rd' },
      ],
    },
    [{ pickNumber: 1 }, null, null],
    2,
  );

  assert.equal(result.authoritativePicks, null);
  assert.deepEqual(result.unparsedCompletedPickNumbers, []);
  assert.equal(result.unparsedStructuralRowCount, 2);
  assert.equal(result.ignoredFutureRowCount, 0);
});

test('retains parsed rows at or beyond current pick so current-pick validation fails closed', () => {
  const picks = [{ pickNumber: 1 }, { pickNumber: 2 }];
  const result = evaluateAuthoritativeLedgerScan(
    {
      ok: true,
      tableCount: 1,
      apparentRowCount: 2,
      snapshots: numberedSnapshots(2),
    },
    picks,
    2,
  );

  assert.equal(result.authoritativePicks, picks);
  assert.equal(result.ignoredFutureRowCount, 0);
  assert.equal(validateLedgerAgainstCurrentPick(result.health, 2).ok, false);
});

test('requires a stable Yahoo current pick across an authoritative DOM scan', () => {
  assert.deepEqual(validateStableCurrentPick(159, 159), {
    ok: true,
    currentPickNumber: 159,
  });
  assert.deepEqual(validateStableCurrentPick(null, null), {
    ok: true,
    currentPickNumber: null,
  });
  assert.deepEqual(validateStableCurrentPick(159, 160), {
    ok: false,
    currentPickNumber: null,
    error: 'Yahoo’s current pick changed from 159 to 160 while the Round-by-Round ledger was scanned. Saved picks were not changed.',
  });
});

test('keeps a fully parsed completed ledger unchanged', () => {
  const picks = [{ pickNumber: 1 }, { pickNumber: 2 }, { pickNumber: 3 }];
  const result = evaluateAuthoritativeLedgerScan(
    {
      ok: true,
      tableCount: 1,
      apparentRowCount: 3,
      snapshots: numberedSnapshots(3),
    },
    picks,
    4,
  );

  assert.equal(result.authoritativePicks, picks);
  assert.equal(result.health.highestPickNumber, 3);
  assert.equal(result.ignoredFutureRowCount, 0);
});

test('reports exact missing and duplicate pick numbers', () => {
  const health = analyzeLedger([
    { pickNumber: 1, player: 'J. Chase' },
    { pickNumber: 2, player: 'B. Robinson' },
    { pickNumber: 2, player: 'Duplicate row' },
    { pickNumber: 4, player: 'J. Jefferson' },
  ]);

  assert.deepEqual(health.missingPickNumbers, [3]);
  assert.deepEqual(health.duplicatePickNumbers, [2]);
  assert.equal(health.isComplete, false);
  assert.equal(formatLedgerIssues(health), 'Missing picks: 3. Duplicate picks: 2.');
});

test('reports exact sanitized unnumbered pick details and count', () => {
  const health = analyzeLedger([
    { pickNumber: 1, player: 'J. Chase' },
    { player: 'C. Olave', position: 'WR', nflTeam: 'NO', fantasyTeam: 'Team 5' },
    { player: '<img src=x onerror=alert(1)>', fantasyTeam: 'Your Team' },
  ]);

  assert.equal(health.unnumberedPicks.length, 2);
  assert.deepEqual(health.unnumberedPicks[0], {
    player: 'C. Olave',
    position: 'WR',
    nflTeam: 'NO',
    fantasyTeam: 'Team 5',
  });
  assert.equal(
    formatLedgerIssues(health),
    'Unnumbered picks (2): C. Olave (NO · WR) — Team 5; <img src=x onerror=alert(1)> — Your Team.',
  );
});

test('marks only a contiguous uniquely numbered ledger as complete', () => {
  assert.equal(analyzeLedger([{ pickNumber: 1 }, { pickNumber: 2 }]).isComplete, true);
  assert.equal(analyzeLedger([{ pickNumber: 2 }]).isComplete, false);
  assert.equal(analyzeLedger([]).isComplete, false);
});

test('requires ledger maximum to immediately precede an available current pick', () => {
  const health = analyzeLedger([{ pickNumber: 1 }, { pickNumber: 2 }]);

  assert.deepEqual(validateLedgerAgainstCurrentPick(health, 3), { ok: true });
  assert.deepEqual(validateLedgerAgainstCurrentPick(health, null), { ok: true });
  assert.deepEqual(validateLedgerAgainstCurrentPick(health, 4), {
    ok: false,
    error: 'Visible ledger ends at pick 2, but Yahoo is currently on pick 4. Saved picks were not changed.',
  });
});

test('requires live current-pick evidence only when repair lowers the saved maximum', () => {
  const saved = analyzeLedger([{ pickNumber: 1 }, { pickNumber: 2 }, { pickNumber: 3 }]);
  const lower = analyzeLedger([{ pickNumber: 1 }, { pickNumber: 2 }]);
  const same = analyzeLedger([{ pickNumber: 1 }, { pickNumber: 2 }, { pickNumber: 3 }]);

  assert.deepEqual(validateDownwardRepairEvidence(saved, lower, 3), { ok: true });
  assert.deepEqual(validateDownwardRepairEvidence(saved, same, null), { ok: true });
  assert.deepEqual(validateDownwardRepairEvidence(saved, lower, null), {
    ok: false,
    error: 'Repair would lower the saved ledger from pick 3 to pick 2, but Yahoo’s live current pick is unavailable. Saved picks were not changed.',
  });
});

test('popup repair failure includes both the operation error and exact health issues', () => {
  const health = analyzeLedger([
    { pickNumber: 1 },
    { pickNumber: 1 },
    { pickNumber: 3 },
    { player: 'C. Olave', fantasyTeam: 'Team 5' },
  ]);

  assert.equal(
    formatRepairFailure('Server rejected repair.', health),
    'Server rejected repair. Missing picks: 2. Duplicate picks: 1. Unnumbered picks (1): C. Olave — Team 5.',
  );
});

test('popup merges raw authoritative gaps and duplicates with saved unnumbered details', () => {
  const authoritative = analyzeLedger([
    { pickNumber: 1 },
    { pickNumber: 2 },
    { pickNumber: 2 },
    { pickNumber: 4 },
  ]);
  const saved = analyzeLedger([
    { pickNumber: 1 },
    { pickNumber: 2 },
    { pickNumber: 4 },
    { player: 'C. Olave', position: 'WR', nflTeam: 'NO', fantasyTeam: 'Team 5' },
  ]);
  const merged = mergeVisibleLedgerHealth(authoritative, saved);

  assert.deepEqual(merged.missingPickNumbers, [3]);
  assert.deepEqual(merged.duplicatePickNumbers, [2]);
  assert.equal(merged.unnumberedPicks.length, 1);
  assert.equal(
    formatLedgerIssues(merged),
    'Missing picks: 3. Duplicate picks: 2. Unnumbered picks (1): C. Olave (NO · WR) — Team 5.',
  );
});
