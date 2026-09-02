const test = require('node:test');
const assert = require('node:assert/strict');
const { loadDomFixture } = require('./dom-fixture.js');

const {
  collectDiagnosticSnapshots,
  extractYahooPlayerKey,
  findCurrentPickNumber,
  findLiveDraftSnapshot,
  findPicksPanelSnapshots,
  findPickSnapshots,
  findRoundByRoundSnapshots,
  scanAuthoritativeRoundByRoundTables,
  snapshotPickElement,
} = require('../dom-scanner.js');
const { parsePicksPanelSnapshot, parseRoundByRoundSnapshot } = require('../draft-parser.js');
const { evaluateAuthoritativeLedgerScan } = require('../ledger-health.js');

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
    attributes: {
      'data-pick-number': '9',
      'data-player-key': '461.p.33536',
      'aria-label': 'Draft pick 9',
    },
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
      playerKey: '461.p.33536',
    },
  });
});

test('extracts only a canonical Yahoo player key and never returns its containing URL', () => {
  const anchor = fakeElement({
    text: 'Breece Hall',
    attributes: {
      href: 'https://football.fantasysports.yahoo.com/f1/10547893/playernote?player_key=461.p.33536&auth=secret',
    },
  });
  const playerCell = fakeElement({ text: 'Breece Hall RB NYJ' });
  playerCell.querySelectorAll = (selector) => (selector === 'a[href]' ? [anchor] : []);

  assert.equal(extractYahooPlayerKey(playerCell), '461.p.33536');
  assert.equal(JSON.stringify(extractYahooPlayerKey(playerCell)).includes('auth'), false);
  assert.equal(extractYahooPlayerKey(fakeElement({
    text: 'malicious',
    attributes: { 'data-player-key': 'https://evil.test/?player_key=461.p.99999' },
  })), null);

  for (const href of [
    'https://football.fantasysports.yahoo.com/f1/10547893/settings?player_key=461.p.33536',
    'https://football.fantasysports.yahoo.com/player/%E0%A4%A?player_key=bad',
  ]) {
    const untrustedAnchor = fakeElement({ text: 'not a player link', attributes: { href } });
    const wrapper = fakeElement({ text: 'still safe' });
    wrapper.querySelectorAll = () => [untrustedAnchor];
    assert.equal(extractYahooPlayerKey(wrapper), null);
  }
});

test('extracts a Yahoo player key only when every supported source agrees', () => {
  const firstKey = '461.p.33536';
  const secondKey = '461.p.99999';

  assert.equal(extractYahooPlayerKey(fakeElement({
    text: 'ambiguous direct attributes',
    attributes: {
      'data-player-key': firstKey,
      'data-yahoo-player-key': secondKey,
    },
  })), null);

  const matchingNested = fakeElement({
    text: 'matching nested key',
    attributes: { 'data-player-key': firstKey },
  });
  const matchingAnchor = fakeElement({
    text: 'matching player link',
    attributes: {
      href: `/player/${firstKey}?player_key=${firstKey}`,
    },
  });
  const agreeing = fakeElement({
    text: 'all identity sources agree',
    attributes: { 'data-player-key': firstKey },
  });
  agreeing.querySelectorAll = (selector) => {
    if (selector === '[data-player-key]') return [matchingNested];
    if (selector === '[data-yahoo-player-key]') return [];
    if (selector === 'a[href]') return [matchingAnchor];
    return [];
  };
  assert.equal(extractYahooPlayerKey(agreeing), firstKey);

  const firstAnchor = fakeElement({
    text: 'first player link',
    attributes: { href: `/player/${firstKey}` },
  });
  const secondAnchor = fakeElement({
    text: 'second player link',
    attributes: { href: `/player/${secondKey}` },
  });
  const conflictingAnchors = fakeElement({ text: 'ambiguous player links' });
  conflictingAnchors.querySelectorAll = (selector) => (
    selector === 'a[href]' ? [firstAnchor, secondAnchor] : []
  );
  assert.equal(extractYahooPlayerKey(conflictingAnchors), null);
});

test('does not snapshot oversized containers that are likely the whole draft page', () => {
  const element = fakeElement({ text: `Pick 1 ${'player '.repeat(200)}` });
  assert.equal(snapshotPickElement(element), null);
});

