const test = require('node:test');
const assert = require('node:assert/strict');

const {
  collectDiagnosticSnapshots,
  findLiveDraftSnapshot,
  findRoundByRoundSnapshots,
  snapshotPickElement,
} = require('../dom-scanner.js');

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

test('collects sanitized football-related DOM diagnostics without URLs', () => {
  const playerRow = {
    tagName: 'DIV',
    textContent: 'RB J. COOK III RB · Buf · Bye 7 Round 2 Pick 19',
    className: 'results-row player-card',
    childElementCount: 4,
    getAttribute(name) {
      return {
        role: 'row',
        'data-testid': 'team-result',
        'aria-label': 'James Cook result',
        href: 'https://example.test/?auth=secret',
      }[name] ?? null;
    },
  };
  const unrelated = { ...playerRow, textContent: 'Settings Help', className: 'footer' };
  const root = { querySelectorAll: () => [playerRow, unrelated] };

  assert.deepEqual(collectDiagnosticSnapshots(root), [
    {
      tag: 'div',
      role: 'row',
      className: 'results-row player-card',
      testId: 'team-result',
      ariaLabel: 'James Cook result',
      childCount: 4,
      text: 'RB J. COOK III RB · Buf · Bye 7 Round 2 Pick 19',
    },
  ]);
});
