const test = require('node:test');
const assert = require('node:assert/strict');

const { picksToCsv, picksToJson } = require('../export.js');

const picks = [
  {
    pickNumber: 1,
    roundNumber: 1,
    roundPick: 1,
    player: 'Ja’Marr Chase',
    position: 'WR',
    nflTeam: 'CIN',
    fantasyTeam: 'Winners, Inc.',
    isUserPick: true,
    recordedAt: '2026-08-01T00:00:00.000Z',
  },
];

test('exports picks as spreadsheet-safe CSV', () => {
  assert.equal(
    picksToCsv(picks),
    [
      'Overall Pick,Round,Round Pick,Player,Position,NFL Team,Fantasy Team,Your Pick,Recorded At',
      '1,1,1,Ja’Marr Chase,WR,CIN,"Winners, Inc.",true,2026-08-01T00:00:00.000Z',
    ].join('\r\n'),
  );
});

test('neutralizes spreadsheet formulas in CSV fields', () => {
  const csv = picksToCsv([{ player: '=HYPERLINK("bad")', fantasyTeam: '+cmd' }]);
  assert.match(csv, /"'=HYPERLINK\(""bad""\)"/);
  assert.match(csv, /'\+cmd/);
});

test('exports readable JSON', () => {
  assert.equal(picksToJson(picks), `${JSON.stringify(picks, null, 2)}\n`);
});