test('extracts only visible cards from the active semantic Yahoo Picks tab', () => {
  const snapshots = findPicksPanelSnapshots(loadDomFixture('yahoo-picks-panel.html'));

  assert.deepEqual(snapshots, [
    {
      pickNumberText: '1',
      playerText: 'J. GIBBS',
      detailsText: 'RB • Det • Bye 6',
      fantasyTeamText: 'Team 1',
    },
    {
      pickNumberText: '2',
      playerText: 'B. ROBINSON',
      detailsText: 'RB • atl • Bye 11',
      fantasyTeamText: 'Team 2',
    },
    {
      pickNumberText: '3',
      playerText: 'P. NACUA',
      detailsText: 'WR • Lar • Bye 11',
      fantasyTeamText: 'Team 3',
    },
    {
      pickNumberText: '4',
      playerText: 'C. MCCAFFREY',
      detailsText: 'RB • sf • Bye 8',
      fantasyTeamText: 'Your Team',
    },
  ]);
  assert.deepEqual(snapshots.map(parsePicksPanelSnapshot), [
    { pickNumber: 1, player: 'J. GIBBS', position: 'RB', nflTeam: 'DET', fantasyTeam: 'Team 1', isUserPick: false },
    { pickNumber: 2, player: 'B. ROBINSON', position: 'RB', nflTeam: 'ATL', fantasyTeam: 'Team 2', isUserPick: false },
    { pickNumber: 3, player: 'P. NACUA', position: 'WR', nflTeam: 'LAR', fantasyTeam: 'Team 3', isUserPick: false },
    { pickNumber: 4, player: 'C. MCCAFFREY', position: 'RB', nflTeam: 'SF', fantasyTeam: 'Your Team', isUserPick: true },
  ]);

  const serialized = JSON.stringify(snapshots);
  for (const forbidden of ['Questionable', 'https:', 'example.test', 'auth=', 'secret', 'avatar', 'joined', 'QUEUE', 'HIDDEN', 'RESPONSIVE']) {
    assert.equal(serialized.includes(forbidden), false);
  }
});

test('requires one rendered active Picks tab paired with Queue', () => {
  const visible = (text, attributes = {}, parentElement = null) => ({
    innerText: text,
    textContent: text,
    parentElement,
    hidden: false,
    getAttribute: (name) => attributes[name] ?? null,
    closest(selector) {
      if (selector === '[role="tablist"]') return parentElement;
      return null;
    },
  });
  const tablist = visible('', { role: 'tablist' });
  const picks = visible('Picks', { role: 'tab', 'aria-selected': 'false', 'aria-controls': 'picks-panel' }, tablist);
  const queue = visible('Queue', { role: 'tab', 'aria-selected': 'true', 'aria-controls': 'queue-panel' }, tablist);
  const root = {
    querySelectorAll: (selector) => (selector === '[role="tab"]' ? [queue, picks] : []),
    getElementById: () => null,
  };

  assert.deepEqual(findPicksPanelSnapshots(root), []);
});

test('generic pick scanning excludes cards mounted under semantic Queue and Picks panels', () => {
  const panel = (id, labelledBy) => ({
    id,
    getAttribute(name) {
      return { role: 'tabpanel', 'aria-labelledby': labelledBy }[name] ?? null;
    },
    contains(element) { return element === card; },
  });
  let card;
  const picksPanel = panel('picks-panel', 'picks-tab');
  const queuePanel = panel('queue-panel', 'queue-tab');
  const tablist = { getAttribute: (name) => name === 'role' ? 'tablist' : null };
  const tab = (id, text, controls, selected) => ({
    id,
    innerText: text,
    getAttribute(name) {
      return {
        role: 'tab',
        'aria-controls': controls,
        'aria-selected': selected,
      }[name] ?? null;
    },
    closest: (selector) => selector === '[role="tablist"]' ? tablist : null,
  });
  const queueTab = tab('queue-tab', 'Queue', 'queue-panel', 'false');
  const picksTab = tab('picks-tab', 'Picks', 'picks-panel', 'true');
  card = fakeElement({
    text: '1\nJ. Gibbs\nDET - RB\nTeam 1',
    attributes: { 'data-pick-number': '1' },
  });
  card.closest = (selector) => selector === '[role="tabpanel"]' ? picksPanel : null;
  const root = {
    querySelectorAll(selector) {
      if (selector === '[role="tab"]') return [queueTab, picksTab];
      return [card];
    },
    getElementById(id) {
      return { 'queue-panel': queuePanel, 'picks-panel': picksPanel }[id] || null;
    },
  };

  assert.deepEqual(findPickSnapshots(root), []);
});

