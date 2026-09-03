const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const {
  createRecommendationAutoRefreshScheduler,
  leagueChoices,
  createRecommendationRequestGuard,
  isRefreshShortcut,
  recommendationStillMatchesSelection,
  resolveExplicitSelection,
  storageChangeAffectsSession,
} = require('../recommendation-sidebar-state.js');

test('accepts a plain R shortcut but ignores typing and modified keystrokes', () => {
  assert.equal(isRefreshShortcut({ key: 'r', target: { tagName: 'BODY' } }), true);
  assert.equal(isRefreshShortcut({ key: 'R', target: { tagName: 'DIV' } }), true);
  assert.equal(isRefreshShortcut({ key: 'r', ctrlKey: true, target: { tagName: 'BODY' } }), false);
  assert.equal(isRefreshShortcut({ key: 'r', target: { tagName: 'SELECT' } }), false);
  assert.equal(isRefreshShortcut({ key: 'r', target: { isContentEditable: true, tagName: 'DIV' } }), false);
});

test('selects only a validated active Yahoo league and never falls back to latest saved state', () => {
  const sessions = {
    'f1:111': { sport: 'f1', leagueId: '111', sessionKey: 'f1:111', updatedAt: '2026-08-31T23:00:00.000Z' },
    'f1:222': { sport: 'f1', leagueId: '222', sessionKey: 'f1:222', updatedAt: '2026-08-31T23:01:00.000Z' },
  };

  assert.equal(resolveExplicitSelection(sessions, { sessionKey: 'f1:111' }), 'f1:111');
  assert.equal(resolveExplicitSelection(sessions, null), null);
  assert.equal(resolveExplicitSelection(sessions, { sessionKey: 'f1:missing' }), null);
});

test('discards a recommendation when the selected league changes in flight', () => {
  const session = {
    sport: 'f1', leagueId: '111', sessionKey: 'f1:111', updatedAt: '2026-08-31T23:00:00.000Z',
  };
  const result = {
    status: 'success', leagueId: '111', generatedAt: '2026-08-31T23:00:00.000Z',
  };

  assert.equal(
    recommendationStillMatchesSelection('f1:111', 'f1:111', session, session, result),
    true,
  );
  assert.equal(
    recommendationStillMatchesSelection('f1:111', 'f1:222', session, session, result),
    false,
  );
  assert.equal(
    recommendationStillMatchesSelection('f1:111', 'f1:111', session, session, { ...result, leagueId: '222' }),
    false,
  );
  assert.equal(
    recommendationStillMatchesSelection(
      'f1:111',
      'f1:111',
      session,
      { ...session, updatedAt: '2026-08-31T23:01:00.000Z' },
      result,
    ),
    false,
  );
});

test('new league selection aborts A and generation guards both A success and A error', async () => {
  const guard = createRecommendationRequestGuard();
  const sessionA = {
    sport: 'f1', leagueId: '111', sessionKey: 'f1:111', updatedAt: '2026-08-31T23:00:00.000Z',
  };
  const tokenA = guard.begin(sessionA);
  let resolveA;
  let rejectA;
  const aSuccess = new Promise((resolve) => { resolveA = resolve; })
    .then(() => guard.requestStillMatchesSelection(tokenA, 'f1:111'));
  const aError = new Promise((_resolve, reject) => { rejectA = reject; })
    .catch(() => guard.requestStillMatchesSelection(tokenA, 'f1:111'));
  const sessionB = {
    sport: 'f1', leagueId: '222', sessionKey: 'f1:222', updatedAt: '2026-08-31T23:00:01.000Z',
  };
  const tokenB = guard.begin(sessionB);

  assert.equal(tokenA.signal.aborted, true);
  assert.equal(guard.requestStillMatchesSelection(tokenA, 'f1:222'), false);
  assert.equal(guard.requestStillMatchesSelection(tokenB, 'f1:222'), true);
  assert.equal(guard.requestStillMatchesSelection(tokenA, 'f1:111'), false);
  resolveA();
  rejectA(new Error('A failed after B selection'));
  assert.equal(await aSuccess, false);
  assert.equal(await aError, false);
});

