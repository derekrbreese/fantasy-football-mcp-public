const test = require('node:test');
const assert = require('node:assert/strict');

const { updateDraftSession } = require('../session-store.js');

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
