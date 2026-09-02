const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const { evaluateAuthoritativeLedgerScan } = require('../ledger-health.js');

test('coherent authoritative scan exposes raw numbered health and exact picks', () => {
  const picks = [
    { pickNumber: 1 },
    { pickNumber: 2 },
    { pickNumber: 2 },
    { pickNumber: 4 },
  ];
  const result = evaluateAuthoritativeLedgerScan(
    { ok: true, tableCount: 1, apparentRowCount: 4 },
    picks,
  );

  assert.equal(result.authoritativePicks, picks);
  assert.deepEqual(result.health.missingPickNumbers, [3]);
  assert.deepEqual(result.health.duplicatePickNumbers, [2]);
  assert.equal(result.error, null);
});

test('absence of an authoritative table clears stale health and errors', () => {
  assert.deepEqual(
    evaluateAuthoritativeLedgerScan(
      { ok: false, tableCount: 0, apparentRowCount: 0, error: 'No table' },
      [],
    ),
    { authoritativePicks: null, health: null, error: null },
  );
});

test('conflicting authoritative tables remain an actionable error', () => {
  const result = evaluateAuthoritativeLedgerScan(
    { ok: false, tableCount: 2, apparentRowCount: 0, error: 'Conflicting Yahoo tables; reload Results.' },
    [],
  );

  assert.equal(result.authoritativePicks, null);
  assert.equal(result.health, null);
  assert.match(result.error, /reload Results/);
});