test('recognizes storage changes only for the explicitly selected league', () => {
  const selected = 'f1:111';
  const encoded = encodeURIComponent(selected);
  const selectedSession = {
    sport: 'f1', leagueId: '111', sessionKey: selected,
    updatedAt: '2026-08-31T23:00:00.000Z',
  };

  assert.equal(storageChangeAffectsSession({
    [`yahooDraftRecorderSession:${encoded}`]: {
      oldValue: selectedSession,
      newValue: { ...selectedSession, updatedAt: '2026-08-31T23:00:01.000Z' },
    },
  }, selected), true);
  assert.equal(storageChangeAffectsSession({
    [`yahooDraftRecorderSession:${encoded}`]: {
      oldValue: selectedSession,
      newValue: { ...selectedSession, lastSyncedAt: selectedSession.updatedAt },
    },
  }, selected), false, 'sync bookkeeping alone does not change the draft revision');
  assert.equal(storageChangeAffectsSession({
    [`yahooDraftRecorderSession:${encoded}`]: {
      oldValue: selectedSession,
      newValue: null,
    },
  }, selected), true, 'deleting the selected session remains actionable');
  assert.equal(storageChangeAffectsSession({
    [`yahooDraftRecorderSessionDeleted:${encoded}`]: {
      newValue: { clearedAt: '2026-08-31T23:00:02.000Z' },
    },
  }, selected), true);
  assert.equal(storageChangeAffectsSession({
    [`yahooDraftRecorderPendingRepair:${encoded}`]: { newValue: { state: 'intent' } },
  }, selected), true);
  assert.equal(storageChangeAffectsSession({
    [`yahooDraftRecorderPendingReset:${encoded}`]: { newValue: { state: 'accepted' } },
  }, selected), true);
  assert.equal(storageChangeAffectsSession({
    yahooDraftRecorderSessions: {
      oldValue: {
        [selected]: {
          sport: 'f1', leagueId: '111', sessionKey: selected,
          updatedAt: '2026-08-31T23:00:00.000Z',
        },
      },
      newValue: {
        [selected]: {
          sport: 'f1', leagueId: '111', sessionKey: selected,
          updatedAt: '2026-08-31T23:00:01.000Z',
        },
      },
    },
  }, selected), true);
  const unchangedSelected = {
    sport: 'f1', leagueId: '111', sessionKey: selected,
    updatedAt: '2026-08-31T23:00:01.000Z',
  };
  assert.equal(storageChangeAffectsSession({
    yahooDraftRecorderSessions: {
      oldValue: { [selected]: unchangedSelected, 'f1:222': { updatedAt: 'before' } },
      newValue: { [selected]: { ...unchangedSelected }, 'f1:222': { updatedAt: 'after' } },
    },
  }, selected), false);
  assert.equal(storageChangeAffectsSession({
    'yahooDraftRecorderSession:f1%3A222': { newValue: { updatedAt: 'later' } },
  }, selected), false);
  assert.equal(storageChangeAffectsSession({}, selected), false);
  assert.equal(storageChangeAffectsSession({
    [`yahooDraftRecorderSession:${encoded}`]: {},
  }, null), false);
});

test('last-synced bookkeeping does not cancel in-flight work or queue a duplicate refresh', () => {
  const selected = 'f1:111';
  const encoded = encodeURIComponent(selected);
  const session = {
    sport: 'f1', leagueId: '111', sessionKey: selected,
    updatedAt: '2026-08-31T23:00:00.000Z',
  };
  let cancelCount = 0;
  let scheduledCallback = null;
  const scheduler = createRecommendationAutoRefreshScheduler({
    setTimeoutImpl(callback) { scheduledCallback = callback; return 1; },
    clearTimeoutImpl() {},
    selectedSessionKey: () => selected,
    sessionForKey: () => session,
    cancelInFlight() { cancelCount += 1; },
    async reloadSessions() { return true; },
    async refresh() { throw new Error('must not refresh an unchanged revision'); },
  });
  scheduler.markRequested(session);

  const changes = {
    [`yahooDraftRecorderSession:${encoded}`]: {
      oldValue: session,
      newValue: { ...session, lastSyncedAt: session.updatedAt },
    },
  };
  if (storageChangeAffectsSession(changes, selected)) scheduler.schedule(selected);

  assert.equal(cancelCount, 0);
  assert.equal(scheduledCallback, null);
});

