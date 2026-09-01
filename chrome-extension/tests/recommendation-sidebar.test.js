const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const {
  leagueChoices,
  createRecommendationRequestGuard,
  recommendationStillMatchesSelection,
  resolveExplicitSelection,
} = require('../recommendation-sidebar-state.js');

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

test('manifest keeps the recorder popup and adds a persistent Firefox sidebar', () => {
  const extensionRoot = path.join(__dirname, '..');
  const manifest = JSON.parse(fs.readFileSync(path.join(extensionRoot, 'manifest.json'), 'utf8'));
  const html = fs.readFileSync(path.join(extensionRoot, 'assistant.html'), 'utf8');
  const popupHtml = fs.readFileSync(path.join(extensionRoot, 'popup.html'), 'utf8');
  const popupSource = fs.readFileSync(path.join(extensionRoot, 'popup.js'), 'utf8');

  assert.equal(manifest.action.default_popup, 'popup.html');
  assert.equal(manifest.sidebar_action.default_panel, 'assistant.html');
  assert.equal(manifest.sidebar_action.default_title, 'Fantasy Draft Assistant');
  assert.equal(manifest.content_scripts[0].js.includes('assistant.js'), false);
  assert.match(html, /id="league-select"/);
  assert.match(html, /id="refresh-recommendations"/);
  assert.match(html, /recommendation-client\.js/);
  assert.match(html, /recommendation-view-model\.js/);
  assert.match(html, /recommendation-renderer\.js/);
  assert.doesNotMatch(html, /<script[^>]*>[^<]+<\/script>/);
  assert.match(popupHtml, /id="open-assistant"/);
  assert.match(popupHtml, /id="open-dashboard"/);
  assert.match(popupSource, /sidebarAction\?\.open/);
  assert.match(popupSource, /draft-dashboard#leagueId=/);
});
