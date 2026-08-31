const test = require('node:test');
const assert = require('node:assert/strict');

const {
  buildPickKey,
  parseDraftUrl,
  parseLiveDraftSnapshot,
  parsePickSnapshot,
  parseRoundByRoundSnapshot,
  upsertPicks,
} = require('../draft-parser.js');

test('parses a Yahoo draft URL without retaining its auth query string', () => {
  assert.deepEqual(
    parseDraftUrl(
      'https://football.fantasysports.yahoo.com/draftclient/f1/12345678/6?auth=secret',
    ),
    {
      sport: 'f1',
      leagueId: '12345678',
      teamId: '6',
      sessionKey: 'f1:12345678',
    },
  );
});

test('reads a pick from explicit labels and normalizes its values', () => {
  const pick = parsePickSnapshot({
    text: 'Round 1 pick information',
    labels: {
      pickNumber: '1',
      roundNumber: '1',
      roundPick: '1',
      player: '  Ja’Marr   Chase ',
      position: 'wr',
      nflTeam: 'cin',
      fantasyTeam: ' Sunday Winners ',
    },
  });

  assert.deepEqual(pick, {
    pickNumber: 1,
    roundNumber: 1,
    roundPick: 1,
    player: 'Ja’Marr Chase',
    position: 'WR',
    nflTeam: 'CIN',
    fantasyTeam: 'Sunday Winners',
  });
});

test('parses a compact Yahoo-style multiline draft row', () => {
  const pick = parsePickSnapshot({
    text: "7\nBijan Robinson\nATL - RB\nSunday Winners",
  });

  assert.deepEqual(pick, {
    pickNumber: 7,
    player: 'Bijan Robinson',
    position: 'RB',
    nflTeam: 'ATL',
    fantasyTeam: 'Sunday Winners',
  });
});

test('parses a sentence-style pick announcement', () => {
  const pick = parsePickSnapshot({
    text: 'Pick 15: CeeDee Lamb (DAL - WR) drafted by The Champions',
  });

  assert.deepEqual(pick, {
    pickNumber: 15,
    player: 'CeeDee Lamb',
    position: 'WR',
    nflTeam: 'DAL',
    fantasyTeam: 'The Champions',
  });
});

test('distinguishes a round pick from the explicitly stated overall pick', () => {
  const pick = parsePickSnapshot({
    text: 'Round 2, Pick 3 (15 overall): CeeDee Lamb (DAL - WR) drafted by The Champions',
  });

  assert.equal(pick.pickNumber, 15);
  assert.equal(pick.roundNumber, 2);
  assert.equal(pick.roundPick, 3);
});

test('parses a compact single-line result row', () => {
  const pick = parsePickSnapshot({
    text: '7. (7) Bijan Robinson (ATL - RB) Sunday Winners',
  });

  assert.deepEqual(pick, {
    pickNumber: 7,
    player: 'Bijan Robinson',
    position: 'RB',
    nflTeam: 'ATL',
    fantasyTeam: 'Sunday Winners',
  });
});

test('parses a Yahoo Round by Round results row', () => {
  assert.deepEqual(
    parseRoundByRoundSnapshot({
      roundText: 'ROUND 2',
      pickText: '19',
      playerText: 'S. Barkley\nRB\nPhi\nBye 10',
      fantasyTeamText: 'Your Team',
    }),
    {
      pickNumber: 19,
      roundNumber: 2,
      player: 'S. Barkley',
      position: 'RB',
      nflTeam: 'PHI',
      fantasyTeam: 'Your Team',
      isUserPick: true,
    },
  );
});

test('ignores Yahoo injury markers in Round by Round player details', () => {
  assert.deepEqual(
    parseRoundByRoundSnapshot({
      roundText: 'ROUND 3',
      pickText: '28',
      playerText: 'B. Hall\nQ\nRB\nNYJ\nBye 13',
      fantasyTeamText: 'Team 4',
    }),
    {
      pickNumber: 28,
      roundNumber: 3,
      player: 'B. Hall',
      position: 'RB',
      nflTeam: 'NYJ',
      fantasyTeam: 'Team 4',
      isUserPick: false,
    },
  );
});