test('selected-league updates debounce, abort stale work, reload, and refresh one new revision', async () => {
  const callbacks = [];
  const cleared = [];
  const sessions = {
    'f1:111': {
      sport: 'f1', leagueId: '111', sessionKey: 'f1:111', updatedAt: '2026-08-31T23:00:00.000Z',
    },
  };
  let selected = 'f1:111';
  let cancelCount = 0;
  let reloadCount = 0;
  let refreshCount = 0;
  const scheduler = createRecommendationAutoRefreshScheduler({
    delayMs: 350,
    setTimeoutImpl(callback, delay) {
      assert.equal(delay, 350);
      callbacks.push(callback);
      return callbacks.length;
    },
    clearTimeoutImpl(identifier) { cleared.push(identifier); },
    selectedSessionKey: () => selected,
    sessionForKey: (key) => sessions[key],
    cancelInFlight() { cancelCount += 1; },
    async reloadSessions() { reloadCount += 1; return true; },
    async refresh() { refreshCount += 1; },
  });
  scheduler.markRequested(sessions[selected]);

  assert.equal(scheduler.schedule('../unsafe'), false);
  assert.equal(scheduler.schedule(selected), true);
  assert.equal(scheduler.schedule(selected), true);
  assert.equal(cancelCount, 2);
  assert.deepEqual(cleared, [1]);
  sessions[selected] = { ...sessions[selected], updatedAt: '2026-08-31T23:00:01.000Z' };

  await callbacks[0]();
  assert.equal(reloadCount, 0, 'superseded debounce callbacks stay inert');
  await callbacks[1]();
  assert.equal(reloadCount, 1);
  assert.equal(refreshCount, 1);

  scheduler.schedule(selected);
  await callbacks[2]();
  assert.equal(reloadCount, 2);
  assert.equal(refreshCount, 1, 'the same snapshot is not requested twice');

  scheduler.schedule(selected);
  selected = 'f1:222';
  await callbacks[3]();
  assert.equal(reloadCount, 2);
  assert.equal(refreshCount, 1, 'a selection change invalidates scheduled work');
});

test('auto-refresh debounce is clamped and reports asynchronous reload errors', async () => {
  let scheduledDelay;
  let scheduledCallback;
  let reported;
  const session = {
    sport: 'f1', leagueId: '111', sessionKey: 'f1:111', updatedAt: '2026-08-31T23:00:01.000Z',
  };
  const scheduler = createRecommendationAutoRefreshScheduler({
    delayMs: 60_000,
    setTimeoutImpl(callback, delay) {
      scheduledCallback = callback;
      scheduledDelay = delay;
      return 1;
    },
    clearTimeoutImpl() {},
    selectedSessionKey: () => session.sessionKey,
    sessionForKey: () => session,
    cancelInFlight() {},
    async reloadSessions() { throw new Error('storage unavailable'); },
    async refresh() { throw new Error('must not refresh'); },
    onError(error) { reported = error; },
  });

  scheduler.schedule(session.sessionKey);
  await scheduledCallback();

  assert.equal(scheduledDelay, 1_000);
  assert.match(reported.message, /storage unavailable/);
});

