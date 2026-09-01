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

test('automatic authoritative rollback exits before persistence and sync', () => {
  const contentSource = fs.readFileSync(path.join(__dirname, '..', 'content.js'), 'utf8');
  const guardStart = contentSource.indexOf('if (!automaticUpdate.ok)');
  const acceptedUpdate = contentSource.indexOf('updated = automaticUpdate.session;', guardStart);
  const guardedBranch = contentSource.slice(guardStart, acceptedUpdate);

  assert.ok(guardStart > 0);
  assert.match(guardedBranch, /return \{ \.\.\.diagnostics, error: automaticUpdate\.error \};/);
  assert.doesNotMatch(guardedBranch, /setSession|syncSession/);
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

test('popup requires full active identity including team before reset', () => {
  const popupSource = fs.readFileSync(path.join(__dirname, '..', 'popup.js'), 'utf8');

  assert.match(popupSource, /sameDraftIdentity\(session, diagnostics\)/);
  assert.match(popupSource, /sameDraftIdentity\(exactSession, activeDiagnostics\)/);
  assert.match(popupSource, /sameDraftIdentity\(pending\.draft, activeIdentity\)/);
});
