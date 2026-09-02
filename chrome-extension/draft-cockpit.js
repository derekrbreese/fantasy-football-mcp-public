(function initDraftCockpit(globalScope) {
  'use strict';

  const MAX_WATCHLIST = 20;
  const MAX_COMPARISON = 3;
  const STORAGE_PREFIX = 'yahooDraftCockpitPreferences:v1:';

  function safeText(value, maximum = 120) {
    if (typeof value !== 'string' && typeof value !== 'number') return '';
    return String(value)
      .replace(/[\u0000-\u001F\u007F]/g, '')
      .trim()
      .slice(0, maximum);
  }

  function normalizedTokens(value) {
    return safeText(value, 120)
      .normalize('NFKD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLowerCase()
      .match(/[a-z0-9]+/g) || [];
  }

  function normalizedPosition(value) {
    const position = safeText(value, 16).toUpperCase();
    if (position === 'DEF' || position === 'D/ST') return 'DST';
    return position.replace(/[^A-Z]/g, '').slice(0, 16);
  }

  function normalizedTeam(value) {
    return safeText(value, 16).toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 16);
  }

  function playerKey(value) {
    const player = value?.player && typeof value.player === 'object' ? value.player : value;
    const name = normalizedTokens(player?.name || player?.player).join('-');
    const position = normalizedPosition(player?.position);
    const team = normalizedTeam(player?.team || player?.nflTeam);
    return name && position ? `${name}|${position}|${team}`.toLowerCase().slice(0, 200) : '';
  }

  function sanitizeCandidate(value) {
    const player = value?.player && typeof value.player === 'object' ? value.player : value;
    const name = safeText(player?.name || player?.player, 120);
    const position = normalizedPosition(player?.position);
    const team = normalizedTeam(player?.team || player?.nflTeam);
    const key = playerKey({ name, position, team });
    if (!key || !name) return null;
    const score = typeof value?.score === 'number' && Number.isFinite(value.score)
      ? Math.max(0, Math.min(100, value.score))
      : (typeof value?.overallScore === 'number' && Number.isFinite(value.overallScore)
        ? Math.max(0, Math.min(100, value.overallScore))
        : null);
    return {
      key,
      name,
      position,
      team,
      tier: safeText(value?.tier || value?.specialistDetails?.value?.tier, 24) || 'unknown',
      score,
    };
  }

  function sanitizePreferences(value) {
    const raw = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    const watchlist = [];
    const seen = new Set();
    for (const item of Array.isArray(raw.watchlist) ? raw.watchlist : []) {
      const candidate = sanitizeCandidate(item);
      if (!candidate || seen.has(candidate.key)) continue;
      watchlist.push(candidate);
      seen.add(candidate.key);
      if (watchlist.length >= MAX_WATCHLIST) break;
    }
    const comparisonKeys = [];
    for (const value of Array.isArray(raw.comparisonKeys) ? raw.comparisonKeys : []) {
      const key = safeText(value, 200).toLowerCase();
      if (!/^[a-z0-9|_-]{1,200}$/.test(key) || comparisonKeys.includes(key)) continue;
      comparisonKeys.push(key);
      if (comparisonKeys.length >= MAX_COMPARISON) break;
    }
    return {
      watchlist,
      comparisonKeys,
      notificationsEnabled: raw.notificationsEnabled === true,
      lastNotificationKey: safeText(raw.lastNotificationKey, 160),
    };
  }

  function storageKey(leagueId) {
    const id = safeText(leagueId, 32);
    if (!/^\d{1,32}$/.test(id)) throw new Error('A valid Yahoo league ID is required.');
    return `${STORAGE_PREFIX}${id}`;
  }

  function addToWatchlist(preferences, value) {
    const next = sanitizePreferences(preferences);
    const candidate = sanitizeCandidate(value);
    if (!candidate || next.watchlist.some((item) => item.key === candidate.key)) return next;
    if (next.watchlist.length < MAX_WATCHLIST) next.watchlist.push(candidate);
    return next;
  }

  function removeFromWatchlist(preferences, key) {
    const next = sanitizePreferences(preferences);
    const safeKey = safeText(key, 200).toLowerCase();
    next.watchlist = next.watchlist.filter((item) => item.key !== safeKey);
    next.comparisonKeys = next.comparisonKeys.filter((item) => item !== safeKey);
    return next;
  }

  function moveWatchlistEntry(preferences, key, direction) {
    const next = sanitizePreferences(preferences);
    const safeKey = safeText(key, 200).toLowerCase();
    const index = next.watchlist.findIndex((item) => item.key === safeKey);
    if (index < 0) return next;
    const target = Math.max(0, Math.min(next.watchlist.length - 1, index + (direction < 0 ? -1 : 1)));
    if (target === index) return next;
    const [item] = next.watchlist.splice(index, 1);
    next.watchlist.splice(target, 0, item);
    return next;
  }

  function samePlayer(candidate, pick) {
    const left = normalizedTokens(candidate?.name);
    const right = normalizedTokens(pick?.player || pick?.name);
    const candidatePosition = normalizedPosition(candidate?.position);
    const pickPosition = normalizedPosition(pick?.position);
    const candidateTeam = normalizedTeam(candidate?.team);
    const pickTeam = normalizedTeam(pick?.nflTeam || pick?.team);
    if (
      candidatePosition === 'DST' && pickPosition === 'DST' &&
      candidateTeam && candidateTeam === pickTeam
    ) return true;
    if (!left.length || !right.length) return false;
    if (left.join('|') === right.join('|')) return true;
    return left.at(-1) === right.at(-1) &&
      left[0][0] === right[0][0] &&
      candidatePosition && candidatePosition === pickPosition &&
      candidateTeam && candidateTeam === pickTeam;
  }

  function reconcileWatchlist(watchlist, picks) {
    const safe = sanitizePreferences({ watchlist }).watchlist;
    const draftPicks = Array.isArray(picks) ? picks.slice(0, 500) : [];
    return safe.map((candidate) => {
      const draftedPick = draftPicks.find((pick) => samePlayer(candidate, pick));
      return {
        ...candidate,
        drafted: Boolean(draftedPick),
        pickNumber: Number.isInteger(draftedPick?.pickNumber) ? draftedPick.pickNumber : null,
      };
    });
  }

  function toggleComparison(preferences, key) {
    const next = sanitizePreferences(preferences);
    const safeKey = safeText(key, 200).toLowerCase();
    if (!next.watchlist.some((item) => item.key === safeKey)) return next;
    if (next.comparisonKeys.includes(safeKey)) {
      next.comparisonKeys = next.comparisonKeys.filter((item) => item !== safeKey);
    } else if (next.comparisonKeys.length < MAX_COMPARISON) {
      next.comparisonKeys.push(safeKey);
    }
    return next;
  }

  function shouldNotify(preferences, response, session) {
    const safe = sanitizePreferences(preferences);
    const until = response?.state?.picksUntilUserTurn;
    const generatedAt = safeText(response?.generatedAt, 64);
    const sessionKey = safeText(session?.sessionKey, 64);
    const exactRevision = generatedAt && generatedAt === safeText(session?.updatedAt, 64);
    const authoritative = response?.state?.health?.complete === true;
    const fresh = response?.state?.health?.fresh === true;
    if (
      !safe.notificationsEnabled || !exactRevision || !authoritative || !fresh ||
      !sessionKey || ![0, 1].includes(until)
    ) return { notify: false, key: '', title: '', message: '' };
    const key = `${sessionKey}|${generatedAt}|${until}`.slice(0, 160);
    if (safe.lastNotificationKey === key) {
      return { notify: false, key, title: '', message: '' };
    }
    const primary = safeText(response?.recommendations?.[0]?.player?.name, 120);
    return {
      notify: true,
      key,
      title: until === 0 ? 'You are on the clock' : 'You are next',
      message: primary
        ? `Current recommendation: ${primary}. Advisory only.`
        : 'Open Draft Assistant for the latest advisory recommendation.',
    };
  }

  function markNotified(preferences, key) {
    const next = sanitizePreferences(preferences);
    next.lastNotificationKey = safeText(key, 160);
    return next;
  }

  const api = {
    MAX_COMPARISON,
    MAX_WATCHLIST,
    addToWatchlist,
    markNotified,
    moveWatchlistEntry,
    playerKey,
    reconcileWatchlist,
    removeFromWatchlist,
    sanitizeCandidate,
    sanitizePreferences,
    shouldNotify,
    storageKey,
    toggleComparison,
  };
  globalScope.YahooDraftCockpit = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
