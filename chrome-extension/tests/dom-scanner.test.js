const test = require('node:test');
const assert = require('node:assert/strict');
const { loadDomFixture } = require('./dom-fixture.js');

const {
  collectDiagnosticSnapshots,
  findCurrentPickNumber,
  findLiveDraftSnapshot,
  findRoundByRoundSnapshots,
  scanAuthoritativeRoundByRoundTables,
  snapshotPickElement,
} = require('../dom-scanner.js');
const { parseRoundByRoundSnapshot } = require('../draft-parser.js');

function node(textContent) {
  return { textContent };
}

function fakeElement({ text, attributes = {}, selectors = {} }) {
  return {
    textContent: text,
    getAttribute(name) {
      return attributes[name] ?? null;
    },
    querySelector(selectorList) {
      for (const selector of selectorList.split(',').map((value) => value.trim())) {
        if (selectors[selector]) return selectors[selector];
      }
      return null;
    },
  };
}

test('snapshots semantic pick fields from a candidate element', () => {
  const element = fakeElement({
    text: 'Pick 9 Player details',
    attributes: { 'data-pick-number': '9', 'aria-label': 'Draft pick 9' },
    selectors: {
      '[data-player-name]': node('Breece Hall'),
      '[data-position]': node('RB'),
      '[data-nfl-team]': node('NYJ'),
      '[data-fantasy-team]': node('Gridiron Greats'),
    },
  });

  assert.deepEqual(snapshotPickElement(element), {
    text: 'Pick 9 Player details',
    attributes: {
      'aria-label': 'Draft pick 9',
      'data-pick-number': '9',
    },
    labels: {
      player: 'Breece Hall',
      position: 'RB',
      nflTeam: 'NYJ',
      fantasyTeam: 'Gridiron Greats',
    },
  });
});

test('does not snapshot oversized containers that are likely the whole draft page', () => {
  const element = fakeElement({ text: `Pick 1 ${'player '.repeat(200)}` });
  assert.equal(snapshotPickElement(element), null);
});

test('finds Yahoo live status and last-pick banners by their visible text', () => {
  const elements = [
    { innerText: 'Whole page YOUR TURN • ROUND 2, PICK 19 plus lots of content' },
    { innerText: 'YOUR TURN\n• ROUND 2, PICK 19' },
    { innerText: 'Last:\nD. LONDON\n(WR · ATL)' },
    { innerText: 'Last:\nD. LONDON\n(WR · ATL)\nTeam 7' },
  ];
  const root = { querySelectorAll: () => elements };

  assert.deepEqual(findLiveDraftSnapshot(root), {
    statusText: 'YOUR TURN\n• ROUND 2, PICK 19',
    lastPickText: 'Last:\nD. LONDON\n(WR · ATL)\nTeam 7',
  });
});

test('finds the last-pick banner while Yahoo is paused', () => {
  const root = {
    querySelectorAll: () => [
      { innerText: 'Draft Paused' },
      { innerText: 'Last:\nC. Olave\n(WR · NO)\nTeam 5' },
    ],
  };

  assert.deepEqual(findLiveDraftSnapshot(root), {
    statusText: 'Draft Paused',
    lastPickText: 'Last:\nC. Olave\n(WR · NO)\nTeam 5',
  });
});

test('extracts Yahoo Round by Round table rows with their round headers', () => {
  function row(values, heading = false) {
    const cells = values.map((textContent) => ({ textContent, innerText: textContent }));
    return {
      innerText: values.join('\n'),
      querySelectorAll(selector) {
        if (selector === 'td') return heading ? [] : cells;
        return [];
      },
    };
  }

  const round3 = row(['ROUND 3'], true);
  const olave = row(['29', 'C. Olave\nWR\nNO\nBye 8', 'Team 5']);
  const hall = row(['28', 'B. Hall\nQ\nRB\nNYJ\nBye 13', 'Team 4']);
  const round2 = row(['ROUND 2'], true);
  const barkley = row(['19', 'S. Barkley\nRB\nPhi\nBye 10', 'Your Team']);
  const table = {
    querySelector(selector) {
      return selector === 'thead' ? { innerText: 'Pick Player Team' } : null;
    },
    querySelectorAll(selector) {
      return selector === 'tr' ? [round3, olave, hall, round2, barkley] : [];
    },
  };
  const root = { querySelectorAll: (selector) => (selector === 'table' ? [table] : []) };

  assert.deepEqual(findRoundByRoundSnapshots(root), [
    {
      roundText: 'ROUND 3',
      pickText: '29',
      playerText: 'C. Olave\nWR\nNO\nBye 8',
      fantasyTeamText: 'Team 5',
    },
    {
      roundText: 'ROUND 3',
      pickText: '28',
      playerText: 'B. Hall\nQ\nRB\nNYJ\nBye 13',
      fantasyTeamText: 'Team 4',
    },
    {
      roundText: 'ROUND 2',
      pickText: '19',
      playerText: 'S. Barkley\nRB\nPhi\nBye 10',
      fantasyTeamText: 'Your Team',
    },
  ]);
});

