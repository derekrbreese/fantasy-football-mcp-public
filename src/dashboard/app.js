(function initializeDraftDashboard() {
  'use strict';

  const client = globalThis.YahooDraftRecommendationClient;
  const viewModels = globalThis.YahooDraftRecommendationViewModel;
  const renderer = globalThis.YahooDraftRecommendationRenderer;
  const cockpit = globalThis.YahooDraftCockpit;
  const profileClient = globalThis.YahooDraftProfileClient;
  const form = document.getElementById('recommendation-form');
  const profileForm = document.getElementById('draft-profile-form');
  const profileReuseForm = document.getElementById('draft-profile-reuse-form');
  const profileSourceLeague = document.getElementById('profile-source-league');
  const profileDefaultForm = document.getElementById('draft-profile-default-form');
  const profileDefaultSport = document.getElementById('profile-default-sport');
  const profileDefaultSource = document.getElementById('profile-default-source');
  const leagueInput = document.getElementById('league-id');
  const requestStatus = document.getElementById('request-status');
  const recommendationView = document.getElementById('recommendation-view');
  const formControls = [...form.elements];
  const profileControls = [...profileForm.elements];
  const profileReuseControls = [...profileReuseForm.elements];
  const profileDefaultControls = [...profileDefaultForm.elements];
  let savedProfiles = [];
  let savedProfileDefaults = [];
  let savedProfilesLoaded = false;
  let savedProfilesLoadFailed = false;
  let savedProfileLoadGeneration = 0;
  let profileControlsBusy = false;
  let latestCockpitData = null;
  let cockpitPreferences = cockpit?.sanitizePreferences({}) || null;
  let selectedPosition = 'OVERALL';
  let activeCockpitLeagueId = null;

  function clear(node) {
    node.replaceChildren();
  }

  function textElement(tag, text, className) {
    const node = document.createElement(tag);
    node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function finiteNumber(value) {
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  }

  function show(id) {
    document.getElementById(id).classList.remove('hidden');
  }

  function resetAnalysisPanels({ preserveCockpit = false } = {}) {
    const panelIds = [
      'quality-panel',
      'draft-summary',
      'recommendations-panel',
      'scenario-panel',
      'roster-panel',
      'history-panel',
    ];
    if (!preserveCockpit) panelIds.push('cockpit-panel');
    panelIds.forEach((id) => document.getElementById(id).classList.add('hidden'));
    const contentIds = [
      'quality-summary',
      'diagnostic-details',
      'recommendation-view',
      'scenario-body',
      'roster-counts',
      'roster-list',
      'draft-history',
      'roster-slots',
      'roster-warnings',
    ];
    if (!preserveCockpit) contentIds.push(
      'watchlist',
      'position-filters',
      'position-board',
      'strategy-comparison',
      'position-runs',
      'fallback-tiers',
      'player-comparison',
      'draft-recap',
    );
    contentIds.forEach((id) => clear(document.getElementById(id)));
    ['current-pick', 'next-pick', 'pick-count', 'team-count'].forEach((id) => {
      document.getElementById(id).textContent = '—';
    });
    if (!preserveCockpit) {
      document.getElementById('queue-candidate').replaceChildren(
        textElement('option', 'Choose a player'),
      );
      document.getElementById('tier-context').textContent = '';
    }
  }

  function setControlsDisabled(disabled) {
    formControls.forEach((control) => {
      control.disabled = disabled;
    });
  }

  function setProfileControlsDisabled(disabled) {
    profileControlsBusy = disabled;
    profileControls.forEach((control) => {
      control.disabled = disabled;
    });
    profileReuseControls.forEach((control) => {
      control.disabled = disabled;
    });
    profileDefaultControls.forEach((control) => {
      control.disabled = disabled;
    });
    if (!disabled) {
      updateProfileReuseControls();
      updateProfileDefaultControls();
    }
  }

  function setStatus(message, kind) {
    requestStatus.textContent = message;
    requestStatus.className = `request-status ${kind}`;
  }

  function setProfileStatus(title, detail, freshness) {
    const status = document.getElementById('profile-source-status');
    document.getElementById('profile-source-title').textContent = title;
    document.getElementById('profile-source-detail').textContent = detail;
    document.getElementById('profile-freshness').textContent = freshness.label;
    status.className = `profile-source-status ${freshness.kind}`;
  }

  function setProfileDefaultStatus(title, detail, kind = 'unknown') {
    const status = document.getElementById('profile-default-status');
    document.getElementById('profile-default-title').textContent = title;
    document.getElementById('profile-default-detail').textContent = detail;
    status.className = `profile-default-status ${kind}`;
  }

  function selectedRosterPositions() {
    const values = {};
    profileForm.querySelectorAll('[data-roster-position]').forEach((input) => {
      values[input.dataset.rosterPosition] = input.value;
    });
    return profileClient.parseRosterPositions(values);
  }

  function selectedLeagueSettings() {
    return {
      teams: document.getElementById('profile-team-count').value,
      rosterPositions: selectedRosterPositions(),
    };
  }

  function isXlsxFile(file) {
    const name = typeof file?.name === 'string' ? file.name.toLowerCase() : '';
    return name.endsWith('.xlsx') || file?.type === 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
  }

  function profileSourceTitle(format) {
    if (format === 'draftsheets-2026' || format === 'xlsx') return 'DraftSheets profile imported';
    if (format === 'json') return 'Local JSON profile imported';
    return 'Local CSV profile imported';
  }

  function safeResponseCount(value, fallback) {
    return Number.isInteger(value) && value > 0 && value <= profileClient.MAX_PROFILE_RANKINGS
      ? value
      : fallback;
  }

  function validLeagueId(value) {
    return /^\d{1,32}$/.test(String(value || '').trim());
  }

  function profileFormatLabel(format) {
    if (format === 'draftsheets-2026') return 'DraftSheets';
    if (format === 'json') return 'JSON';
    return 'CSV';
  }

  function profileForSelectedLeague() {
    const leagueId = leagueInput.value.trim();
    return savedProfiles.find((profile) => profile.leagueId === leagueId) || null;
  }

  function defaultForSport(sport) {
    return savedProfileDefaults.find((entry) => entry.sport === sport) || null;
  }

  function profileForDefault(entry) {
    if (!entry) return null;
    return savedProfiles.find((profile) => (
      profile.sport === entry.sport && profile.leagueId === entry.sourceLeagueId
    )) || null;
  }

  function updateProfileReuseControls() {
    const leagueId = leagueInput.value.trim();
    const sourceLeagueId = profileSourceLeague.value;
    const validSource = savedProfiles.some((profile) => (
      profile.leagueId === sourceLeagueId && profile.leagueId !== leagueId
    ));
    profileSourceLeague.disabled = profileControlsBusy ||
      !savedProfilesLoaded ||
      !validLeagueId(leagueId) ||
      Boolean(profileForSelectedLeague());
    document.getElementById('reuse-profile-button').disabled = profileControlsBusy ||
      Boolean(profileForSelectedLeague()) ||
      !validSource;
  }

  function updateProfileDefaultControls() {
    const sport = profileDefaultSport.value;
    const sourceLeagueId = profileDefaultSource.value;
    const currentDefault = defaultForSport(sport);
    const validSource = savedProfiles.some((profile) => (
      profile.sport === sport && profile.leagueId === sourceLeagueId
    ));
    profileDefaultSport.disabled = profileControlsBusy ||
      !savedProfilesLoaded ||
      (savedProfiles.length === 0 && savedProfileDefaults.length === 0);
    profileDefaultSource.disabled = profileControlsBusy ||
      !savedProfilesLoaded ||
      !sport;
    document.getElementById('set-profile-default-button').disabled = profileControlsBusy ||
      !validSource ||
      currentDefault?.sourceLeagueId === sourceLeagueId;
    document.getElementById('clear-profile-default-button').disabled = profileControlsBusy ||
      !currentDefault;
  }

  function renderSavedProfileChoices() {
    const previousValue = profileSourceLeague.value;
    const leagueId = leagueInput.value.trim();
    const choices = savedProfiles.filter((profile) => profile.leagueId !== leagueId);
    const placeholder = document.createElement('option');
    placeholder.value = '';
    if (savedProfilesLoaded) {
      placeholder.textContent = choices.length
        ? 'Choose a saved source profile'
        : 'No other saved profiles found';
    } else {
      placeholder.textContent = savedProfilesLoadFailed
        ? 'Saved profiles unavailable'
        : 'Loading saved profiles…';
    }
    profileSourceLeague.replaceChildren(placeholder);
    choices.forEach((profile) => {
      const option = document.createElement('option');
      option.value = profile.leagueId;
      option.textContent = profileClient.profileChoiceLabel(profile);
      profileSourceLeague.appendChild(option);
    });
    profileSourceLeague.value = choices.some((profile) => profile.leagueId === previousValue)
      ? previousValue
      : '';
    updateProfileReuseControls();
  }

  function renderProfileDefaultSourceChoices() {
    const previousValue = profileDefaultSource.value;
    const sport = profileDefaultSport.value;
    const currentDefault = defaultForSport(sport);
    const choices = savedProfiles.filter((profile) => profile.sport === sport);
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = sport
      ? (choices.length ? 'Choose a saved profile' : 'No saved profiles for this sport')
      : 'Choose a sport first';
    profileDefaultSource.replaceChildren(placeholder);
    choices.forEach((profile) => {
      const option = document.createElement('option');
      option.value = profile.leagueId;
      const suffix = currentDefault?.sourceLeagueId === profile.leagueId
        ? ' · current default'
        : '';
      option.textContent = `${profileClient.profileChoiceLabel(profile)}${suffix}`;
      profileDefaultSource.appendChild(option);
    });
    if (currentDefault && choices.some((profile) => (
      profile.leagueId === currentDefault.sourceLeagueId
    ))) {
      profileDefaultSource.value = currentDefault.sourceLeagueId;
    } else {
      profileDefaultSource.value = choices.some((profile) => profile.leagueId === previousValue)
        ? previousValue
        : '';
    }
    updateProfileDefaultControls();
  }

  function showProfileDefaultStatus() {
    const sport = profileDefaultSport.value;
    if (!sport) {
      setProfileDefaultStatus(
        savedProfiles.length ? 'Choose a Yahoo sport' : 'No default profile selected',
        savedProfiles.length
          ? 'Select a sport to view or change its default profile.'
          : 'Import a profile before setting a default for future drafts.',
      );
      return;
    }
    const currentDefault = defaultForSport(sport);
    const sourceProfile = profileForDefault(currentDefault);
    if (currentDefault && !sourceProfile) {
      setProfileDefaultStatus(
        `${profileClient.profileSportLabel(sport)} default source is missing`,
        `The saved default points to League ${currentDefault.sourceLeagueId}, but that source profile is missing and will not be applied. Clear it or choose another saved profile for this sport.`,
        'error',
      );
      return;
    }
    if (currentDefault && sourceProfile) {
      const freshness = profileClient.describeProfileFreshness(
        sourceProfile.asOf || sourceProfile.importedAt,
      );
      setProfileDefaultStatus(
        `${profileClient.profileSportLabel(sport)} default: League ${sourceProfile.leagueId}`,
        `${freshness.label}. This profile will be copied only to future profileless Yahoo drafts for this sport, including real drafts and mocks. Existing exact profiles win, and picks are never copied.`,
        freshness.kind,
      );
      return;
    }
    setProfileDefaultStatus(
      `No ${profileClient.profileSportLabel(sport)} default`,
      'Future drafts for this sport remain unbound until an exact profile is imported, reused, or explicitly set as the default here.',
    );
  }

  function renderProfileDefaultChoices() {
    const previousSport = profileDefaultSport.value;
    const sports = [...new Set([
      ...savedProfiles.map((profile) => profile.sport),
      ...savedProfileDefaults.map((entry) => entry.sport),
    ])].sort();
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = savedProfilesLoaded
      ? (sports.length ? 'Choose a Yahoo sport' : 'No saved profile sports')
      : (savedProfilesLoadFailed ? 'Saved profiles unavailable' : 'Loading saved profiles…');
    profileDefaultSport.replaceChildren(placeholder);
    sports.forEach((sport) => {
      const option = document.createElement('option');
      option.value = sport;
      option.textContent = profileClient.profileSportLabel(sport);
      profileDefaultSport.appendChild(option);
    });
    if (sports.includes(previousSport)) profileDefaultSport.value = previousSport;
    else if (sports.length === 1) profileDefaultSport.value = sports[0];
    else profileDefaultSport.value = '';
    renderProfileDefaultSourceChoices();
    showProfileDefaultStatus();
  }

  function showSelectedProfileStatus() {
    const leagueId = leagueInput.value.trim();
    const selectedProfile = profileForSelectedLeague();
    if (selectedProfile) {
      setProfileStatus(
        `${profileFormatLabel(selectedProfile.format)} profile ready`,
        `${selectedProfile.rankingCount} ranked players bound to league ${leagueId}.`,
        profileClient.describeProfileFreshness(selectedProfile.asOf),
      );
    } else if (savedProfiles.length && validLeagueId(leagueId)) {
      setProfileStatus(
        'This draft has no local profile',
        'Choose a saved source profile above, then use it explicitly for this draft.',
        { kind: 'unknown', label: 'No profile selected for this draft' },
      );
    } else if (savedProfiles.length) {
      setProfileStatus(
        'Saved profiles available',
        'Select the active league before choosing a saved source profile.',
        { kind: 'unknown', label: 'No profile selected for this draft' },
      );
    } else {
      setProfileStatus(
        'No local profile imported',
        'Import one below, or open a draft that already has an exact saved profile.',
        { kind: 'unknown', label: 'Source date unknown' },
      );
    }
  }

  async function loadSavedProfiles() {
    const loadGeneration = ++savedProfileLoadGeneration;
    savedProfilesLoaded = false;
    savedProfilesLoadFailed = false;
    renderSavedProfileChoices();
    renderProfileDefaultChoices();
    try {
      const catalog = await profileClient.listDraftProfileCatalog();
      if (loadGeneration !== savedProfileLoadGeneration) return false;
      savedProfiles = catalog.profiles;
      savedProfileDefaults = catalog.defaults;
      savedProfilesLoaded = true;
      savedProfilesLoadFailed = false;
      renderSavedProfileChoices();
      renderProfileDefaultChoices();
      showSelectedProfileStatus();
      return true;
    } catch (error) {
      if (loadGeneration !== savedProfileLoadGeneration) return false;
      savedProfiles = [];
      savedProfileDefaults = [];
      savedProfilesLoaded = false;
      savedProfilesLoadFailed = true;
      renderSavedProfileChoices();
      renderProfileDefaultChoices();
      setProfileStatus(
        'Saved profile list unavailable',
        String(error?.message || 'Saved profiles could not be loaded.').slice(0, 240),
        { kind: 'error', label: 'No profile was selected or changed' },
      );
      return false;
    }
  }

  function prefillLeagueFromFragment() {
    const params = new URLSearchParams(window.location.hash.slice(1));
    const leagueId = params.get('leagueId') || '';
    if (/^\d{1,32}$/.test(leagueId)) {
      leagueInput.value = leagueId;
      setStatus(`League ${leagueId} selected from the extension. Refresh when ready.`, 'idle');
    }
  }

  function diagnosticGroup(title, values) {
    const group = textElement('section', '', 'diagnostic-group');
    group.appendChild(textElement('h3', title));
    const list = textElement('ul', '', 'compact-list');
    values.forEach((value) => list.appendChild(textElement('li', value)));
    group.appendChild(list);
    return group;
  }

  function renderDiagnostics(data, model) {
    show('quality-panel');
    const summary = document.getElementById('quality-summary');
    const details = document.getElementById('diagnostic-details');
    clear(summary);
    clear(details);
    summary.appendChild(textElement('strong', model.statusTitle, model.mode === 'success' ? 'ready' : 'blocked'));
    summary.appendChild(textElement('span', model.statusMessage));
    const latency = finiteNumber(data.latencyMs);
    summary.appendChild(textElement(
      'span',
      `Strategy ${data.strategy || 'unknown'} · ${latency === null ? 'scoring latency unavailable' : `${latency.toFixed(1)} ms scoring latency`}`,
    ));

    const sources = data.dataSources && typeof data.dataSources === 'object'
      ? Object.entries(data.dataSources).slice(0, 8).map(([name, source]) => `${name}: ${String(source)}`)
      : ['Source attribution unavailable.'];
    details.appendChild(diagnosticGroup('Data sources', sources));

    const checks = data?.critic?.checks && typeof data.critic.checks === 'object'
      ? Object.entries(data.critic.checks).slice(0, 12).map(([name, passed]) => `${name}: ${passed === true ? 'pass' : 'needs caution'}`)
      : ['Critic checks unavailable.'];
    details.appendChild(diagnosticGroup('Deterministic critic', checks));

    const readinessChecks = Array.isArray(data?.cockpit?.readiness?.checks)
      ? data.cockpit.readiness.checks.slice(0, 10).map((check) => (
        `${check.passed === true ? 'Ready' : 'Needs attention'}: ${String(check.label || 'Unknown check')}`
      ))
      : ['Draft cockpit readiness unavailable.'];
    details.appendChild(diagnosticGroup('Draft readiness', readinessChecks));
  }

  function renderSummary(data) {
    const state = data.state || {};
    show('draft-summary');
    document.getElementById('current-pick').textContent =
      finiteNumber(state.currentOverallPick) ?? '—';
    document.getElementById('next-pick').textContent =
      finiteNumber(state.nextUserPick) ?? '—';
    document.getElementById('pick-count').textContent = Array.isArray(state.picks) ? state.picks.length : '—';
    document.getElementById('team-count').textContent = finiteNumber(state.teamCount) ?? 'unavailable';
  }

  function renderSharedBoard(data, leagueId) {
    show('recommendations-panel');
    const model = viewModels.createRecommendationViewModel(
      data,
      { leagueId },
      { maxRecommendations: 20 },
    );
    renderer.renderRecommendationView(recommendationView, model);
    return model;
  }

  function renderScenarios(data, model) {
    const recommendationMode = model?.mode === 'success' || model?.mode === 'degraded';
    const visibleRecommendationCount = Array.isArray(model?.recommendations)
      ? model.recommendations.length
      : 0;
    const recommendations = recommendationMode && Array.isArray(data.recommendations)
      ? data.recommendations.slice(0, visibleRecommendationCount)
      : [];
    const body = document.getElementById('scenario-body');
    clear(body);
    if (recommendations.length === 0) {
      document.getElementById('scenario-panel').classList.add('hidden');
      return;
    }
    show('scenario-panel');
    const scoreKeys = ['value', 'rosterConstruction', 'draftDynamics', 'opponentModel', 'riskNews', 'scenario'];
    recommendations.forEach((item) => {
      const row = document.createElement('tr');
      row.appendChild(textElement('th', String(item?.player?.name || 'Unknown')));
      scoreKeys.forEach((key) => {
        const cell = document.createElement('td');
        const rawScore = finiteNumber(item?.scores?.[key]);
        if (rawScore === null) {
          cell.appendChild(textElement('span', '—', 'unavailable-score'));
        } else {
          const score = Math.max(0, Math.min(100, rawScore));
          const progress = document.createElement('progress');
          progress.max = 100;
          progress.value = score;
          progress.setAttribute('aria-label', `${key} score ${score} out of 100`);
          cell.appendChild(progress);
          cell.appendChild(textElement('span', score.toFixed(0)));
        }
        row.appendChild(cell);
      });
      body.appendChild(row);
    });
  }

  function renderRoster(data) {
    const roster = Array.isArray(data?.state?.userRoster) ? data.state.userRoster : [];
    const plan = data?.cockpit?.rosterPlan;
    const counts = document.getElementById('roster-counts');
    const list = document.getElementById('roster-list');
    const slots = document.getElementById('roster-slots');
    const warnings = document.getElementById('roster-warnings');
    clear(counts);
    clear(list);
    clear(slots);
    clear(warnings);
    if (roster.length === 0 && !Array.isArray(plan?.slots)) {
      document.getElementById('roster-panel').classList.add('hidden');
      return;
    }
    show('roster-panel');
    const totals = new Map();
    roster.forEach((pick) => {
      const position = String(pick.position || 'Unknown');
      totals.set(position, (totals.get(position) || 0) + 1);
      list.appendChild(textElement(
        'li',
        `${pick.player || 'Unknown'} · ${position}${pick.pickNumber ? ` · pick ${pick.pickNumber}` : ''}`,
      ));
    });
    [...totals.entries()].sort().forEach(([position, total]) => {
      counts.appendChild(textElement('span', `${position} ${total}`, 'position-chip'));
    });
    if (Array.isArray(plan?.slots)) {
      plan.slots.slice(0, 12).forEach((slot) => {
        const node = textElement('div', '', 'roster-slot');
        node.appendChild(textElement('strong', String(slot.position || 'Slot')));
        node.appendChild(textElement(
          'span',
          `${finiteNumber(slot.current) ?? 0}/${finiteNumber(slot.required) ?? 0} filled · ${finiteNumber(slot.open) ?? 0} open`,
        ));
        slots.appendChild(node);
      });
    }
    if (Array.isArray(plan?.warnings)) {
      plan.warnings.slice(0, 6).forEach((warning) => {
        warnings.appendChild(textElement('li', String(warning).slice(0, 240)));
      });
    }
  }

  function renderHistory(data) {
    const picks = Array.isArray(data?.state?.picks) ? [...data.state.picks] : [];
    const list = document.getElementById('draft-history');
    clear(list);
    if (picks.length === 0) {
      document.getElementById('history-panel').classList.add('hidden');
      return;
    }
    show('history-panel');
    picks.slice(-24).reverse().forEach((pick) => {
      const item = textElement('li', '', pick.isUserPick ? 'user-pick' : '');
      item.appendChild(textElement('strong', `#${pick.pickNumber ?? '?'}`));
      item.appendChild(textElement('span', String(pick.player || 'Unknown player')));
      item.appendChild(textElement('small', [pick.position, pick.nflTeam, pick.fantasyTeam].filter(Boolean).join(' · ')));
      list.appendChild(item);
    });
  }

  function cockpitCandidateMap(data) {
    const result = new Map();
    const add = (value) => {
      const safe = cockpit.sanitizeCandidate(value);
      if (!safe) return;
      const existing = result.get(safe.key) || {};
      result.set(safe.key, { ...existing, ...safe, raw: value?.raw || value });
    };
    const boards = Array.isArray(data?.cockpit?.positionBoards)
      ? data.cockpit.positionBoards
      : [];
    boards.forEach((board) => {
      if (Array.isArray(board?.candidates)) board.candidates.forEach(add);
    });
    if (Array.isArray(data?.recommendations)) {
      data.recommendations.slice(0, 20).forEach((item) => add({
        ...item,
        name: item?.player?.name,
        position: item?.player?.position,
        team: item?.player?.team,
        score: item?.overallScore,
        tier: item?.specialistDetails?.value?.tier,
        raw: item,
      }));
    }
    return result;
  }

  function loadCockpitPreferences(leagueId) {
    if (activeCockpitLeagueId === leagueId && cockpitPreferences) return;
    activeCockpitLeagueId = leagueId;
    selectedPosition = 'OVERALL';
    let stored = null;
    try {
      stored = JSON.parse(localStorage.getItem(cockpit.storageKey(leagueId)) || 'null');
    } catch (_error) {
      stored = null;
    }
    cockpitPreferences = cockpit.sanitizePreferences(stored);
  }

  function saveCockpitPreferences() {
    if (!activeCockpitLeagueId || !cockpitPreferences) return;
    cockpitPreferences = cockpit.sanitizePreferences(cockpitPreferences);
    try {
      localStorage.setItem(
        cockpit.storageKey(activeCockpitLeagueId),
        JSON.stringify(cockpitPreferences),
      );
    } catch (_error) {
      setStatus('Cockpit preferences could not be saved in this browser.', 'warning');
    }
  }

  function cockpitButton(label, action, key, className = '') {
    const button = textElement('button', label, `cockpit-action ${className}`.trim());
    button.type = 'button';
    button.dataset.cockpitAction = action;
    if (key) button.dataset.playerKey = key;
    return button;
  }

  function renderWatchlist(data, candidates) {
    const list = document.getElementById('watchlist');
    const select = document.getElementById('queue-candidate');
    clear(list);
    const placeholder = textElement('option', 'Choose a player');
    placeholder.value = '';
    select.replaceChildren(placeholder);

    const reconciled = cockpit.reconcileWatchlist(
      cockpitPreferences.watchlist,
      data?.state?.picks,
    );
    const queuedKeys = new Set(reconciled.map((item) => item.key));
    [...candidates.values()]
      .filter((candidate) => !queuedKeys.has(candidate.key))
      .sort((left, right) => (right.score || 0) - (left.score || 0))
      .forEach((candidate) => {
        const option = textElement(
          'option',
          `${candidate.name} · ${candidate.position}${candidate.team ? ` · ${candidate.team}` : ''}`,
        );
        option.value = candidate.key;
        select.appendChild(option);
      });

    if (!reconciled.length) {
      list.appendChild(textElement(
        'li',
        'Your queue is empty. Add players from the selector or position board.',
        'watchlist-empty',
      ));
      return;
    }
    reconciled.forEach((item, index) => {
      const node = textElement('li', '', `watchlist-item${item.drafted ? ' drafted' : ''}`);
      const identity = textElement('div', '', 'watchlist-identity');
      identity.appendChild(textElement('strong', `${index + 1}. ${item.name}`));
      identity.appendChild(textElement(
        'small',
        item.drafted
          ? `${item.position} · ${item.team || 'team unknown'} · drafted at #${item.pickNumber || '?'}`
          : `${item.position} · ${item.team || 'team unknown'} · ${item.tier} tier`,
      ));
      node.appendChild(identity);
      const actions = textElement('div', '', 'watchlist-actions');
      actions.appendChild(cockpitButton('↑', 'up', item.key));
      actions.appendChild(cockpitButton('↓', 'down', item.key));
      const comparing = cockpitPreferences.comparisonKeys.includes(item.key);
      actions.appendChild(cockpitButton(
        comparing ? 'Comparing' : 'Compare',
        'compare',
        item.key,
        `compare-toggle${comparing ? ' active' : ''}`,
      ));
      actions.appendChild(cockpitButton('Remove', 'remove', item.key));
      node.appendChild(actions);
      list.appendChild(node);
    });
  }

  function renderPositionBoard(data) {
    const filters = document.getElementById('position-filters');
    const boardRoot = document.getElementById('position-board');
    clear(filters);
    clear(boardRoot);
    const boards = Array.isArray(data?.cockpit?.positionBoards)
      ? data.cockpit.positionBoards
      : [];
    if (!boards.some((board) => board.position === selectedPosition)) {
      selectedPosition = boards[0]?.position || 'OVERALL';
    }
    boards.forEach((board) => {
      const button = textElement(
        'button',
        board.position === 'OVERALL' ? 'Overall' : board.position,
        `position-filter${board.position === selectedPosition ? ' active' : ''}`,
      );
      button.type = 'button';
      button.dataset.position = board.position;
      filters.appendChild(button);
    });
    const board = boards.find((item) => item.position === selectedPosition);
    if (!board) {
      document.getElementById('tier-context').textContent = 'Position availability is blocked until the ledger is trustworthy.';
      boardRoot.appendChild(textElement('p', 'No trustworthy position board is available.', 'comparison-empty'));
      return;
    }
    const drop = finiteNumber(board.nextTierDropAfter);
    document.getElementById('tier-context').textContent =
      `${board.tierRemaining} ${board.leadingTier} tier player(s) remain${drop === null ? '' : ` · visible tier drop after ${drop} option(s)`}.`;
    board.candidates.slice(0, 5).forEach((candidate) => {
      const safe = cockpit.sanitizeCandidate(candidate);
      if (!safe) return;
      const card = textElement('div', '', 'position-candidate');
      const identity = textElement('div', '', 'watchlist-identity');
      identity.appendChild(textElement('strong', safe.name));
      identity.appendChild(textElement('small', `${safe.position} · ${safe.team || 'team unknown'} · ${safe.tier} tier`));
      card.appendChild(identity);
      card.appendChild(textElement('span', safe.score === null ? '—' : safe.score.toFixed(1), 'candidate-score'));
      const actions = textElement('div', '', 'candidate-actions');
      const queued = cockpitPreferences.watchlist.some((item) => item.key === safe.key);
      const comparing = cockpitPreferences.comparisonKeys.includes(safe.key);
      actions.appendChild(cockpitButton(queued ? 'Queued' : 'Add to queue', 'add', safe.key));
      actions.lastChild.disabled = queued;
      actions.appendChild(cockpitButton(
        comparing ? 'Comparing' : 'Compare',
        'compare-candidate',
        safe.key,
        `compare-toggle${comparing ? ' active' : ''}`,
      ));
      card.appendChild(actions);
      boardRoot.appendChild(card);
    });
  }

  function renderStrategyComparison(data) {
    const root = document.getElementById('strategy-comparison');
    clear(root);
    const comparison = data?.cockpit?.strategyComparison;
    root.appendChild(textElement(
      'p',
      String(comparison?.summary || 'Strategy comparison is unavailable.'),
      'strategy-summary',
    ));
    if (!Array.isArray(comparison?.strategies)) return;
    comparison.strategies.slice(0, 3).forEach((entry) => {
      const row = textElement('div', '', 'strategy-row');
      row.appendChild(textElement('span', String(entry.strategy || 'unknown')));
      row.appendChild(textElement('strong', String(entry?.primary?.name || 'Unavailable')));
      const score = finiteNumber(entry?.primary?.score);
      row.appendChild(textElement('small', score === null ? '—' : score.toFixed(1)));
      root.appendChild(row);
    });
  }

  function renderRunsAndFallbacks(data) {
    const runs = document.getElementById('position-runs');
    const fallbacks = document.getElementById('fallback-tiers');
    clear(runs);
    clear(fallbacks);
    const activeRuns = Array.isArray(data?.cockpit?.positionRuns) ? data.cockpit.positionRuns : [];
    if (!activeRuns.length) runs.appendChild(textElement('li', 'No active three-pick position run in the last eight selections.'));
    activeRuns.slice(0, 4).forEach((run) => runs.appendChild(textElement('li', String(run.message || '').slice(0, 180))));
    const tiers = Array.isArray(data?.cockpit?.fallbackTiers) ? data.cockpit.fallbackTiers : [];
    tiers.slice(0, 4).forEach((tier) => {
      const group = textElement('div', '', 'fallback-tier');
      group.appendChild(textElement('strong', `${tier.position} · ${tier.tier} tier`));
      const names = Array.isArray(tier.candidates)
        ? tier.candidates.slice(0, 3).map((item) => String(item.name || 'Unknown'))
        : [];
      group.appendChild(textElement('p', names.join(' → ') || 'No fallback candidates available.'));
      fallbacks.appendChild(group);
    });
  }

  function renderComparison(candidates) {
    const root = document.getElementById('player-comparison');
    clear(root);
    const queueByKey = new Map(cockpitPreferences.watchlist.map((item) => [item.key, item]));
    const selected = cockpitPreferences.comparisonKeys
      .map((key) => candidates.get(key) || queueByKey.get(key))
      .filter(Boolean)
      .slice(0, 3);
    if (selected.length < 2) {
      root.appendChild(textElement('p', 'Choose at least two queued players to compare.', 'comparison-empty'));
      return;
    }
    const table = textElement('table', '', 'comparison-table');
    const head = document.createElement('thead');
    const headRow = document.createElement('tr');
    ['Player', 'Pos', 'Score', 'Tier', 'Rank', 'ADP', 'Roster impact', 'Risk/news'].forEach((label) => {
      headRow.appendChild(textElement('th', label));
    });
    head.appendChild(headRow);
    table.appendChild(head);
    const body = document.createElement('tbody');
    selected.forEach((candidate) => {
      const raw = candidate.raw || {};
      const row = document.createElement('tr');
      const values = [
        candidate.name,
        candidate.position,
        candidate.score === null || candidate.score === undefined ? '—' : Number(candidate.score).toFixed(1),
        candidate.tier,
        raw?.player?.rank ?? raw?.rank ?? '—',
        raw?.player?.adp ?? raw?.adp ?? '—',
        raw?.rosterImpact || 'Unavailable',
        raw?.risk?.status || 'unknown',
      ];
      values.forEach((value, index) => row.appendChild(textElement(index === 0 ? 'th' : 'td', String(value))));
      body.appendChild(row);
    });
    table.appendChild(body);
    root.appendChild(table);
  }

  function renderRecap(data) {
    const root = document.getElementById('draft-recap');
    clear(root);
    const recap = data?.cockpit?.recap;
    if (!recap) {
      root.appendChild(textElement('p', 'Draft recap is unavailable.', 'comparison-empty'));
      return;
    }
    const progress = document.createElement('progress');
    progress.className = 'recap-progress';
    progress.max = 1;
    progress.value = finiteNumber(recap.progress) ?? 0;
    progress.setAttribute('aria-label', 'Draft completion progress');
    root.appendChild(progress);
    root.appendChild(textElement(
      'p',
      `${recap.recordedPicks ?? 0}${recap.expectedPicks ? ` of ${recap.expectedPicks}` : ''} league picks recorded · ${recap.complete ? 'draft complete' : recap.status}`,
      'recap-summary',
    ));
    root.appendChild(textElement('p', String(recap.summary || ''), 'recap-summary'));
    const decisions = textElement('div', '', 'recap-decisions');
    if (Array.isArray(recap.decisions)) recap.decisions.slice(-8).forEach((decision) => {
      const item = textElement('div', '', 'recap-decision');
      item.appendChild(textElement('strong', String(decision.player || 'Unknown')));
      const labelClass = decision.label === 'value' ? 'recap-value' : (decision.label === 'reach' ? 'recap-reach' : '');
      item.appendChild(textElement(
        'span',
        `Pick ${decision.pickNumber} · ADP ${decision.adp} · ${decision.label}`,
        labelClass,
      ));
      decisions.appendChild(item);
    });
    root.appendChild(decisions);
  }

  function renderCockpit(data, leagueId) {
    if (!cockpit || !data?.cockpit) return;
    loadCockpitPreferences(leagueId);
    latestCockpitData = data;
    show('cockpit-panel');
    const candidates = cockpitCandidateMap(data);
    renderWatchlist(data, candidates);
    renderPositionBoard(data);
    renderStrategyComparison(data);
    renderRunsAndFallbacks(data);
    renderComparison(candidates);
    renderRecap(data);
  }

  function render(data, leagueId) {
    resetAnalysisPanels();
    const model = renderSharedBoard(data, leagueId);
    renderDiagnostics(data, model);
    if (data.state) renderSummary(data);
    renderScenarios(data, model);
    renderRoster(data);
    renderHistory(data);
    renderCockpit(data, leagueId);
    return model;
  }

  function requestStatusForModel(model, leagueId) {
    if (model.mode === 'success') {
      return [`Analysis refreshed for league ${leagueId}.`, 'success'];
    }
    if (model.mode === 'degraded') {
      return [`Analysis for league ${leagueId} needs the cautions below.`, 'warning'];
    }
    if (model.mode === 'blocked') {
      return [`Recommendations are blocked for league ${leagueId}; repair the draft ledger.`, 'warning'];
    }
    return [`Recommendations are unavailable for league ${leagueId}.`, 'error'];
  }

  function renderClientError(message, leagueId) {
    show('recommendations-panel');
    const model = viewModels.createRecommendationViewModel(
      { status: 'error', leagueId, message },
      { leagueId },
    );
    renderer.renderRecommendationView(recommendationView, model);
  }

  function recommendationErrorMessage(error, leagueId) {
    if (error?.name === 'AbortError') {
      return 'Recommendation timed out. Confirm the loopback server and selected ranking source, then retry.';
    }
    const message = String(error?.message || 'Recommendation unavailable.');
    const hasReusableProfile = savedProfiles.some((profile) => profile.leagueId !== leagueId);
    if (hasReusableProfile && profileClient.isProfileReuseRecommendationError(message)) {
      return `${message}. This draft has no matching local profile; choose a saved source profile above and select Use for this draft.`;
    }
    return message;
  }

  async function refreshAnalysis(leagueId) {
    resetAnalysisPanels({ preserveCockpit: activeCockpitLeagueId === leagueId });
    setControlsDisabled(true);
    setProfileControlsDisabled(true);
    setStatus('Refreshing live context and running bounded specialist scoring…', 'loading');
    try {
      const data = await client.fetchDraftRecommendationsForLeagueId(leagueId, {
        endpoint: '/draft-recommendation',
        strategy: document.getElementById('strategy').value,
        count: document.getElementById('count').value,
        rankingCount: document.getElementById('ranking-count').value,
        simulations: document.getElementById('simulations').value,
        timeoutMs: 30000,
      });
      if (leagueInput.value.trim() !== leagueId) {
        setStatus('League selection changed; the prior response was discarded.', 'warning');
        return;
      }
      const model = render(data, leagueId);
      setStatus(...requestStatusForModel(model, leagueId));
    } catch (error) {
      const message = recommendationErrorMessage(error, leagueId);
      resetAnalysisPanels();
      renderClientError(message, leagueId);
      setStatus(message, 'error');
    } finally {
      setControlsDisabled(false);
      setProfileControlsDisabled(false);
    }
  }

  profileReuseForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!profileReuseForm.reportValidity() || !form.reportValidity()) return;
    const sourceLeagueId = profileSourceLeague.value;
    const leagueId = leagueInput.value.trim();
    const sourceProfile = savedProfiles.find((profile) => (
      profile.leagueId === sourceLeagueId && profile.leagueId !== leagueId
    ));
    if (!sourceProfile) {
      setProfileStatus(
        'Profile reuse needs a source',
        'Choose one saved profile explicitly before using it for this draft.',
        { kind: 'error', label: 'No profile was selected or changed' },
      );
      return;
    }

    let shouldRefresh = false;
    setControlsDisabled(true);
    setProfileControlsDisabled(true);
    setProfileStatus(
      'Reusing saved profile…',
      `Copying rankings and league settings from league ${sourceLeagueId} to league ${leagueId}. Recorded picks will not be copied.`,
      { kind: 'loading', label: 'Binding exact draft identity' },
    );
    try {
      const result = await profileClient.bindDraftProfile(sourceLeagueId, leagueId);
      if (leagueInput.value.trim() !== leagueId) {
        throw new Error('League selection changed during profile reuse; no recommendation was requested.');
      }
      await loadSavedProfiles();
      const rankingCount = safeResponseCount(result.rankingCount, sourceProfile.rankingCount);
      setProfileStatus(
        'Saved profile ready for this draft',
        `${rankingCount} ranked players and league settings copied from league ${sourceLeagueId} to league ${leagueId}. Recorded picks stayed with league ${leagueId}.`,
        profileClient.describeProfileFreshness(result.asOf || sourceProfile.asOf),
      );
      shouldRefresh = true;
    } catch (error) {
      const message = String(
        error?.message || 'The saved profile could not be used for this draft.',
      ).slice(0, 240);
      const crossSport = /different sport/i.test(message);
      setProfileStatus(
        crossSport ? 'Saved profile sport does not match this draft' : 'Profile reuse failed',
        crossSport
          ? `${profileClient.profileSportLabel(sourceProfile.sport)} source rejected: its sport does not match the server-resolved target draft. Choose a source labeled for the same sport.`
          : message,
        { kind: 'error', label: 'No profile change was confirmed' },
      );
    } finally {
      setControlsDisabled(false);
      setProfileControlsDisabled(false);
    }
    if (shouldRefresh) await refreshAnalysis(leagueId);
  });

  profileDefaultForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!profileDefaultForm.reportValidity()) return;
    const sport = profileDefaultSport.value;
    const sourceLeagueId = profileDefaultSource.value;
    const sourceProfile = savedProfiles.find((profile) => (
      profile.sport === sport && profile.leagueId === sourceLeagueId
    ));
    if (!sourceProfile) {
      setProfileDefaultStatus(
        'Default profile needs a source',
        'Choose one saved profile for the selected sport. No default was changed.',
        'error',
      );
      return;
    }

    setControlsDisabled(true);
    setProfileControlsDisabled(true);
    setProfileDefaultStatus(
      `Setting ${profileClient.profileSportLabel(sport)} default…`,
      `League ${sourceLeagueId} will apply only to future profileless Yahoo drafts for this sport.`,
      'loading',
    );
    try {
      await profileClient.setDefaultDraftProfile(sport, sourceLeagueId);
      await loadSavedProfiles();
      const freshness = profileClient.describeProfileFreshness(
        sourceProfile.asOf || sourceProfile.importedAt,
      );
      setProfileDefaultStatus(
        `${profileClient.profileSportLabel(sport)} default saved`,
        `${freshness.label}. League ${sourceLeagueId} will apply to future profileless Yahoo drafts, including real drafts and mocks. Existing exact profiles win, and picks are never copied.`,
        freshness.kind,
      );
    } catch (error) {
      setProfileDefaultStatus(
        'Default profile was not changed',
        String(error?.message || 'The profile default could not be saved.').slice(0, 240),
        'error',
      );
    } finally {
      setControlsDisabled(false);
      setProfileControlsDisabled(false);
    }
  });

  document.getElementById('clear-profile-default-button').addEventListener('click', async () => {
    const sport = profileDefaultSport.value;
    const currentDefault = defaultForSport(sport);
    if (!currentDefault) return;

    setControlsDisabled(true);
    setProfileControlsDisabled(true);
    setProfileDefaultStatus(
      `Clearing ${profileClient.profileSportLabel(sport)} default…`,
      'Already bound exact profiles will remain unchanged.',
      'loading',
    );
    try {
      await profileClient.setDefaultDraftProfile(sport, null);
      await loadSavedProfiles();
      setProfileDefaultStatus(
        `${profileClient.profileSportLabel(sport)} default cleared`,
        'Future profileless drafts will remain unbound. Existing exact profiles and recorded picks were not changed.',
      );
    } catch (error) {
      setProfileDefaultStatus(
        'Default profile was not cleared',
        String(error?.message || 'The profile default could not be cleared.').slice(0, 240),
        'error',
      );
    } finally {
      setControlsDisabled(false);
      setProfileControlsDisabled(false);
    }
  });

  profileForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!profileForm.reportValidity() || !form.reportValidity()) return;
    const leagueId = leagueInput.value.trim();
    const fileInput = document.getElementById('draft-profile-file');
    const file = fileInput.files?.[0];
    if (!file) {
      setProfileStatus(
        'Profile import failed',
        'Choose a supported DraftSheets, CSV, or JSON file.',
        { kind: 'error', label: 'Source date unknown' },
      );
      return;
    }

    setControlsDisabled(true);
    setProfileControlsDisabled(true);
    setProfileStatus(
      'Importing local profile…',
      `Validating rankings and binding them to league ${leagueId}.`,
      { kind: 'loading', label: 'Source freshness pending' },
    );
    try {
      let result;
      let format;
      let countFallback = null;
      let asOf = null;
      let truncatedCount = 0;
      if (isXlsxFile(file)) {
        result = await profileClient.saveDraftProfileXlsx(file, leagueId, {
          leagueSettings: selectedLeagueSettings(),
        });
        format = 'xlsx';
        asOf = typeof result.asOf === 'string' ? result.asOf : null;
      } else {
        if (Number(file.size) > 2_000_000) {
          throw new Error('The ranking file must be 2 MB or smaller.');
        }
        const parsed = profileClient.parseDraftProfileFile(await file.text(), file.name);
        format = parsed.format;
        countFallback = parsed.rankings.length;
        truncatedCount = parsed.truncatedCount;
        asOf = document.getElementById('profile-as-of').value || parsed.asOf || null;
        result = await profileClient.saveDraftProfile({
          leagueId,
          format,
          asOf,
          rankings: parsed.rankings,
          leagueSettings: selectedLeagueSettings(),
        });
      }
      if (leagueInput.value.trim() !== leagueId) {
        throw new Error('League selection changed during import; verify the active profile before drafting.');
      }
      const rankingCount = safeResponseCount(result.rankingCount, countFallback);
      const detailParts = [
        rankingCount ? `${rankingCount} ranked players` : 'Ranked players saved',
        `bound to league ${leagueId}`,
      ];
      if (truncatedCount) detailParts.push(`top 500 retained; ${truncatedCount} lower ranks omitted`);
      const freshness = profileClient.describeProfileFreshness(result.asOf || asOf);
      await loadSavedProfiles();
      setProfileStatus(profileSourceTitle(format), detailParts.join(' · '), freshness);
      fileInput.value = '';
    } catch (error) {
      setProfileStatus(
        'Profile import failed',
        String(error?.message || 'The local draft profile could not be imported.').slice(0, 240),
        { kind: 'error', label: 'No profile change was confirmed' },
      );
    } finally {
      setControlsDisabled(false);
      setProfileControlsDisabled(false);
    }
  });

  document.getElementById('queue-add').addEventListener('click', () => {
    if (!latestCockpitData || !cockpitPreferences) return;
    const key = document.getElementById('queue-candidate').value;
    const candidate = cockpitCandidateMap(latestCockpitData).get(key);
    if (!candidate) return;
    cockpitPreferences = cockpit.addToWatchlist(cockpitPreferences, candidate);
    saveCockpitPreferences();
    renderCockpit(latestCockpitData, activeCockpitLeagueId);
  });

  document.getElementById('cockpit-panel').addEventListener('click', (event) => {
    const button = event.target.closest?.('[data-cockpit-action]');
    if (!button || !latestCockpitData || !cockpitPreferences) return;
    const action = button.dataset.cockpitAction;
    const key = button.dataset.playerKey;
    const candidates = cockpitCandidateMap(latestCockpitData);
    if (action === 'add') {
      cockpitPreferences = cockpit.addToWatchlist(cockpitPreferences, candidates.get(key));
    } else if (action === 'remove') {
      cockpitPreferences = cockpit.removeFromWatchlist(cockpitPreferences, key);
    } else if (action === 'up' || action === 'down') {
      cockpitPreferences = cockpit.moveWatchlistEntry(
        cockpitPreferences,
        key,
        action === 'up' ? -1 : 1,
      );
    } else if (action === 'compare') {
      cockpitPreferences = cockpit.toggleComparison(cockpitPreferences, key);
    } else if (action === 'compare-candidate') {
      cockpitPreferences = cockpit.addToWatchlist(cockpitPreferences, candidates.get(key));
      cockpitPreferences = cockpit.toggleComparison(cockpitPreferences, key);
    } else {
      return;
    }
    saveCockpitPreferences();
    renderCockpit(latestCockpitData, activeCockpitLeagueId);
  });

  document.getElementById('position-filters').addEventListener('click', (event) => {
    const button = event.target.closest?.('[data-position]');
    if (!button || !latestCockpitData) return;
    selectedPosition = String(button.dataset.position || 'OVERALL');
    renderPositionBoard(latestCockpitData);
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const leagueId = leagueInput.value.trim();
    await refreshAnalysis(leagueId);
  });

  profileSourceLeague.addEventListener('change', updateProfileReuseControls);
  profileDefaultSport.addEventListener('change', () => {
    renderProfileDefaultSourceChoices();
    showProfileDefaultStatus();
  });
  profileDefaultSource.addEventListener('change', updateProfileDefaultControls);
  leagueInput.addEventListener('input', () => {
    renderSavedProfileChoices();
    if (savedProfilesLoaded) showSelectedProfileStatus();
  });
  prefillLeagueFromFragment();

  if (!client || !viewModels || !renderer || !cockpit) {
    setStatus('Shared recommendation UI modules are unavailable.', 'error');
    setControlsDisabled(true);
    return;
  }
  if (!profileClient) {
    setProfileStatus(
      'Local profile import unavailable',
      'The dashboard profile module did not load.',
      { kind: 'error', label: 'Source date unknown' },
    );
    setProfileControlsDisabled(true);
  } else {
    void loadSavedProfiles();
  }
}());
