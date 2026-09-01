(function initPopup() {
  'use strict';

  const extensionApi = YahooDraftWebExtension.createWebExtensionApi(globalThis);
  const webext = extensionApi.native;
  const lockManager = globalThis.navigator?.locks;
  const draftStorage = YahooDraftStorage.createDraftStorage(extensionApi, { lockManager });
  const operationLock = YahooDraftStorage.createSessionOperationLock(lockManager);
  const elements = {
    indicator: document.querySelector('#recording-indicator'),
    draftName: document.querySelector('#draft-name'),
    count: document.querySelector('#pick-count'),
    status: document.querySelector('#status'),
    tbody: document.querySelector('#picks'),
    empty: document.querySelector('#empty-state'),
    ledgerHealth: document.querySelector('#ledger-health'),
    ledgerIssues: document.querySelector('#ledger-issues'),
    assistant: document.querySelector('#open-assistant'),
    dashboard: document.querySelector('#open-dashboard'),
    rescan: document.querySelector('#rescan'),
    repair: document.querySelector('#repair'),
    diagnostics: document.querySelector('#diagnostics'),
    csv: document.querySelector('#export-csv'),
    json: document.querySelector('#export-json'),
    reset: document.querySelector('#reset'),
  };

  let activeTabId;
  let activeSessionKey;
  let activeDiagnostics;
  let currentSession;
  let resetReconciliationInProgress = null;

  elements.assistant.hidden = typeof webext.sidebarAction?.open !== 'function';

  async function readSessions() {
    return draftStorage.listSessions();
  }

  async function getActiveTab() {
    const tabs = await extensionApi.queryTabs({ active: true, currentWindow: true });
    return tabs[0];
  }

  async function sendToActiveTab(type, details = {}) {
    if (!activeTabId) return null;
    try {
      return await extensionApi.sendTabMessage(activeTabId, { type, ...details });
    } catch (_error) {
      return null;
    }
  }

  function setStatus(message, kind = '') {
    elements.status.textContent = message;
    elements.status.className = `status ${kind}`.trim();
  }

  function appendCell(row, text, className = '') {
    const cell = document.createElement('td');
    cell.textContent = text || '—';
    if (className) cell.className = className;
    row.appendChild(cell);
  }

  function resetRevision(session) {
    return session?.lastSyncedAt === undefined ? session?.updatedAt : session.lastSyncedAt;
  }

  function render(session, diagnostics) {
    currentSession = session;
    const picks = session?.picks || [];
    const isLive = Boolean(diagnostics?.sessionKey);
    const activeLeagueId = isLive && session?.sessionKey === diagnostics.sessionKey
      ? session?.leagueId
      : null;

    elements.indicator.classList.toggle('live', isLive);
    elements.indicator.title = isLive ? 'Watching this Yahoo draft' : 'No Yahoo draft detected';
    elements.draftName.textContent = session?.leagueId ? `League ${session.leagueId}` : 'Not detected';
    elements.count.textContent = String(picks.length);
    elements.tbody.replaceChildren();

    const savedHealth = YahooDraftLedgerHealth.analyzeLedger(picks);
    const health = YahooDraftLedgerHealth.mergeVisibleLedgerHealth(
      diagnostics?.authoritativeLedgerHealth,
      savedHealth,
    );
    const issues = YahooDraftLedgerHealth.formatLedgerIssues(health);
    elements.ledgerHealth.hidden = !issues;
    elements.ledgerIssues.textContent = issues;

    for (const pick of [...picks].reverse()) {
      const row = document.createElement('tr');
      if (pick.isUserPick === true || /^Your Team$/i.test(pick.fantasyTeam || '')) row.classList.add('user-pick');
      appendCell(row, String(pick.pickNumber || ''));

      const playerCell = document.createElement('td');
      const player = document.createElement('div');
      player.className = 'player';
      player.textContent = pick.player || 'Unknown player';
      playerCell.appendChild(player);
      if (pick.roundNumber) {
        const round = document.createElement('div');
        round.className = 'meta';
        round.textContent = `Round ${pick.roundNumber}${pick.roundPick ? `, pick ${pick.roundPick}` : ''}`;
        playerCell.appendChild(round);
      }
      row.appendChild(playerCell);

      appendCell(row, [pick.nflTeam, pick.position].filter(Boolean).join(' · '));
      appendCell(row, pick.fantasyTeam || '');
      elements.tbody.appendChild(row);
    }

    elements.empty.hidden = picks.length > 0;
    elements.csv.disabled = picks.length === 0;
    elements.json.disabled = picks.length === 0;
    const canResetActiveSession = isLive &&
      YahooDraftSessionStore.sameDraftIdentity(session, diagnostics) &&
      YahooDraftSyncClient.validIsoTimestamp(resetRevision(session));
    elements.reset.disabled = !canResetActiveSession;
    elements.rescan.disabled = !isLive;
    elements.repair.disabled = !isLive;
    elements.diagnostics.disabled = !isLive;
    elements.dashboard.href = activeLeagueId && /^\d{1,32}$/.test(activeLeagueId)
      ? `http://127.0.0.1:8765/draft-dashboard#leagueId=${encodeURIComponent(activeLeagueId)}`
      : 'http://127.0.0.1:8765/draft-dashboard';

    if (!isLive) {
      setStatus(session ? 'Showing a saved draft. Open its Yahoo draft page to resume recording.' : 'Open a Yahoo live draft to begin recording.');
    } else if (diagnostics.error) {
      setStatus(`Recorder error: ${diagnostics.error}`, 'error');
    } else if (diagnostics.authoritativeLedgerError) {
      setStatus(`Authoritative ledger error: ${diagnostics.authoritativeLedgerError}`, 'error');
    } else if (diagnostics.candidateCount > 0 && diagnostics.parsedCount === 0) {
      setStatus('Yahoo rows were found, but their fields could not be parsed. The page layout may have changed.', 'warning');
    } else if (picks.length === 0) {
      setStatus('Watching this draft. Picks will appear here as Yahoo posts them.');
    } else {
      const syncLabel = diagnostics.syncStatus === 'connected'
        ? 'agent sync connected'
        : 'agent sync offline; Agent JSON is available';
      setStatus(`Recording automatically · ${syncLabel} · last scan ${new Date(diagnostics.lastScanAt).toLocaleTimeString()}`);
    }
  }

  function latestSession(sessions) {
    return Object.values(sessions).sort((left, right) => String(right.updatedAt || '').localeCompare(String(left.updatedAt || '')))[0];
  }

  function repairCoordinatorFor(sessionKey) {
    return YahooDraftSessionStore.createDurableRepairCoordinator({
      readPending: () => draftStorage.getPendingRepair(sessionKey),
      writePending: (record) => draftStorage.setPendingRepair(sessionKey, record),
      clearPending: () => draftStorage.clearPendingRepair(sessionKey),
      isSessionReset: async (session) => {
        const resetAt = await draftStorage.getResetAt(sessionKey);
        const sessionTime = Date.parse(session?.updatedAt);
        const resetTime = Date.parse(resetAt);
        return Number.isFinite(sessionTime) && Number.isFinite(resetTime) && sessionTime <= resetTime;
      },
      syncRepair: async (session) => {
        const timestamp = YahooDraftAgentContext.validIsoTimestamp(session?.updatedAt);
        if (!timestamp) throw new Error('Pending repair has no valid snapshot timestamp.');
        const context = YahooDraftAgentContext.sessionToAgentContext(
          session,
          timestamp,
          { repair: true },
        );
        await YahooDraftSyncClient.syncDraftContext(context);
      },
      persistSession: (session) => draftStorage.setSession(sessionKey, session),
    });
  }

  function resetCoordinatorFor(sessionKey) {
    return YahooDraftSessionStore.createDurableResetCoordinator({
      readPending: () => draftStorage.getPendingReset(sessionKey),
      writePending: (record) => draftStorage.setPendingReset(sessionKey, record),
      clearPending: () => draftStorage.clearPendingReset(sessionKey),
      resetServer: (session) => YahooDraftSyncClient.resetDraftSession(session),
      finalizeReset: (exactSessionKey, resetAt) => {
        if (exactSessionKey !== sessionKey) throw new Error('Reset acknowledgement changed leagues.');
        return draftStorage.finalizeReset(sessionKey, resetAt);
      },
    });
  }

  async function reconcilePendingReset(sessionKey, activeIdentity) {
    if (!sessionKey) return null;
    const pending = await draftStorage.getPendingReset(sessionKey);
    if (!pending) return null;
    if (!YahooDraftSessionStore.sameDraftIdentity(pending.draft, activeIdentity)) {
      return {
        ok: false,
        error: 'A pending reset belongs to a different Yahoo team. Open that exact draft tab to resume it.',
      };
    }
    if (!resetReconciliationInProgress) {
      resetReconciliationInProgress = operationLock
        .run(sessionKey, () => resetCoordinatorFor(sessionKey).reconcile())
        .finally(() => { resetReconciliationInProgress = null; });
    }
    return resetReconciliationInProgress;
  }

  async function refresh(options = {}) {
    let [sessions, diagnostics] = await Promise.all([
      readSessions(),
      sendToActiveTab('YAHOO_DRAFT_RECORDER_STATUS'),
    ]);
    activeSessionKey = diagnostics?.sessionKey || null;
    activeDiagnostics = diagnostics || null;
    let resumedReset = null;
    if (options.resumeReset !== false && activeSessionKey) {
      resumedReset = await reconcilePendingReset(activeSessionKey, diagnostics);
      if (resumedReset?.ok) sessions = await readSessions();
    }
    const saved = activeSessionKey ? sessions[activeSessionKey] : latestSession(sessions);
    const session = saved || (diagnostics ? {
      sport: diagnostics.sport,
      leagueId: diagnostics.leagueId,
      teamId: diagnostics.teamId,
      sessionKey: diagnostics.sessionKey,
      picks: [],
    } : null);
    render(session, diagnostics);
    if (resumedReset?.ok) {
      setStatus('Reset reconciled safely. The imported profile was preserved; rescan this Yahoo page to begin recording again. Close older tabs for this mock draft.', 'warning');
    } else if (resumedReset && !resumedReset.ok) {
      setStatus(resumedReset.error || 'Reset is still pending; retry Reset after checking the local server.', 'error');
    }
    return { diagnostics, resumedReset, session };
  }

  function download(content, extension, mimeType) {
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    const league = currentSession?.leagueId || 'draft';
    anchor.href = url;
    anchor.download = `yahoo-draft-${league}-${new Date().toISOString().slice(0, 10)}.${extension}`;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  elements.rescan.addEventListener('click', async () => {
    setStatus('Scanning the Yahoo draft page…');
    const resetAt = activeSessionKey
      ? await draftStorage.getResetAt(activeSessionKey)
      : null;
    await sendToActiveTab('YAHOO_DRAFT_RECORDER_RESCAN', {
      forceSync: true,
      ...(resetAt ? { resetAt } : {}),
    });
    await refresh();
  });

  elements.assistant.addEventListener('click', () => {
    try {
      const opening = webext.sidebarAction?.open?.();
      if (opening === undefined && typeof webext.sidebarAction?.open !== 'function') {
        setStatus('The persistent sidebar is unavailable in this browser. Use Full dashboard instead.', 'warning');
        return;
      }
      Promise.resolve(opening)
        .then(() => window.close())
        .catch((error) => setStatus(`Could not open Draft Assistant: ${String(error?.message || error)}`, 'error'));
    } catch (error) {
      setStatus(`Could not open Draft Assistant: ${String(error?.message || error)}`, 'error');
    }
  });

  elements.repair.addEventListener('click', async () => {
    if (!window.confirm('Replace this draft’s saved picks with the complete numbered ledger currently visible in Results → Round by Round? Saved picks will remain unchanged if that ledger is incomplete.')) return;
    setStatus('Validating the full Round-by-Round ledger…');
    const result = await sendToActiveTab('YAHOO_DRAFT_RECORDER_REPAIR');
    if (!result?.ok) {
      await refresh();
      setStatus(
        YahooDraftLedgerHealth.formatRepairFailure(result?.error, result?.health) || 'Repair could not find a complete Round-by-Round ledger. Open Results → Round by Round and try again.',
        'error',
      );
      return;
    }
    await refresh();
    setStatus(`Repair complete: ${result.repairedCount} contiguous numbered picks saved and synced.`);
  });

  elements.diagnostics.addEventListener('click', async () => {
    setStatus('Collecting sanitized Yahoo row diagnostics…');
    const report = await sendToActiveTab('YAHOO_DRAFT_RECORDER_DIAGNOSTICS');
    if (!report) {
      setStatus('Could not collect diagnostics from the active Yahoo draft tab.', 'error');
      return;
    }
    download(`${JSON.stringify(report, null, 2)}\n`, 'diagnostics.json', 'application/json');
    setStatus('Diagnostics saved to your Downloads folder.');
  });

  elements.csv.addEventListener('click', () => {
    download(YahooDraftExport.picksToCsv(currentSession?.picks), 'csv', 'text/csv;charset=utf-8');
  });

  elements.json.addEventListener('click', () => {
    const context = YahooDraftAgentContext.sessionToAgentContext(currentSession);
    download(`${JSON.stringify(context, null, 2)}\n`, 'agent-context.json', 'application/json');
  });

  elements.reset.addEventListener('click', async () => {
    if (
      !activeSessionKey ||
      !currentSession ||
      !YahooDraftSessionStore.sameDraftIdentity(currentSession, activeDiagnostics) ||
      !YahooDraftSyncClient.validIsoTimestamp(resetRevision(currentSession))
    ) {
      setStatus('Open the exact Yahoo mock draft tab and rescan it before resetting.', 'error');
      return;
    }
    const confirmed = window.confirm(
      `Reset recorded picks for active League ${currentSession.leagueId}? This cannot be undone and clears only this exact mock-draft session from the browser and local MCP server. Your imported ranking/profile settings are preserved.`,
    );
    if (!confirmed) return;

    const sessionKey = activeSessionKey;
    setStatus('Resetting this exact mock-draft session…');
    let result;
    try {
      result = await operationLock.run(sessionKey, async () => {
        const repairResult = await repairCoordinatorFor(sessionKey).reconcile();
        if (!repairResult.ok) return repairResult;

        const coordinator = resetCoordinatorFor(sessionKey);
        if (!await coordinator.hasPending()) {
          const exactSession = await draftStorage.getSession(sessionKey);
          if (!YahooDraftSessionStore.sameDraftIdentity(exactSession, activeDiagnostics)) {
            return { ok: false, error: 'The active draft changed before reset. Rescan and try again.' };
          }
          await coordinator.begin(exactSession);
        }
        return coordinator.reconcile();
      });
    } catch (error) {
      result = { ok: false, error: String(error?.message || error) };
    }

    if (!result?.ok) {
      if (result?.retryAfterRescan) {
        await sendToActiveTab('YAHOO_DRAFT_RECORDER_RESCAN', { forceSync: true });
      }
      await refresh({ resumeReset: false });
      setStatus(result?.error || 'Reset failed without clearing this draft. Retry after checking the local server.', 'error');
      return;
    }

    const readyToRescan = await YahooDraftSyncClient.waitUntilAfterReset(result.resetAt);
    let rescanResult = null;
    if (readyToRescan) {
      rescanResult = await sendToActiveTab('YAHOO_DRAFT_RECORDER_RESCAN', {
        forceSync: true,
        resetAt: result.resetAt,
      });
    }
    await refresh({ resumeReset: false });
    if (!readyToRescan || !rescanResult) {
      setStatus('Reset complete and imported profile preserved. Wait a moment, then reload or rescan this Yahoo page. Close older tabs for the same mock draft.', 'warning');
      return;
    }
    if (rescanResult.error) {
      setStatus(`Reset complete and imported profile preserved, but the fresh page scan needs attention: ${rescanResult.error} Close older tabs for this mock draft.`, 'warning');
      return;
    }
    if (rescanResult.syncStatus !== 'connected') {
      setStatus('Reset complete and imported profile preserved. The page was rescanned, but server sync is still offline or pending, so recommendations may not update yet. Keep the server running and rescan again. Close older tabs for this mock draft.', 'warning');
      return;
    }
    setStatus('Reset complete. Imported profile preserved and the current Yahoo page was rescanned from a fresh ledger. Close older tabs for this mock draft.');
  });

  webext.storage.onChanged.addListener((changes, area) => {
    if (area === 'local' && YahooDraftStorage.isRelevantStorageChange(changes)) refresh();
  });

  getActiveTab()
    .then((tab) => {
      activeTabId = tab?.id;
      return refresh();
    })
    .catch((error) => setStatus(`Could not load recorded picks: ${error.message}`, 'error'));
})();