test('generic pick scanning ignores hidden and non-rendered responsive candidates', () => {
  const visible = fakeElement({
    text: '1\nJ. Gibbs\nDET - RB\nTeam 1',
    attributes: { 'data-pick-number': '1' },
  });
  const hidden = fakeElement({
    text: '2\nHidden Player\nATL - RB\nTeam 2',
    attributes: { 'data-pick-number': '2' },
  });
  hidden.hidden = true;
  const ariaHidden = fakeElement({
    text: '3\nAria Hidden\nLAR - WR\nTeam 3',
    attributes: { 'data-pick-number': '3', 'aria-hidden': 'true' },
  });
  const styledHidden = fakeElement({
    text: '4\nStyled Hidden\nSF - RB\nTeam 4',
    attributes: { 'data-pick-number': '4' },
  });
  styledHidden.ownerDocument = {
    defaultView: { getComputedStyle: () => ({ display: 'none', visibility: 'visible' }) },
  };
  const root = {
    querySelectorAll(selector) {
      return selector === '[role="tab"]' ? [] : [visible, hidden, ariaHidden, styledHidden];
    },
  };

  assert.deepEqual(findPickSnapshots(root), [snapshotPickElement(visible)]);
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

test('extracts stable keys from sanitized Yahoo ledger markup without retaining URLs or attributes', () => {
  const snapshots = findRoundByRoundSnapshots(
    loadDomFixture('yahoo-round-by-round-player-keys.html'),
  );

  assert.deepEqual(snapshots.map((snapshot) => snapshot.playerKey || null), [
    '461.p.33536',
    '461.p.31860',
    '461.p.30123',
    null,
  ]);
  assert.deepEqual(snapshots.map(parseRoundByRoundSnapshot).map((pick) => pick.playerKey || null), [
    '461.p.33536',
    '461.p.31860',
    '461.p.30123',
    null,
  ]);
  const serialized = JSON.stringify(snapshots);
  for (const forbidden of ['https:', 'evil.test', 'auth=', 'token=', 'do-not-copy', 'data-private-manager']) {
    assert.equal(serialized.includes(forbidden), false);
  }
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

test('classifies numbered unfilled fixture rows as future only with current-pick evidence', () => {
  const scan = scanAuthoritativeRoundByRoundTables(
    loadDomFixture('yahoo-round-by-round-future-rows.html'),
  );
  const parsedResults = scan.snapshots.map(parseRoundByRoundSnapshot);
  const evaluation = evaluateAuthoritativeLedgerScan(scan, parsedResults, 3);

  assert.equal(scan.apparentRowCount, 6);
  assert.equal(parsedResults.filter(Boolean).length, 2);
  assert.equal(evaluation.error, null);
  assert.deepEqual(evaluation.authoritativePicks.map((pick) => pick.pickNumber), [1, 2]);
  assert.equal(evaluation.ignoredFutureRowCount, 4);
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

test('ignores a hidden stale current-pick marker', () => {
  const root = {
    querySelectorAll: () => [
      { innerText: '', textContent: 'ROUND 14, PICK 159' },
      { innerText: 'ROUND 14, PICK 160', hidden: true },
      { innerText: 'ROUND 14, PICK 163' },
    ],
  };

  assert.equal(findCurrentPickNumber(root), 163);
});

test('rejects conflicting visible current-pick markers as ambiguous', () => {
  const root = {
    querySelectorAll: () => [
      { innerText: 'ROUND 14, PICK 159' },
      { innerText: 'ROUND 14, PICK 163' },
    ],
  };

  assert.equal(findCurrentPickNumber(root), null);
});

test('accepts duplicate visible markers only when their pick numbers agree', () => {
  const root = {
    querySelectorAll: () => [
      { innerText: 'Draft status ROUND 14, PICK 163' },
      { innerText: 'ROUND 14, PICK 163' },
    ],
  };

  assert.equal(findCurrentPickNumber(root), 163);
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
    picksPanelSnapshotCount: 0,
    fieldPresence: {
      pickNumber: 1,
      roundNumber: 0,
      roundPick: 0,
      player: 0,
      position: 0,
      nflTeam: 0,
      fantasyTeam: 0,
      playerKey: 0,
    },
  });
  const serialized = JSON.stringify(diagnostics);
  for (const forbidden of ['secret', 'Manager', 'chat', 'https:', 'auth=', 'className', 'testId', 'ariaLabel']) {
    assert.equal(serialized.includes(forbidden), false);
  }
});
