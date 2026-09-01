(function startYahooDraftRecorder() {
  'use strict';

  const extensionApi = YahooDraftWebExtension.createWebExtensionApi(globalThis);
  const webext = extensionApi.native;
  const operationLock = YahooDraftStorage.createSessionOperationLock(webext.runtime);
  const draftStorage = YahooDraftStorage.createDraftStorage(extensionApi, { operationLock });
  const metadata = YahooDraftParser.parseDraftUrl(window.location.href);
  if (!metadata) return;

  const contentLoadedAt = new Date().toISOString();
  let scanTimer;
  let scanInProgress = null;
  let lastSyncedSignature;
  let lastSyncAttemptAt = 0;
  let allowedResetAt = null;
  const diagnostics = {
    sport: metadata.sport,
    leagueId: metadata.leagueId,
    teamId: metadata.teamId,
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

  async function leaseAwait(lease, operation) {
    lease?.throwIfLost?.();
    const result = await operation();
    lease?.throwIfLost?.();
    return result;
  }

  async function blockForPendingReset(lease) {
    const pending = await leaseAwait(
      lease,
      () => draftStorage.getPendingReset(metadata.sessionKey),
    );
    if (!pending) return null;
    const error = 'A confirmed mock-draft reset is pending reconciliation; scans, repairs, and sync are blocked.';
    diagnostics.syncStatus = 'reset-pending';
    diagnostics.syncError = error;
    return { ...diagnostics, ok: false, error };
  }

  async function blockForPreResetTab(lease) {
    const resetAt = await leaseAwait(
      lease,
      () => draftStorage.getResetAt(metadata.sessionKey),
    );
    if (!YahooDraftStorage.isTabBlockedByReset(contentLoadedAt, resetAt, allowedResetAt)) return null;
    const error = 'This Yahoo tab was open before the mock-draft reset. Reload it, or explicitly rescan it from the recorder popup, before recording resumes.';
    diagnostics.syncStatus = 'reset-reload-required';
    diagnostics.syncError = error;
    return { ...diagnostics, ok: false, error };
  }

  async function persistSuccessfulOrdinarySync(session, snapshotTimestamp, lease) {
    const resetBlock = await blockForPendingReset(lease);
    if (resetBlock) return;
    const resetAt = await leaseAwait(
      lease,
      () => draftStorage.getResetAt(metadata.sessionKey),
    );
    if (resetAt && Date.parse(snapshotTimestamp) <= Date.parse(resetAt)) return;
    const current = await leaseAwait(
      lease,
      () => draftStorage.getSession(metadata.sessionKey),
    );
    if (current && !YahooDraftSessionStore.sameDraftIdentity(current, metadata)) return;
    const currentTime = Date.parse(current?.updatedAt);
    const snapshotTime = Date.parse(session.updatedAt);
    if (Number.isFinite(currentTime) && Number.isFinite(snapshotTime) && currentTime > snapshotTime) return;
    await leaseAwait(
      lease,
      () => draftStorage.setSession(metadata.sessionKey, {
        ...session,
        lastSyncedAt: snapshotTimestamp,
      }),
    );
  }

  async function isSessionReset(session, lease) {
    const resetAt = await leaseAwait(
      lease,
      () => draftStorage.getResetAt(metadata.sessionKey),
    );
    const sessionTime = Date.parse(session?.updatedAt);
    const resetTime = Date.parse(resetAt);
    return Number.isFinite(sessionTime) && Number.isFinite(resetTime) && sessionTime <= resetTime;
  }

  async function syncSession(session, options = {}, lease) {
    const isRepair = options.repair === true;
    const resetBlock = await blockForPendingReset(lease);
    if (resetBlock) {
      if (options.requireSuccess) throw new Error(resetBlock.error);
      return null;
    }
    const oldTabBlock = await blockForPreResetTab(lease);
    if (oldTabBlock) {
      if (options.requireSuccess) throw new Error(oldTabBlock.error);
      return null;
    }
    if (!isRepair && await leaseAwait(
      lease,
      () => draftStorage.getPendingRepair(metadata.sessionKey),
    )) {
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
      const response = await leaseAwait(
        lease,
        () => YahooDraftSyncClient.syncDraftContext(context, { signal: lease?.signal }),
      );
      if (!isRepair) {
        await persistSuccessfulOrdinarySync(session, snapshotTimestamp, lease);
      }
      diagnostics.syncStatus = 'connected';
      diagnostics.lastSyncAt = new Date().toISOString();
      return response;
    } catch (error) {
      lease?.throwIfLost?.();
      diagnostics.syncStatus = 'unavailable';
      diagnostics.syncError = error?.name === 'AbortError' ? 'Connection timed out' : String(error?.message || error);
      if (options.requireSuccess) throw error;
      return null;
    }
  }

  function repairCoordinatorForLease(lease) {
    return YahooDraftSessionStore.createDurableRepairCoordinator({
      readPending: () => leaseAwait(
        lease,
        () => draftStorage.getPendingRepair(metadata.sessionKey),
      ),
      writePending: (record) => leaseAwait(
        lease,
        () => draftStorage.setPendingRepair(metadata.sessionKey, record),
      ),
      clearPending: () => leaseAwait(
        lease,
        () => draftStorage.clearPendingRepair(metadata.sessionKey),
      ),
      isSessionReset: (session) => isSessionReset(session, lease),
      syncRepair: async (session) => {
        lastSyncedSignature = undefined;
        await syncSession(session, { repair: true, requireSuccess: true }, lease);
      },
      persistSession: (session) => leaseAwait(
        lease,
        () => draftStorage.setSession(metadata.sessionKey, session),
      ),
    });
  }

  async function performScan(lease) {
    lease?.throwIfLost?.();
    const resetBlock = await blockForPendingReset(lease);
    if (resetBlock) return resetBlock;
    const oldTabBlock = await blockForPreResetTab(lease);
    if (oldTabBlock) return oldTabBlock;
    const repairCoordinator = repairCoordinatorForLease(lease);
    const reconciliation = await leaseAwait(lease, () => repairCoordinator.reconcile());
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

    lease?.throwIfLost?.();
    const existing = await leaseAwait(
      lease,
      () => draftStorage.getSession(metadata.sessionKey),
    );
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

    const resetBeforeWrite = await blockForPendingReset(lease);
    if (resetBeforeWrite) return resetBeforeWrite;
    if (JSON.stringify(existing?.picks || []) !== JSON.stringify(updated.picks)) {
      await leaseAwait(
        lease,
        () => draftStorage.setSession(metadata.sessionKey, updated),
      );
    }

    await syncSession(updated, {}, lease);
    lease?.throwIfLost?.();
    return { ...diagnostics };
  }

  async function performRepair(lease) {
    lease?.throwIfLost?.();
    const resetBlock = await blockForPendingReset(lease);
    if (resetBlock) return resetBlock;
    const oldTabBlock = await blockForPreResetTab(lease);
    if (oldTabBlock) return oldTabBlock;
    const repairCoordinator = repairCoordinatorForLease(lease);
    const reconciliation = await leaseAwait(lease, () => repairCoordinator.reconcile());
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

    lease?.throwIfLost?.();
    const existing = await leaseAwait(
      lease,
      () => draftStorage.getSession(metadata.sessionKey),
    );
    const staged = YahooDraftSessionStore.prepareDraftRepair(
      existing,
      metadata,
      authoritativePicks,
      now,
      { currentPickNumber },
    );
    if (!staged.ok) return staged;

    const resetBeforeIntent = await blockForPendingReset(lease);
    if (resetBeforeIntent) return resetBeforeIntent;
    try {
      await leaseAwait(
        lease,
        () => repairCoordinator.begin(metadata.sessionKey, staged.session),
      );
    } catch (error) {
      return {
        ok: false,
        session: existing,
        health: staged.health,
        error: `Repair could not be staged safely in browser storage: ${String(error?.message || error)}`,
      };
    }
    const repairResult = await leaseAwait(lease, () => repairCoordinator.reconcile());
    if (repairResult.discardedByReset) {
      return {
        ok: false,
        error: 'This repair was superseded by a completed mock-draft reset. Reload or explicitly rescan this Yahoo tab.',
      };
    }
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
    lease?.throwIfLost?.();
    return {
      ok: true,
      repairedCount: staged.session.picks.length,
      health: staged.health,
      syncStatus: diagnostics.syncStatus,
    };
  }

  function scanNow(options = {}) {
    if (options.forceSync === true) {
      lastSyncedSignature = undefined;
      diagnostics.syncStatus = 'not-attempted';
      const precedingOperation = scanInProgress;
      const operation = (precedingOperation
        ? precedingOperation.catch(() => undefined)
        : Promise.resolve())
        .then(() => operationLock.run(metadata.sessionKey, performScan))
        .catch((error) => {
          console.warn('[Yahoo Draft Recorder] Scan failed:', error);
          return { ...diagnostics, error: error.message };
        })
        .finally(() => {
          if (scanInProgress === operation) scanInProgress = null;
        });
      scanInProgress = operation;
      return operation;
    }
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

  async function rescanFromMessage(message) {
    if (message?.resetAt) {
      const storedResetAt = await draftStorage.getResetAt(metadata.sessionKey);
      if (
        storedResetAt === message.resetAt &&
        Number.isFinite(Date.parse(storedResetAt)) &&
        Date.now() > Date.parse(storedResetAt)
      ) {
        allowedResetAt = storedResetAt;
      }
    }
    return scanNow({ forceSync: message?.forceSync === true });
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
      rescanFromMessage(message).then(sendResponse);
      return true;
    }
    if (message?.type === 'YAHOO_DRAFT_RECORDER_REPAIR') {
      repairNow().then(sendResponse).catch((error) => sendResponse({
        ok: false,
        error: `Repair was interrupted; its durable journal will block and reconcile before further scanning or sync: ${String(error?.message || error)}`,
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