test('sidebar controller wires cancellation, snapshot re-read, and five-way isolation guard', () => {
  const extensionRoot = path.join(__dirname, '..');
  const source = fs.readFileSync(path.join(extensionRoot, 'assistant.js'), 'utf8');

  assert.match(source, /createRecommendationRequestGuard\(\)/);
  assert.match(source, /requestGuard\.begin\(session\)/);
  assert.match(source, /signal:\s*token\.signal/);
  assert.match(source, /const currentSession = await draftStorage\.getSession\(selected\)/);
  assert.match(
    source,
    /recommendationStillMatchesSelection\(\s*selected,\s*selectedSessionKey,\s*session,\s*currentSession,\s*result,/,
  );
  assert.ok((source.match(/cancelPendingRecommendation\(\)/g) || []).length >= 4);
  assert.match(
    source,
    /elements\.league\.addEventListener\('change', \(\) => \{\s*sessionLoadGeneration \+= 1;/,
  );
  assert.match(source, /createRecommendationAutoRefreshScheduler/);
  assert.match(source, /storageChangeAffectsSession\(\s*changes,\s*selectedSessionKey,?\s*\)/);
  assert.match(source, /autoRefresh\.schedule\(selectedSessionKey\)/);
  assert.match(source, /autoRefresh\.markRequested\(session\)/);
  assert.match(source, /autoRefresh\.cancelScheduled\(\)/);
  assert.match(
    source,
    /elements\.refresh\.addEventListener\('click', \(\) => \{\s*autoRefresh\.cancelScheduled\(\);\s*refreshRecommendations\(\);/,
  );
  assert.match(source, /simulations:\s*256/);

  const refreshStart = source.indexOf('async function refreshRecommendations()');
  const catchStart = source.indexOf('} catch (error) {', refreshStart);
  const errorGuard = source.indexOf(
    'if (!requestGuard.requestStillMatchesSelection(token, selectedSessionKey)) return;',
    catchStart,
  );
  const errorRender = source.indexOf('renderMessage(message, session);', catchStart);
  assert.ok(catchStart > refreshStart);
  assert.ok(errorGuard > catchStart && errorGuard < errorRender);
});

test('league chooser exposes only allowlisted identity labels', () => {
  const choices = leagueChoices({
    'f1:222': {
      sport: 'f1', leagueId: '222', sessionKey: 'f1:222',
      fantasyTeam: '<img onerror=alert(1)>',
    },
    'f1:111': { sport: 'f1', leagueId: '111', sessionKey: 'f1:111' },
    malicious: { sport: 'f1', leagueId: '../secret', sessionKey: 'f1:../secret' },
  });

  assert.deepEqual(choices, [
    { sessionKey: 'f1:111', leagueId: '111', label: 'League 111' },
    { sessionKey: 'f1:222', leagueId: '222', label: 'League 222' },
  ]);
});

test('manifest keeps the recorder UI and cross-browser background lock broker', () => {
  const extensionRoot = path.join(__dirname, '..');
  const manifest = JSON.parse(fs.readFileSync(path.join(extensionRoot, 'manifest.json'), 'utf8'));
  const html = fs.readFileSync(path.join(extensionRoot, 'assistant.html'), 'utf8');
  const popupHtml = fs.readFileSync(path.join(extensionRoot, 'popup.html'), 'utf8');
  const popupSource = fs.readFileSync(path.join(extensionRoot, 'popup.js'), 'utf8');

  assert.equal(manifest.action.default_popup, 'popup.html');
  assert.equal(manifest.sidebar_action.default_panel, 'assistant.html');
  assert.equal(manifest.sidebar_action.default_title, 'Fantasy Draft Assistant');
  assert.deepEqual(manifest.background, {
    scripts: ['lock-broker.js'],
    service_worker: 'lock-broker.js',
  });
  assert.equal(manifest.minimum_chrome_version, '121');
  assert.ok(manifest.permissions.includes('notifications'));
  assert.equal(manifest.content_scripts[0].js.includes('assistant.js'), false);
  assert.match(html, /id="league-select"/);
  assert.match(html, /id="refresh-recommendations"/);
  assert.match(html, /aria-keyshortcuts="R"/);
  assert.match(html, /recommendation-client\.js/);
  assert.match(html, /recommendation-view-model\.js/);
  assert.match(html, /recommendation-renderer\.js/);
  assert.match(html, /draft-cockpit\.js/);
  assert.match(html, /id="queue-candidate"/);
  assert.match(html, /id="turn-notifications"/);
  assert.match(
    fs.readFileSync(path.join(extensionRoot, 'assistant.js'), 'utf8'),
    /storageKey\(session\.sessionKey\)/,
  );
  assert.match(
    fs.readFileSync(path.join(extensionRoot, 'assistant.js'), 'utf8'),
    /notificationId\(session\.sessionKey\)/,
  );
  assert.doesNotMatch(html, /<script[^>]*>[^<]+<\/script>/);
  assert.match(popupHtml, /id="open-assistant"/);
  assert.match(popupHtml, /id="open-dashboard"/);
  assert.match(popupSource, /sidebarAction\?\.open/);
  assert.match(popupSource, /draft-dashboard#leagueId=/);
});

test('manifest packages correctly sized football icons for browser surfaces', () => {
  const extensionRoot = path.join(__dirname, '..');
  const manifest = JSON.parse(fs.readFileSync(path.join(extensionRoot, 'manifest.json'), 'utf8'));
  const packageMetadata = JSON.parse(fs.readFileSync(path.join(extensionRoot, 'package.json'), 'utf8'));
  const expectedIcons = {
    16: 'icons/football-16.png',
    32: 'icons/football-32.png',
    48: 'icons/football-48.png',
    64: 'icons/football-64.png',
    96: 'icons/football-96.png',
    128: 'icons/football-128.png',
  };

  assert.deepEqual(manifest.icons, expectedIcons);
  assert.deepEqual(manifest.action.default_icon, {
    16: 'icons/football-16.png',
    24: 'icons/football-24.png',
    32: 'icons/football-32.png',
    48: 'icons/football-48.png',
    64: 'icons/football-64.png',
  });
  assert.deepEqual(manifest.sidebar_action.default_icon, {
    16: 'icons/football-16.png',
    32: 'icons/football-32.png',
    64: 'icons/football-64.png',
  });
  assert.equal(packageMetadata.version, manifest.version);

  const iconPaths = new Set([
    ...Object.values(manifest.icons),
    ...Object.values(manifest.action.default_icon),
    ...Object.values(manifest.sidebar_action.default_icon),
  ]);
  for (const relativePath of iconPaths) {
    const icon = fs.readFileSync(path.join(extensionRoot, relativePath));
    const expectedSize = Number(relativePath.match(/-(\d+)\.png$/)?.[1]);
    assert.deepEqual(icon.subarray(0, 8), Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
    assert.equal(icon.readUInt32BE(16), expectedSize, relativePath);
    assert.equal(icon.readUInt32BE(20), expectedSize, relativePath);
  }
});
