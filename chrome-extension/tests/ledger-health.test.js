const test = require('node:test');
const assert = require('node:assert/strict');

const {
  analyzeLedger,
  formatLedgerIssues,
  formatRepairFailure,
  mergeVisibleLedgerHealth,
  validateDownwardRepairEvidence,
  validateLedgerAgainstCurrentPick,
} = require('../ledger-health.js');

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
