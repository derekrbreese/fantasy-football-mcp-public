const test = require('node:test');
const assert = require('node:assert/strict');

const {
  addToWatchlist,
  markNotified,
  moveWatchlistEntry,
  reconcileWatchlist,
  removeFromWatchlist,
  sanitizePreferences,
  shouldNotify,
  storageKey,
  toggleComparison,
} = require('../draft-cockpit.js');

const candidates = [
  { name: 'Amon-Ra St. Brown', position: 'WR', team: 'DET', score: 91.2, tier: 'elite' },
  { name: 'Breece Hall', position: 'RB', team: 'NYJ', score: 89.4, tier: 'starter' },
  { name: 'Sam LaPorta', position: 'TE', team: 'DET', score: 87.1, tier: 'starter' },
  { name: 'Josh Allen', position: 'QB', team: 'BUF', score: 86.5, tier: 'starter' },
];

test('sanitizes exact-league cockpit preferences and bounds private player state', () => {
  const raw = {
    watchlist: Array.from({ length: 30 }, (_, index) => ({
      ...candidates[index % candidates.length],
      name: `${candidates[index % candidates.length].name} ${index}`,
      secretUrl: 'https://example.test/private',
    })),
    comparisonKeys: ['unsafe', ...Array.from({ length: 10 }, (_, index) => `key-${index}`)],
    notificationsEnabled: true,
    lastNotificationKey: 'x'.repeat(500),
    cookie: 'secret',
  };

  const preferences = sanitizePreferences(raw);

  assert.equal(preferences.watchlist.length, 20);
  assert.equal(preferences.comparisonKeys.length, 3);
  assert.equal(preferences.notificationsEnabled, true);
  assert.ok(preferences.lastNotificationKey.length <= 160);
  assert.equal(JSON.stringify(preferences).includes('secret'), false);
  assert.equal(storageKey('10572539'), 'yahooDraftCockpitPreferences:v1:10572539');
  assert.throws(() => storageKey('../private'), /valid Yahoo league ID/);
});

test('adds, reorders, removes, and reconciles a conservative exact player queue', () => {
  let preferences = sanitizePreferences({});
  preferences = addToWatchlist(preferences, candidates[0]);
  preferences = addToWatchlist(preferences, candidates[1]);
  preferences = addToWatchlist(preferences, candidates[0]);
  assert.deepEqual(preferences.watchlist.map((item) => item.name), [
    'Amon-Ra St. Brown',
    'Breece Hall',
  ]);

  preferences = moveWatchlistEntry(preferences, preferences.watchlist[1].key, -1);
  assert.deepEqual(preferences.watchlist.map((item) => item.name), [
    'Breece Hall',
    'Amon-Ra St. Brown',
  ]);

  const reconciled = reconcileWatchlist(preferences.watchlist, [{
    player: 'A. St. Brown', position: 'WR', nflTeam: 'DET', pickNumber: 17,
  }]);
  assert.equal(reconciled[0].drafted, false);
  assert.equal(reconciled[1].drafted, true);
  assert.equal(reconciled[1].pickNumber, 17);

  preferences = removeFromWatchlist(preferences, preferences.watchlist[0].key);
  assert.deepEqual(preferences.watchlist.map((item) => item.name), ['Amon-Ra St. Brown']);
});

test('limits quick comparison to three current candidates', () => {
  let preferences = sanitizePreferences({});
  for (const candidate of candidates) preferences = addToWatchlist(preferences, candidate);
  for (const item of preferences.watchlist) preferences = toggleComparison(preferences, item.key);
  assert.equal(preferences.comparisonKeys.length, 3);

  const first = preferences.comparisonKeys[0];
  preferences = toggleComparison(preferences, first);
  assert.equal(preferences.comparisonKeys.includes(first), false);
});

test('turn notifications are opt-in, authoritative, urgent, and deduplicated', () => {
  const session = {
    sessionKey: 'nfl:10572539',
    leagueId: '10572539',
    updatedAt: '2026-09-02T02:00:00.000Z',
  };
  const response = {
    generatedAt: session.updatedAt,
    state: {
      picksUntilUserTurn: 1,
      health: { complete: true, fresh: true },
    },
    recommendations: [{ player: { name: 'Amon-Ra St. Brown' } }],
  };

  assert.equal(shouldNotify(sanitizePreferences({}), response, session).notify, false);
  let preferences = sanitizePreferences({ notificationsEnabled: true });
  const decision = shouldNotify(preferences, response, session);
  assert.equal(decision.notify, true);
  assert.match(decision.title, /You are next/);
  assert.match(decision.message, /Amon-Ra St. Brown/);

  preferences = markNotified(preferences, decision.key);
  assert.equal(shouldNotify(preferences, response, session).notify, false);
  assert.equal(shouldNotify(preferences, {
    ...response,
    state: { picksUntilUserTurn: 1, health: { complete: false, fresh: true } },
  }, session).notify, false);
  assert.equal(shouldNotify(preferences, {
    ...response,
    state: { picksUntilUserTurn: 1, health: { complete: true, fresh: false } },
  }, session).notify, false);
});
