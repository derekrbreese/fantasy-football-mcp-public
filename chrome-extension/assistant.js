(function initDraftAssistant() {
  'use strict';

  const extensionApi = YahooDraftWebExtension.createWebExtensionApi(globalThis);
  const webext = extensionApi.native;
  const operationLock = YahooDraftStorage.createSessionOperationLock(webext.runtime);
  const draftStorage = YahooDraftStorage.createDraftStorage(extensionApi, { operationLock });
  const requestGuard = YahooDraftRecommendationSidebarState.createRecommendationRequestGuard();
  const cockpit = YahooDraftCockpit;
  const elements = {
    league: document.querySelector('#league-select'),
    strategy: document.querySelector('#strategy-select'),
    draftPlan: document.querySelector('#draft-plan-select'),
    refresh: document.querySelector('#refresh-recommendations'),
    dashboard: document.querySelector('#open-dashboard'),
    controllerStatus: document.querySelector('#controller-status'),
    view: document.querySelector('#recommendation-view'),
    cockpit: document.querySelector('#cockpit-controls'),
    queueCandidate: document.querySelector('#queue-candidate'),
    queueAdd: document.querySelector('#queue-add'),
    watchlist: document.querySelector('#watchlist'),
    notifications: document.querySelector('#turn-notifications'),
  };

  let sessions = {};
  let selectedSessionKey = null;
  let refreshing = false;
  let sessionLoadGeneration = 0;
  let cockpitPreferences = cockpit.sanitizePreferences({});
  let cockpitResponse = null;
  const autoRefresh = YahooDraftRecommendationSidebarState
    .createRecommendationAutoRefreshScheduler({
      delayMs: 350,
      selectedSessionKey: () => selectedSessionKey,
      sessionForKey: (sessionKey) => sessions[sessionKey],
      cancelInFlight: cancelPendingRecommendation,
      reloadSessions: () => loadSessions({ preferActive: false }),
      refresh: refreshRecommendations,
      onUnchanged: (session) => {
        if (session?.sessionKey === selectedSessionKey) {
          setControllerStatus('Recommendations already match the latest recorded draft state.');
        }
      },
      onError: (error) => {
        setControllerStatus(
          `Could not auto-refresh recommendations: ${String(error?.message || error)}`,
          'error',
        );
      },
    });

  function cancelPendingRecommendation() {
    requestGuard.cancel();
    refreshing = false;
    updateControls();
  }

  function renderMessage(message, session = {}) {
    const model = YahooDraftRecommendationViewModel.createRecommendationViewModel({
      status: 'error',
      leagueId: session.leagueId,
      message,
    }, session);
    YahooDraftRecommendationRenderer.renderRecommendationView(elements.view, model);
  }

  function setControllerStatus(message, kind = '') {
    elements.controllerStatus.textContent = message;
    elements.controllerStatus.className = `controller-status${kind ? ` controller-status--${kind}` : ''}`;
  }

  function updateDashboardLink(session) {
    if (!session?.leagueId || !/^\d{1,32}$/.test(session.leagueId)) {
      elements.dashboard.hidden = true;
      elements.dashboard.href = 'http://127.0.0.1:8765/draft-dashboard';
      return;
    }
    // The fragment is not included in the HTTP request or server logs.
    elements.dashboard.href = `http://127.0.0.1:8765/draft-dashboard#leagueId=${encodeURIComponent(session.leagueId)}`;
    elements.dashboard.hidden = false;
  }

  function updateControls() {
    const session = selectedSessionKey ? sessions[selectedSessionKey] : null;
    elements.refresh.disabled = refreshing || !session;
    elements.league.disabled = refreshing;
    elements.strategy.disabled = refreshing;
    elements.draftPlan.disabled = refreshing;
    updateDashboardLink(session);
  }

  function cockpitCandidates(response) {
    const candidates = new Map();
    const boards = Array.isArray(response?.cockpit?.positionBoards)
      ? response.cockpit.positionBoards
      : [];
    for (const board of boards) {
      for (const raw of Array.isArray(board?.candidates) ? board.candidates : []) {
        const candidate = cockpit.sanitizeCandidate(raw);
        if (candidate) candidates.set(candidate.key, candidate);
      }
    }
    for (const raw of Array.isArray(response?.recommendations) ? response.recommendations : []) {
      const candidate = cockpit.sanitizeCandidate(raw);
      if (candidate) candidates.set(candidate.key, candidate);
    }
    return candidates;
  }

  async function loadCockpitPreferences(session) {
    cockpitResponse = null;
    cockpitPreferences = cockpit.sanitizePreferences({});
    elements.cockpit.hidden = !session?.leagueId;
    if (!session?.leagueId) return;
    const key = cockpit.storageKey(session.sessionKey);
    const stored = await extensionApi.storageGet(key);
    if (selectedSessionKey !== session.sessionKey) return;
    cockpitPreferences = cockpit.sanitizePreferences(stored?.[key]);
    renderCockpit(session);
  }

  async function saveCockpitPreferences(session) {
    if (!session?.leagueId) return;
    const key = cockpit.storageKey(session.sessionKey);
    await extensionApi.storageSet({ [key]: cockpitPreferences });
  }

  function cockpitActionButton(label, action, key, title = '') {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    button.dataset.cockpitAction = action;
    button.dataset.playerKey = key;
    if (title) button.title = title;
    return button;
  }

  function renderCockpit(session) {
    elements.cockpit.hidden = !session?.leagueId;
    elements.notifications.checked = cockpitPreferences.notificationsEnabled;
    const candidates = cockpitCandidates(cockpitResponse);
    const selectable = [...candidates.values()].filter((candidate) => (
      !cockpitPreferences.watchlist.some((item) => item.key === candidate.key)
    ));
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = selectable.length ? 'Choose a player' : 'No additional players';
    elements.queueCandidate.replaceChildren(placeholder);
    for (const candidate of selectable) {
      const option = document.createElement('option');
      option.value = candidate.key;
      option.textContent = `${candidate.name} · ${candidate.position}`;
      elements.queueCandidate.appendChild(option);
    }
    elements.queueAdd.disabled = !selectable.length;

    const reconciled = cockpit.reconcileWatchlist(
      cockpitPreferences.watchlist,
      session?.picks,
    );
    elements.watchlist.replaceChildren();
    if (!reconciled.length) {
      const empty = document.createElement('li');
      empty.className = 'queue-help';
      empty.textContent = 'Add players from the latest trustworthy position board.';
      elements.watchlist.appendChild(empty);
      return;
    }
    reconciled.forEach((candidate, index) => {
      const item = document.createElement('li');
      item.className = `watchlist-item${candidate.drafted ? ' watchlist-item--drafted' : ''}`;
      const identity = document.createElement('span');
      identity.className = 'watchlist-identity';
      const name = document.createElement('strong');
      name.textContent = candidate.name;
      const meta = document.createElement('small');
      meta.textContent = candidate.drafted
        ? `${candidate.position} · drafted at pick ${candidate.pickNumber || 'unknown'}`
        : `${candidate.position} · ${candidate.tier} tier`;
      identity.append(name, meta);
      const actions = document.createElement('span');
      actions.className = 'watchlist-actions';
      const up = cockpitActionButton('↑', 'up', candidate.key, 'Move up');
      up.disabled = index === 0;
      const down = cockpitActionButton('↓', 'down', candidate.key, 'Move down');
      down.disabled = index === reconciled.length - 1;
      actions.append(up, down, cockpitActionButton('×', 'remove', candidate.key, 'Remove'));
      item.append(identity, actions);
      elements.watchlist.appendChild(item);
    });
  }

  async function maybeNotify(response, session) {
    const decision = cockpit.shouldNotify(cockpitPreferences, response, session);
    if (!decision.notify) return;
    const iconUrl = webext.runtime?.getURL
      ? webext.runtime.getURL('icons/football-128.png')
      : 'icons/football-128.png';
    await extensionApi.createNotification(cockpit.notificationId(session.sessionKey), {
      type: 'basic',
      iconUrl,
      title: decision.title,
      message: decision.message,
    });
    cockpitPreferences = cockpit.markNotified(cockpitPreferences, decision.key);
    await saveCockpitPreferences(session);
  }

  async function activeYahooDiagnostics() {
    try {
      const tabs = await extensionApi.queryTabs({ active: true, currentWindow: true });
      if (!tabs[0]?.id) return null;
      return await extensionApi.sendTabMessage(tabs[0].id, {
        type: 'YAHOO_DRAFT_RECORDER_STATUS',
      });
    } catch (_error) {
      return null;
    }
  }

  function populateLeagueSelect(choices) {
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = choices.length
      ? 'Choose a recorded league'
      : 'No recorded leagues found';
    elements.league.replaceChildren(placeholder);
    for (const choice of choices) {
      const option = document.createElement('option');
      option.value = choice.sessionKey;
      option.textContent = choice.label;
      elements.league.appendChild(option);
    }
    elements.league.value = selectedSessionKey || '';
  }

  async function loadSessions({ preferActive = true } = {}) {
    const loadGeneration = ++sessionLoadGeneration;
    const previousSelection = selectedSessionKey;
    const [storedSessions, diagnostics] = await Promise.all([
      draftStorage.listSessions(),
      preferActive ? activeYahooDiagnostics() : Promise.resolve(null),
    ]);
    if (loadGeneration !== sessionLoadGeneration) return false;
    sessions = storedSessions;
    const choices = YahooDraftRecommendationSidebarState.leagueChoices(sessions);
    if (preferActive) {
      selectedSessionKey = YahooDraftRecommendationSidebarState.resolveExplicitSelection(
        sessions,
        diagnostics,
      );
    } else if (!sessions[selectedSessionKey]) {
      selectedSessionKey = null;
    }
    if (selectedSessionKey !== previousSelection) {
      requestGuard.cancel();
      refreshing = false;
    }
    populateLeagueSelect(choices);
    updateControls();
    await loadCockpitPreferences(selectedSessionKey ? sessions[selectedSessionKey] : null);
    if (!selectedSessionKey) {
      setControllerStatus(choices.length
        ? 'Choose a recorded league. No saved draft is selected implicitly.'
        : 'Open a Yahoo draft and use the recorder before requesting recommendations.');
      renderMessage('Choose a recorded Yahoo league and refresh recommendations.');
    } else if (diagnostics?.sessionKey === selectedSessionKey) {
      setControllerStatus(`Using the active Yahoo tab: League ${sessions[selectedSessionKey].leagueId}.`);
    }
    return Boolean(selectedSessionKey);
  }

  async function refreshRecommendations() {
    const selected = selectedSessionKey;
    if (!selected || refreshing) return;
    let token = requestGuard.begin(sessions[selected]);
    refreshing = true;
    updateControls();
    setControllerStatus('Computing bounded deterministic recommendations…', 'loading');
    try {
      const session = await draftStorage.getSession(selected);
      if (!requestGuard.requestStillMatchesSelection(token, selectedSessionKey)) return;
      if (!session || session.sessionKey !== selected) {
        selectedSessionKey = null;
        cancelPendingRecommendation();
        await loadSessions({ preferActive: false });
        return;
      }
      sessions[selected] = session;
      token = requestGuard.begin(session);
      autoRefresh.markRequested(session);
      const result = await YahooDraftRecommendationClient.fetchDraftRecommendations(session, {
        strategy: elements.strategy.value,
        draftPlan: elements.draftPlan.value,
        count: 5,
        rankingCount: 250,
        simulations: 256,
        timeoutMs: 30000,
        signal: token.signal,
      });
      const currentSession = await draftStorage.getSession(selected);
      if (!requestGuard.requestStillMatchesSelection(token, selectedSessionKey)) return;
      if (!YahooDraftRecommendationSidebarState.recommendationStillMatchesSelection(
        selected,
        selectedSessionKey,
        session,
        currentSession,
        result,
      )) {
        if (currentSession) sessions[selected] = currentSession;
        renderMessage('The recorded draft changed while recommendations were being computed. Refresh again for the latest state.', currentSession || session);
        setControllerStatus('Draft state changed during the request; the stale response was discarded.', 'degraded');
        return;
      }
      const model = YahooDraftRecommendationViewModel.createRecommendationViewModel(result, session);
      YahooDraftRecommendationRenderer.renderRecommendationView(elements.view, model);
      cockpitResponse = result;
      renderCockpit(currentSession || session);
      let notificationWarning = '';
      try {
        await maybeNotify(result, currentSession || session);
      } catch (notificationError) {
        notificationWarning = String(notificationError?.message || notificationError);
      }
      setControllerStatus(
        notificationWarning
          ? `Recommendations refreshed, but the turn alert could not be shown: ${notificationWarning}`
          : model.mode === 'success'
          ? 'Recommendations refreshed from the latest synced draft state.'
          : 'Recommendations refreshed with the cautions shown below.',
        notificationWarning
          ? 'degraded'
          : (model.mode === 'blocked' || model.mode === 'error' ? 'error' : model.mode),
      );
    } catch (error) {
      if (!requestGuard.requestStillMatchesSelection(token, selectedSessionKey)) return;
      const session = sessions[selected] || {};
      const message = error?.name === 'AbortError'
        ? 'The loopback recommendation request timed out.'
        : String(error?.message || error);
      renderMessage(message, session);
      setControllerStatus(message, 'error');
    } finally {
      if (requestGuard.requestStillMatchesSelection(token, selectedSessionKey)) {
        requestGuard.finish(token);
        refreshing = false;
        updateControls();
      }
    }
  }

  elements.league.addEventListener('change', () => {
    sessionLoadGeneration += 1;
    autoRefresh.cancelScheduled();
    cancelPendingRecommendation();
    const value = elements.league.value;
    selectedSessionKey = sessions[value] ? value : null;
    updateControls();
    if (!selectedSessionKey) {
      setControllerStatus('Choose a recorded league. No saved draft is selected implicitly.');
      renderMessage('Choose a recorded Yahoo league and refresh recommendations.');
      return;
    }
    const session = sessions[selectedSessionKey];
    setControllerStatus(`Selected League ${session.leagueId}. Refresh when you want a new recommendation.`);
    renderMessage('Refresh to request recommendations for this explicitly selected league.', session);
    loadCockpitPreferences(session)
      .catch((error) => setControllerStatus(String(error?.message || error), 'error'));
  });

  elements.refresh.addEventListener('click', () => {
    autoRefresh.cancelScheduled();
    refreshRecommendations();
  });

  elements.queueAdd.addEventListener('click', () => {
    const session = selectedSessionKey ? sessions[selectedSessionKey] : null;
    const candidate = cockpitCandidates(cockpitResponse).get(elements.queueCandidate.value);
    if (!session || !candidate) return;
    cockpitPreferences = cockpit.addToWatchlist(cockpitPreferences, candidate);
    saveCockpitPreferences(session)
      .then(() => renderCockpit(session))
      .catch((error) => setControllerStatus(String(error?.message || error), 'error'));
  });

  elements.watchlist.addEventListener('click', (event) => {
    const button = event.target.closest?.('[data-cockpit-action]');
    const session = selectedSessionKey ? sessions[selectedSessionKey] : null;
    if (!button || !session) return;
    const key = button.dataset.playerKey;
    if (button.dataset.cockpitAction === 'remove') {
      cockpitPreferences = cockpit.removeFromWatchlist(cockpitPreferences, key);
    } else if (button.dataset.cockpitAction === 'up' || button.dataset.cockpitAction === 'down') {
      cockpitPreferences = cockpit.moveWatchlistEntry(
        cockpitPreferences,
        key,
        button.dataset.cockpitAction === 'up' ? -1 : 1,
      );
    } else {
      return;
    }
    saveCockpitPreferences(session)
      .then(() => renderCockpit(session))
      .catch((error) => setControllerStatus(String(error?.message || error), 'error'));
  });

  elements.notifications.addEventListener('change', () => {
    const session = selectedSessionKey ? sessions[selectedSessionKey] : null;
    if (!session) return;
    cockpitPreferences = cockpit.sanitizePreferences({
      ...cockpitPreferences,
      notificationsEnabled: elements.notifications.checked,
    });
    saveCockpitPreferences(session)
      .catch((error) => setControllerStatus(String(error?.message || error), 'error'));
  });

  document.addEventListener('keydown', (event) => {
    if (
      elements.refresh.disabled ||
      !YahooDraftRecommendationSidebarState.isRefreshShortcut(event)
    ) return;
    event.preventDefault();
    autoRefresh.cancelScheduled();
    refreshRecommendations();
  });

  webext.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local' || !YahooDraftStorage.isRelevantStorageChange(changes)) return;
    if (
      selectedSessionKey &&
      YahooDraftRecommendationSidebarState.storageChangeAffectsSession(
        changes,
        selectedSessionKey,
      )
    ) {
      setControllerStatus('Draft state updated; refreshing recommendations shortly…', 'loading');
      autoRefresh.schedule(selectedSessionKey);
      return;
    }
    loadSessions({ preferActive: false })
      .catch((error) => setControllerStatus(String(error?.message || error), 'error'));
  });

  webext.tabs?.onActivated?.addListener?.(() => {
    autoRefresh.cancelScheduled();
    cancelPendingRecommendation();
    renderMessage('Active Yahoo tab changed. Resolving its recorded league before refreshing.');
    loadSessions({ preferActive: true })
      .then((hasActiveLeague) => {
        if (!hasActiveLeague) return null;
        renderMessage(
          'Refresh to request recommendations for the league in the active Yahoo tab.',
          sessions[selectedSessionKey],
        );
        return refreshRecommendations();
      })
      .catch((error) => {
        setControllerStatus(String(error?.message || error), 'error');
      });
  });

  loadSessions()
    .then((hasActiveLeague) => {
      if (hasActiveLeague) return refreshRecommendations();
      return null;
    })
    .catch((error) => {
      const message = `Could not load recorded leagues: ${String(error?.message || error)}`;
      setControllerStatus(message, 'error');
      renderMessage(message);
    });
})();