test('rejects malformed Round by Round rows', () => {
  assert.equal(parseRoundByRoundSnapshot({ pickText: 'Pick', playerText: 'Player', fantasyTeamText: 'Team' }), null);
  assert.equal(parseRoundByRoundSnapshot({ pickText: '29', playerText: 'Loading', fantasyTeamText: 'Team 5' }), null);
});

test('parses Yahoo current-pick and last-pick banners', () => {
  assert.deepEqual(
    parseLiveDraftSnapshot({
      statusText: 'YOUR TURN\n• ROUND 2, PICK 19',
      lastPickText: 'Last:\nD. LONDON\n(WR · ATL)\nTeam 7',
    }),
    {
      pickNumber: 18,
      player: 'D. LONDON',
      position: 'WR',
      nflTeam: 'ATL',
      fantasyTeam: 'Team 7',
    },
  );
});

test('records the last player without guessing a pick number while paused', () => {
  assert.deepEqual(
    parseLiveDraftSnapshot({
      statusText: 'DRAFT PAUSED',
      lastPickText: 'Last: C. Olave (WR · NO) Team 5',
    }),
    {
      player: 'C. Olave',
      position: 'WR',
      nflTeam: 'NO',
      fantasyTeam: 'Team 5',
    },
  );
});

test('rejects unrelated page text and incomplete rows', () => {
  assert.equal(parsePickSnapshot({ text: 'Players Queue Chat Settings' }), null);
  assert.equal(parsePickSnapshot({ text: '12\nDraft results are loading' }), null);
});

test('uses the overall pick as the stable key when it is available', () => {
  assert.equal(
    buildPickKey('f1:12345678', {
      pickNumber: 12,
      player: 'Amon-Ra St. Brown',
      fantasyTeam: 'Team One',
    }),
    'f1:12345678:pick:12',
  );
});

test('replaces an unnumbered live observation when its pick number becomes available', () => {
  const existing = [{ player: 'C. Olave', fantasyTeam: 'Team 5' }];
  const incoming = [{ pickNumber: 18, player: 'C. Olave', fantasyTeam: 'Team 5' }];

  assert.deepEqual(upsertPicks('f1:12345678', existing, incoming), incoming);
});

test('collapses an existing numbered and unnumbered duplicate during migration', () => {
  const existing = [
    { pickNumber: 29, player: 'C. Olave', fantasyTeam: 'Team 5', roundNumber: 3 },
    { player: 'C. OLAVE', fantasyTeam: 'Team 5', position: 'WR', nflTeam: 'NO' },
  ];
  const observed = [
    { pickNumber: 29, player: 'C. Olave', fantasyTeam: 'Team 5', roundNumber: 3, position: 'WR', nflTeam: 'NO' },
  ];

  assert.deepEqual(upsertPicks('f1:12345678', existing, observed), observed);
});

test('does not duplicate a numbered ledger pick with an unnumbered live banner', () => {
  const ledger = [{ pickNumber: 29, player: 'C. Olave', fantasyTeam: 'Team 5', roundNumber: 3 }];
  const live = [{ player: 'C. Olave', fantasyTeam: 'Team 5', position: 'WR' }];

  assert.deepEqual(upsertPicks('f1:12345678', ledger, live), [
    { pickNumber: 29, player: 'C. Olave', fantasyTeam: 'Team 5', roundNumber: 3, position: 'WR' },
  ]);
});

test('upserts richer observations without duplicating an existing pick', () => {
  const existing = [
    {
      pickNumber: 2,
      player: 'Justin Jefferson',
      recordedAt: '2026-08-01T00:00:00.000Z',
    },
  ];
  const incoming = [
    {
      pickNumber: 2,
      player: 'Justin Jefferson',
      position: 'WR',
      nflTeam: 'MIN',
      recordedAt: '2026-08-01T00:01:00.000Z',
    },
    {
      pickNumber: 1,
      player: 'Ja’Marr Chase',
      recordedAt: '2026-08-01T00:00:30.000Z',
    },
  ];

  assert.deepEqual(upsertPicks('f1:12345678', existing, incoming), [
    {
      pickNumber: 1,
      player: 'Ja’Marr Chase',
      recordedAt: '2026-08-01T00:00:30.000Z',
    },
    {
      pickNumber: 2,
      player: 'Justin Jefferson',
      position: 'WR',
      nflTeam: 'MIN',
      recordedAt: '2026-08-01T00:00:00.000Z',
    },
  ]);
});