test('server-bound context uses the session snapshot timestamp, not POST time', () => {
  const contentSource = fs.readFileSync(path.join(__dirname, '..', 'content.js'), 'utf8');

  assert.match(contentSource, /validIsoTimestamp\(session\.updatedAt\)/);
  assert.match(contentSource, /sessionToAgentContext\(\s*session,\s*snapshotTimestamp,/);
  assert.doesNotMatch(contentSource, /sessionToAgentContext\(\s*session,\s*new Date\(\)\.toISOString\(\)/);
});

test('automatic authoritative rollback preserves picks while syncing a capture blocker', () => {
  const contentSource = fs.readFileSync(path.join(__dirname, '..', 'content.js'), 'utf8');
  const guardStart = contentSource.indexOf('if (!automaticUpdate.ok)');
  const acceptedUpdate = contentSource.indexOf('updated = automaticUpdate.session;', guardStart);
  const guardedBranch = contentSource.slice(guardStart, acceptedUpdate);
  const downwardStart = guardedBranch.indexOf("if (automaticUpdate.reason === 'downward-prefix')");
  const mismatchStart = guardedBranch.indexOf('} else {', downwardStart);
  const downwardBranch = guardedBranch.slice(downwardStart, mismatchStart);

  assert.ok(guardStart > 0);
  assert.match(
    downwardBranch,
    /setAuthoritativeCaptureBlocked\(\s*existing,\s*true,\s*now,/,
  );
  assert.doesNotMatch(downwardBranch, /updateDraftSession\(existing, metadata, picks, now\)/);
  assert.match(contentSource.slice(acceptedUpdate), /await syncSession\(updated, \{\}, lease\);/);
});

test('current-pick mismatch falls back to conservative merge and sync', () => {
  const contentSource = fs.readFileSync(path.join(__dirname, '..', 'content.js'), 'utf8');
  const guardStart = contentSource.indexOf('if (!automaticUpdate.ok)');
  const acceptedUpdate = contentSource.indexOf('updated = automaticUpdate.session;', guardStart);
  const guardedBranch = contentSource.slice(guardStart, acceptedUpdate);

  assert.ok(guardStart > 0);
  assert.match(guardedBranch, /automaticUpdate\.reason === 'downward-prefix'/);
  assert.match(guardedBranch, /updateDraftSessionFromSecondaryObservations\([\s\S]*existing,[\s\S]*metadata,[\s\S]*picks,[\s\S]*now,/);
  assert.match(guardedBranch, /setAuthoritativeCaptureBlocked/);
});

test('unsafe authoritative rows still sync conservatively parsed observations', () => {
  const contentSource = fs.readFileSync(path.join(__dirname, '..', 'content.js'), 'utf8');
  const scanStart = contentSource.indexOf('async function performScan(lease)');
  const scanSource = contentSource.slice(
    scanStart,
    contentSource.indexOf('async function performRepair(lease)', scanStart),
  );

  assert.ok(scanStart > 0);
  assert.match(
    scanSource,
    /if \(authoritativeEvaluation\.authoritativePicks\)[\s\S]*else \{\s*updated = YahooDraftSessionStore\.updateDraftSessionFromSecondaryObservations\(/,
  );
  assert.match(scanSource, /await syncSession\(updated, \{\}, lease\);/);
  assert.match(
    scanSource,
    /if \(authoritativeEvaluation\.error\)[\s\S]*setAuthoritativeCaptureBlocked\(updated, true\)/,
  );
});

test('Picks-tab cards enter only the ordinary non-ledger observation path', () => {
  const contentSource = fs.readFileSync(path.join(__dirname, '..', 'content.js'), 'utf8');
  const scanStart = contentSource.indexOf('async function performScan(lease)');
  const repairStart = contentSource.indexOf('async function performRepair(lease)', scanStart);
  const scanSource = contentSource.slice(scanStart, repairStart);
  const repairSource = contentSource.slice(repairStart, contentSource.indexOf('function scanNow(', repairStart));

  assert.match(scanSource, /findPicksPanelSnapshots\(document\)/);
  assert.match(scanSource, /parsePicksPanelSnapshot/);
  assert.match(scanSource, /nonLedgerPicks\.push\(\.\.\.picksPanelPicks\)/);
  assert.match(scanSource, /updateDraftSessionFromSecondaryObservations/);
  assert.doesNotMatch(repairSource, /PicksPanel|picksPanel/);
  assert.doesNotMatch(scanSource, /repair:\s*true/);
});

test('capture-only changes participate in sync deduplication and browser persistence', () => {
  const contentSource = fs.readFileSync(path.join(__dirname, '..', 'content.js'), 'utf8');

  assert.match(
    contentSource,
    /JSON\.stringify\(\[session\.sessionKey, session\.picks, isRepair, session\.authoritativeCaptureBlocked\]\)/,
  );
  assert.match(
    contentSource,
    /existing\?\.authoritativeCaptureBlocked === true[\s\S]*updated\.authoritativeCaptureBlocked === true/,
  );
  assert.match(
    contentSource,
    /existing\?\.numberedLedgerAuthoritative === true[\s\S]*updated\.numberedLedgerAuthoritative === true/,
  );
});

test('automatic scans and repairs share one stable-current-pick ledger evaluation', () => {
  const contentSource = fs.readFileSync(path.join(__dirname, '..', 'content.js'), 'utf8');
  const helperStart = contentSource.indexOf('function evaluateVisibleAuthoritativeLedger()');
  const scanStart = contentSource.indexOf('async function performScan(lease)', helperStart);
  const helperSource = contentSource.slice(helperStart, scanStart);
  const performScanSource = contentSource.slice(
    scanStart,
    contentSource.indexOf('async function performRepair(lease)', scanStart),
  );
  const performRepairSource = contentSource.slice(
    contentSource.indexOf('async function performRepair(lease)', scanStart),
    contentSource.indexOf('function scanNow(', scanStart),
  );

  assert.ok(helperStart > 0);
  assert.equal((helperSource.match(/findCurrentPickNumber\(document\)/g) || []).length, 2);
  assert.match(helperSource, /validateStableCurrentPick/);
  assert.match(helperSource, /snapshots\.map\(.*parseRoundByRoundSnapshot/s);
  assert.match(helperSource, /evaluateAuthoritativeLedgerScan/);
  assert.match(performScanSource, /evaluateVisibleAuthoritativeLedger\(\)/);
  assert.match(performRepairSource, /evaluateVisibleAuthoritativeLedger\(\)/);
  assert.doesNotMatch(performScanSource, /findCurrentPickNumber\(document\)/);
  assert.doesNotMatch(performRepairSource, /findCurrentPickNumber\(document\)/);
});

test('popup resets only the exact active Yahoo session and never a latest-session fallback', () => {
  const popupSource = fs.readFileSync(path.join(__dirname, '..', 'popup.js'), 'utf8');

  assert.match(popupSource, /!activeSessionKey.*sameDraftIdentity\(currentSession, activeDiagnostics\)/s);
  assert.match(popupSource, /coordinator\.begin\(exactSession\)/);
  assert.match(popupSource, /sendToActiveTab\('YAHOO_DRAFT_RECORDER_RESCAN'/);
  assert.doesNotMatch(popupSource, /resetCoordinator\.begin\(latestSession/);
  assert.match(popupSource, /rescanResult\.syncStatus !== 'connected'/);
});

test('content blocks scans and sync while a durable reset journal exists', () => {
  const contentSource = fs.readFileSync(path.join(__dirname, '..', 'content.js'), 'utf8');

  assert.match(contentSource, /draftStorage\.getPendingReset\(metadata\.sessionKey\)/);
  assert.match(contentSource, /reset-pending/);
  assert.match(contentSource, /isTabBlockedByReset\(contentLoadedAt, resetAt, allowedResetAt\)/);
  assert.match(contentSource, /message\?\.resetAt/);
  assert.match(contentSource, /forceSync/);
  assert.match(contentSource, /lastSyncedAt: snapshotTimestamp/);
  assert.match(contentSource, /isSessionReset/);
  assert.match(contentSource, /sport: metadata\.sport/);
  assert.match(contentSource, /teamId: metadata\.teamId/);
});

test('content repair uses the extension broker instead of Firefox page-realm Web Locks', () => {
  const contentSource = fs.readFileSync(path.join(__dirname, '..', 'content.js'), 'utf8');
  const popupSource = fs.readFileSync(path.join(__dirname, '..', 'popup.js'), 'utf8');
  const assistantSource = fs.readFileSync(path.join(__dirname, '..', 'assistant.js'), 'utf8');

  for (const source of [contentSource, popupSource, assistantSource]) {
    assert.match(source, /createSessionOperationLock\(webext\.runtime\)/);
    assert.doesNotMatch(source, /navigator\?*\.locks|navigator\.locks/);
    assert.match(source, /createDraftStorage\(extensionApi, \{ operationLock \}\)/);
  }
  assert.match(contentSource, /async function performScan\(lease\)/);
  assert.match(contentSource, /async function performRepair\(lease\)/);
  assert.match(contentSource, /syncDraftContext\(context, \{ signal: lease\?\.signal \}\)/);
  assert.match(contentSource, /Repair was interrupted; its durable journal will block and reconcile/);
  assert.doesNotMatch(contentSource, /Repair failed without changing saved picks/);
  assert.match(popupSource, /operationLock\.run\(sessionKey, async \(lease\)/);
  assert.match(popupSource, /resetDraftSession\(session, \{ signal: lease\?\.signal \}\)/);
  assert.match(popupSource, /finalizeReset\(sessionKey, resetAt, lease\)/);
});

test('popup requires full active identity including team before reset', () => {
  const popupSource = fs.readFileSync(path.join(__dirname, '..', 'popup.js'), 'utf8');

  assert.match(popupSource, /sameDraftIdentity\(session, diagnostics\)/);
  assert.match(popupSource, /sameDraftIdentity\(exactSession, activeDiagnostics\)/);
  assert.match(popupSource, /sameDraftIdentity\(pending\.draft, activeIdentity\)/);
});
