(function startYahooDraftRecorder() {
  'use strict';

  const extensionApi = YahooDraftWebExtension.createWebExtensionApi(globalThis);
  const webext = extensionApi.native;
  const lockManager = globalThis.navigator?.locks;
  const draftStorage = YahooDraftStorage.createDraftStorage(extensionApi, { lockManager });
  const operationLock = YahooDraftStorage.createSessionOperationLock(lockManager);
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
    authoritativeLedgerHealth: null,
    authoritativeLedgerError: null,
    syncStatus: 'not-attempted',
    lastSyncAt: null,
    syncError: null,
  };

  async function syncSession(session, options = {}) {
    const isRepair = options.repair === true;
    if (!isRepair && await draftStorage.getPendingRepair(metadata.sessionKey)) {
      diagnostics.syncStatus = 'repair-pending-local-save';
      diagnostics.syncError = 'A durable repair is pending reconciliation; ordinary sync is blocked.';
      return null;
    }
    const signature = JSON.stringify([session.sessionKey, session.picks, isRepair]);
    const now = Date.now();
    if (!options.requireSuccess && signature === lastSyncedSignature && diagnostics.syncStatus === 'connected') return;
    if (!options.requireSuccess && signature === lastSyncedSignature && now - lastSyncAttemptAt < 10000) return;

    lastSyncedSignature = signature;
    lastSyncAttemptAt = now;
    diagnostics.syncStatus = 'connecting';
    diagnostics.syncError = null;
    try {
      const snapshotTimestamp = YahooDraftAgentContext.validIsoTimestamp(session.updatedAt);
      if (!snapshotTimestamp) throw new Error('Draft snapshot has no valid updatedAt timestamp; sync was blocked.');
      const context = YahooDraftAgentContext.sessionToAgentContext(
        session,
        snapshotTimestamp,
        { repair: isRepair },
      );
      const response = await YahooDraftSyncClient.syncDraftContext(context);
      diagnostics.syncStatus = 'connected';
      diagnostics.lastSyncAt = new Date().toISOString();
      return response;
    } catch (error) {
      diagnostics.syncStatus = 'unavailable';
      diagnostics.syncError = error?.name === 'AbortError' ? 'Connection timed out' : String(error?.message || error);
      if (options.requireSuccess) throw error;
      return null;
    }
  }

  const repairCoordinator = YahooDraftSessionStore.createDurableRepairCoordinator({
    readPending: () => draftStorage.getPendingRepair(metadata.sessionKey),
    writePending: (record) => draftStorage.setPendingRepair(metadata.sessionKey, record),
    clearPending: () => draftStorage.clearPendingRepair(metadata.sessionKey),
    syncRepair: async (session) => {
      lastSyncedSignature = undefined;
      await syncSession(session, { repair: true, requireSuccess: true });
    },
    persistSession: (session) => draftStorage.setSession(metadata.sessionKey, session),
  });

  async function performScan() {
    const reconciliation = await repairCoordinator.reconcile();
    if (!reconciliation.ok) {
      diagnostics.syncStatus = 'repair-pending-local-save';
      diagnostics.syncError = reconciliation.error;
      return { ...diagnostics, error: reconciliation.error };
    }
    const now = new Date().toISOString();
    const snapshots = YahooDraftDomScanner.findPickSnapshots(document);
    const ledgerSnapshots = YahooDraftDomScanner.findRoundByRoundSnapshots(document);
    const authoritativeScan = YahooDraftDomScanner.scanAuthoritativeRoundByRoundTables(document);
    const parsedAuthoritativePicks = authoritativeScan.ok
      ? authoritativeScan.snapshots
        .map((snapshot) => YahooDraftParser.parseRoundByRoundSnapshot(snapshot))
        .filter(Boolean)
      : [];
    const authoritativeEvaluation = YahooDraftLedgerHealth.evaluateAuthoritativeLedgerScan(
      authoritativeScan,
      parsedAuthoritativePicks,
    );
    diagnostics.authoritativeLedgerHealth = authoritativeEvaluation.health;
    diagnostics.authoritativeLedgerError = authoritativeEvaluation.error;

    const nonLedgerPicks = snapshots
      .map((snapshot) => YahooDraftParser.parsePickSnapshot(snapshot))
      .filter(Boolean);
    const ledgerPicks = ledgerSnapshots
      .map((snapshot) => YahooDraftParser.parseRoundByRoundSnapshot(snapshot))
      .filter(Boolean);
    const liveSnapshot = YahooDraftDomScanner.findLiveDraftSnapshot(document);
    const livePick = liveSnapshot
      ? YahooDraftParser.parseLiveDraftSnapshot(liveSnapshot)
      : null;
    if (livePick) nonLedgerPicks.push(livePick);
    const picks = [...nonLedgerPicks, ...ledgerPicks];

    diagnostics.lastScanAt = now;
    diagnostics.ledgerCandidateCount = ledgerSnapshots.length;
    diagnostics.candidateCount = snapshots.length + ledgerSnapshots.length + (liveSnapshot ? 1 : 0);
    diagnostics.parsedCount = picks.length;

    const existing = await draftStorage.getSession(metadata.sessionKey);
    let updated;
    if (authoritativeEvaluation.authoritativePicks) {
      const automaticUpdate = YahooDraftSessionStore.prepareAutomaticAuthoritativeUpdate(
        existing,
        metadata,
        authoritativeEvaluation.authoritativePicks,
        nonLedgerPicks,
        now,
        { currentPickNumber: YahooDraftDomScanner.findCurrentPickNumber(document) },
      );
      if (!automaticUpdate.ok) {
        diagnostics.recordedCount = existing?.picks?.length || 0;
        diagnostics.authoritativeLedgerError = automaticUpdate.error;
        diagnostics.syncStatus = 'blocked-authoritative-prefix';
        diagnostics.syncError = automaticUpdate.error;
        return { ...diagnostics, error: automaticUpdate.error };
      }
      updated = automaticUpdate.session;
    } else {
      updated = YahooDraftSessionStore.updateDraftSession(existing, metadata, picks, now);
    }
    diagnostics.recordedCount = updated.picks.length;

    if (JSON.stringify(existing?.picks || []) !== JSON.stringify(updated.picks)) {
      await draftStorage.setSession(metadata.sessionKey, updated);
    }

    await syncSession(updated);
    return { ...diagnostics };
  }

  async function performRepair() {
    const reconciliation = await repairCoordinator.reconcile();
    if (!reconciliation.ok) return reconciliation;
    const now = new Date().toISOString();
    const ledgerScan = YahooDraftDomScanner.scanAuthoritativeRoundByRoundTables(document);
    if (!ledgerScan.ok) return ledgerScan;
    const authoritativePicks = ledgerScan.snapshots
      .map((snapshot) => YahooDraftParser.parseRoundByRoundSnapshot(snapshot))
      .filter(Boolean);
    if (authoritativePicks.length !== ledgerScan.apparentRowCount) {
      return { ok: false, error: `Yahoo showed ${ledgerScan.apparentRowCount} apparent completed ledger rows, but only ${authoritativePicks.length} parsed safely. Saved picks were not changed.` };
    }

    const health = YahooDraftLedgerHealth.analyzeLedger(authoritativePicks);
    const currentPickNumber = YahooDraftDomScanner.findCurrentPickNumber(document);
    const currentPickValidation = YahooDraftLedgerHealth.validateLedgerAgainstCurrentPick(
      health,
      currentPickNumber,
    );
    if (!currentPickValidation.ok) return { ...currentPickValidation, health };

    const existing = await draftStorage.getSession(metadata.sessionKey);
    const staged = YahooDraftSessionStore.prepareDraftRepair(
      existing,
      metadata,
      authoritativePicks,
      now,
      { currentPickNumber },
    );
    if (!staged.ok) return staged;

    try {
      await repairCoordinator.begin(metadata.sessionKey, staged.session);
    } catch (error) {
      return {
        ok: false,
        session: existing,
        health: staged.health,
        error: `Repair could not be staged safely in browser storage: ${String(error?.message || error)}`,
      };
    }
    const repairResult = await repairCoordinator.reconcile();
    if (!repairResult.ok) {
      diagnostics.syncStatus = 'repair-pending-local-save';
      diagnostics.syncError = repairResult.error;
      return { ...repairResult, session: existing, health: staged.health };
    }

    diagnostics.lastScanAt = now;
    diagnostics.ledgerCandidateCount = ledgerScan.apparentRowCount;
    diagnostics.candidateCount = ledgerScan.apparentRowCount;
    diagnostics.parsedCount = authoritativePicks.length;
    diagnostics.recordedCount = staged.session.picks.length;
    diagnostics.authoritativeLedgerHealth = YahooDraftLedgerHealth.summarizeNumberedLedgerHealth(
      staged.health,
    );
    diagnostics.authoritativeLedgerError = null;
    return {
      ok: true,
      repairedCount: staged.session.picks.length,
      health: staged.health,
      syncStatus: diagnostics.syncStatus,
    };
  }

  function scanNow() {
    if (!scanInProgress) {
      const operation = operationLock.run(metadata.sessionKey, performScan)
        .catch((error) => {
          console.warn('[Yahoo Draft Recorder] Scan failed:', error);
          return { ...diagnostics, error: error.message };
        })
        .finally(() => {
          if (scanInProgress === operation) scanInProgress = null;
        });
      scanInProgress = operation;
    }
    return scanInProgress;
  }

  function repairNow() {
    const precedingOperation = scanInProgress;
    const operation = (precedingOperation
      ? precedingOperation.catch(() => undefined)
      : Promise.resolve())
      .then(() => operationLock.run(metadata.sessionKey, performRepair))
      .finally(() => {
        if (scanInProgress === operation) scanInProgress = null;
      });
    scanInProgress = operation;
    return operation;
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
    if (message?.type === 'YAHOO_DRAFT_RECORDER_REPAIR') {
      repairNow().then(sendResponse).catch((error) => sendResponse({
        ok: false,
        error: `Repair failed without changing saved picks: ${String(error?.message || error)}`,
      }));
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
        scanner: {
          candidateCount: diagnostics.candidateCount,
          parsedCount: diagnostics.parsedCount,
          ledgerCandidateCount: diagnostics.ledgerCandidateCount,
          recordedCount: diagnostics.recordedCount,
          syncStatus: diagnostics.syncStatus,
          hasCompletedScan: Boolean(diagnostics.lastScanAt),
          hasCompletedSync: Boolean(diagnostics.lastSyncAt),
          authoritativeLedgerHealth: diagnostics.authoritativeLedgerHealth,
          hasAuthoritativeLedgerError: Boolean(diagnostics.authoritativeLedgerError),
        },
        elements: YahooDraftDomScanner.collectDiagnosticSnapshots(document),
      });
      return false;
    }
    return false;
  });

  scanNow();
})();