test('ignores unrelated three-column tables', () => {
  const table = {
    querySelector: () => ({ innerText: 'Rank Player Proj' }),
    querySelectorAll: () => [],
  };
  const root = { querySelectorAll: () => [table] };
  assert.deepEqual(findRoundByRoundSnapshots(root), []);
});

test('extracts the sanitized Yahoo Round by Round DOM fixture', () => {
  assert.deepEqual(findRoundByRoundSnapshots(loadDomFixture('yahoo-round-by-round.html')), [
    { roundText: 'ROUND 1', pickText: '1', playerText: 'J. Chase\nWR\nCIN\nBye 10', fantasyTeamText: 'Team 1' },
    { roundText: 'ROUND 1', pickText: '2', playerText: 'B. Robinson\nRB\nATL\nBye 5', fantasyTeamText: 'Your Team' },
    { roundText: 'ROUND 1', pickText: '3', playerText: 'J. Jefferson\nWR\nMIN\nBye 6', fantasyTeamText: 'Team 3' },
  ]);
});

test('collapses identical responsive copies into one authoritative ledger', () => {
  const result = scanAuthoritativeRoundByRoundTables(
    loadDomFixture('yahoo-round-by-round-responsive-duplicate.html'),
  );

  assert.equal(result.ok, true);
  assert.equal(result.tableCount, 2);
  assert.equal(result.distinctTableCount, 1);
  assert.equal(result.apparentRowCount, 2);
  assert.deepEqual(result.snapshots.map((snapshot) => snapshot.pickText), ['1', '2']);
});

test('retains every apparent row so malformed Yahoo markup cannot pass repair', () => {
  const result = scanAuthoritativeRoundByRoundTables(
    loadDomFixture('yahoo-round-by-round-malformed.html'),
  );

  assert.equal(result.ok, true);
  assert.equal(result.apparentRowCount, 3);
  assert.deepEqual(result.snapshots.map((snapshot) => snapshot.pickText), ['1', 'Loading', '3']);
  assert.equal(result.snapshots.map(parseRoundByRoundSnapshot).filter(Boolean).length, 1);
});

test('counts unexpected cell counts and role-cell rows so repair fails closed', () => {
  const result = scanAuthoritativeRoundByRoundTables(
    loadDomFixture('yahoo-round-by-round-unexpected-shapes.html'),
  );

  assert.equal(result.ok, true);
  assert.equal(result.apparentRowCount, 4);
  assert.equal(result.snapshots.map(parseRoundByRoundSnapshot).filter(Boolean).length, 1);
  assert.deepEqual(result.snapshots.slice(1).map((snapshot) => snapshot.cellShape), [
    'td:2',
    'td:4',
    'role-cell:3',
  ]);
});

test('rejects conflicting Round by Round tables as ambiguous', () => {
  const first = loadDomFixture('yahoo-round-by-round.html').querySelectorAll('table')[0];
  const partial = loadDomFixture('yahoo-round-by-round-malformed.html').querySelectorAll('table')[0];
  const result = scanAuthoritativeRoundByRoundTables({
    querySelectorAll: (selector) => (selector === 'table' ? [first, partial] : []),
  });

  assert.equal(result.ok, false);
  assert.equal(result.tableCount, 2);
  assert.equal(result.distinctTableCount, 2);
  assert.match(result.error, /conflicting/i);
});

test('finds the live current pick even when no last-pick banner is present', () => {
  const root = {
    querySelectorAll: () => [
      { innerText: 'Whole page ROUND 4, PICK 37 plus other content' },
      { innerText: 'YOUR TURN\n• ROUND 4, PICK 37' },
    ],
  };

  assert.equal(findCurrentPickNumber(root), 37);
});

test('collects only structural diagnostic counters from adversarial DOM', () => {
  const playerRow = {
    tagName: 'DIV',
    textContent: 'Secret manager chat https://example.test/?auth=secret Round 2 Pick 19',
    className: 'secret-manager-chat auth=secret',
    childElementCount: 4,
    getAttribute(name) {
      return {
        role: 'row',
        'data-pick-number': '19',
        'data-testid': 'secret-query-string',
        'aria-label': 'Private Manager Name',
        href: 'https://example.test/?auth=secret',
      }[name] ?? null;
    },
    querySelector() { return null; },
  };
  const root = {
    querySelectorAll(selector) {
      if (selector === 'table') return [];
      return [playerRow];
    },
  };

  const diagnostics = collectDiagnosticSnapshots(root);
  assert.deepEqual(diagnostics, {
    candidateCount: 1,
    snapshottedCandidateCount: 1,
    roundByRoundTableCount: 0,
    roundByRoundDistinctTableCount: 0,
    roundByRoundApparentRowCount: 0,
    fieldPresence: {
      pickNumber: 1,
      roundNumber: 0,
      roundPick: 0,
      player: 0,
      position: 0,
      nflTeam: 0,
      fantasyTeam: 0,
    },
  });
  const serialized = JSON.stringify(diagnostics);
  for (const forbidden of ['secret', 'Manager', 'chat', 'https:', 'auth=', 'className', 'testId', 'ariaLabel']) {
    assert.equal(serialized.includes(forbidden), false);
  }
});
