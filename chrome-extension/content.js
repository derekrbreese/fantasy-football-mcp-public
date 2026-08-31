(function startYahooDraftRecorder() {
  'use strict';

  const STORAGE_KEY = 'yahooDraftRecorderSessions';
  const extensionApi = YahooDraftWebExtension.createWebExtensionApi(globalThis);
  const webext = extensionApi.native;
  const metadata = YahooDraftParser.parseDraftUrl(window.location.href);
  if (!metadata) return;

  let scanTimer;
  let scanInProgress = null;
  let lastSyncedSignature;
  let lastSyncAttemptAt = 0;
  const diagnostics = {
    sessionKey: metadata.sessionKey,
    lastScanAt: null,
    candidateCount: 0,
    parsedCount: 0,
    ledgerCandidateCount: 0,
    recordedCount: 0,
    syncStatus: 'not-attempted',
    lastSyncAt: null,
    syncError: null,
  };

  async function getStorage() {
    const result = await extensionApi.storageGet(STORAGE_KEY);
    return result[STORAGE_KEY] || {};
  }

  function setStorage(sessions) {
    return extensionApi.storageSet({ [STORAGE_KEY]: sessions });
  }

  async function syncSession(session) {
    const signature = JSON.stringify([session.sessionKey, session.picks]);
    const now = Date.now();
    if (signature === lastSyncedSignature && diagnostics.syncStatus === 'connected') return;
    if (signature === lastSyncedSignature && now - lastSyncAttemptAt < 10000) return;

    lastSyncedSignature = signature;
    lastSyncAttemptAt = now;
    diagnostics.syncStatus = 'connecting';
    diagnostics.syncError = null;
    try {
      const context = YahooDraftAgentContext.sessionToAgentContext(session);
      await YahooDraftSyncClient.syncDraftContext(context);
      diagnostics.syncStatus = 'connected';
      diagnostics.lastSyncAt = new Date().toISOString();
    } catch (error) {
      diagnostics.syncStatus = 'unavailable';
      diagnostics.syncError = error?.name === 'AbortError' ? 'Connection timed out' : String(error?.message || error);
    }
  }

  async function performScan() {
    const now = new Date().toISOString();
    const snapshots = YahooDraftDomScanner.findPickSnapshots(document);
    const ledgerSnapshots = YahooDraftDomScanner.findRoundByRoundSnapshots(document);
    const picks = [
      ...snapshots.map((snapshot) => YahooDraftParser.parsePickSnapshot(snapshot)),
      ...ledgerSnapshots.map((snapshot) => YahooDraftParser.parseRoundByRoundSnapshot(snapshot)),
    ].filter(Boolean);
    const liveSnapshot = YahooDraftDomScanner.findLiveDraftSnapshot(document);
    const livePick = liveSnapshot
      ? YahooDraftParser.parseLiveDraftSnapshot(liveSnapshot)
      : null;
    if (livePick) picks.push(livePick);

    diagnostics.lastScanAt = now;
    diagnostics.ledgerCandidateCount = ledgerSnapshots.length;
    diagnostics.candidateCount = snapshots.length + ledgerSnapshots.length + (liveSnapshot ? 1 : 0);
    diagnostics.parsedCount = picks.length;

    const sessions = await getStorage();
    const existing = sessions[metadata.sessionKey];
    const updated = YahooDraftSessionStore.updateDraftSession(existing, metadata, picks, now);
    diagnostics.recordedCount = updated.picks.length;

    if (JSON.stringify(existing?.picks || []) !== JSON.stringify(updated.picks)) {
      sessions[metadata.sessionKey] = updated;
      await setStorage(sessions);
    }

    await syncSession(updated);
    return { ...diagnostics };
  }

  function scanNow() {
    if (!scanInProgress) {
      scanInProgress = performScan()
        .catch((error) => {
          console.warn('[Yahoo Draft Recorder] Scan failed:', error);
          return { ...diagnostics, error: error.message };
        })
        .finally(() => {
          scanInProgress = null;
        });
    }
    return scanInProgress;
  }

  function scheduleScan() {
    window.clearTimeout(scanTimer);
    scanTimer = window.setTimeout(scanNow, 400);
  }

  const observer = new MutationObserver(scheduleScan);
  observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true });

  webext.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === 'YAHOO_DRAFT_RECORDER_STATUS') {
      sendResponse({ ...diagnostics });
      return false;
    }
    if (message?.type === 'YAHOO_DRAFT_RECORDER_RESCAN') {
      scanNow().then(sendResponse);
      return true;
    }
    if (message?.type === 'YAHOO_DRAFT_RECORDER_DIAGNOSTICS') {
      sendResponse({
        generatedAt: new Date().toISOString(),
        session: {
          sport: metadata.sport,
          leagueId: metadata.leagueId,
          teamId: metadata.teamId,
        },
        scanner: { ...diagnostics },
        elements: YahooDraftDomScanner.collectDiagnosticSnapshots(document),
      });
      return false;
    }
    return false;
  });

  scanNow();
})();
