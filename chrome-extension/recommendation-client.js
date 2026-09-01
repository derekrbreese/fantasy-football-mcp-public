(function initRecommendationClient(globalScope) {
  'use strict';

  const DEFAULT_RECOMMENDATION_ENDPOINT = 'http://127.0.0.1:8765/draft-recommendation';
  const STRATEGIES = new Set(['conservative', 'balanced', 'aggressive']);

  function clampInteger(value, minimum, maximum, fallback) {
    let number;
    if (typeof value === 'number') number = value;
    else if (typeof value === 'string' && /^[+-]?\d+(?:\.\d+)?$/.test(value.trim())) {
      number = Number(value);
    } else return fallback;
    if (!Number.isFinite(number)) return fallback;
    return Math.max(minimum, Math.min(maximum, Math.trunc(number)));
  }

  function explicitLeagueIdentity(session) {
    if (!session) throw new Error('Choose a Yahoo league before requesting recommendations.');
    const sport = typeof session.sport === 'string' ? session.sport : '';
    const leagueId = typeof session.leagueId === 'string' ? session.leagueId : '';
    const sessionKey = typeof session.sessionKey === 'string' ? session.sessionKey : '';
    if (!/^[a-z0-9_-]{1,16}$/i.test(sport) || !/^\d{1,32}$/.test(leagueId)) {
      throw new Error('The selected session does not contain a valid Yahoo league identity.');
    }
    if (sessionKey !== `${sport}:${leagueId}`) {
      throw new Error('The selected session identity does not match its Yahoo league.');
    }
    return { leagueId };
  }

  function explicitLeagueId(value) {
    const leagueId = typeof value === 'string' ? value : '';
    if (!/^\d{1,32}$/.test(leagueId)) {
      throw new Error('Choose a valid Yahoo league ID before requesting recommendations.');
    }
    return leagueId;
  }

  function buildRecommendationRequestForLeagueId(value, preferences = {}) {
    const leagueId = explicitLeagueId(value);
    const strategy = STRATEGIES.has(preferences.strategy) ? preferences.strategy : 'balanced';
    return {
      schemaVersion: 1,
      leagueId,
      strategy,
      count: clampInteger(preferences.count, 1, 20, 5),
      rankingCount: clampInteger(preferences.rankingCount, 25, 500, 250),
      simulations: clampInteger(preferences.simulations, 0, 512, 256),
    };
  }

  function buildRecommendationRequest(session, preferences = {}) {
    const { leagueId } = explicitLeagueIdentity(session);
    return buildRecommendationRequestForLeagueId(leagueId, preferences);
  }

  function safeRecommendationEndpoint(value) {
    const endpoint = value || DEFAULT_RECOMMENDATION_ENDPOINT;
    if (endpoint === '/draft-recommendation') return endpoint;
    let parsed;
    try {
      parsed = new URL(endpoint);
    } catch (_error) {
      throw new Error('Recommendation endpoint must be a loopback URL.');
    }
    const loopbackHosts = new Set(['127.0.0.1', 'localhost', '[::1]']);
    if (
      parsed.protocol !== 'http:' ||
      !loopbackHosts.has(parsed.hostname) ||
      parsed.pathname !== '/draft-recommendation' ||
      parsed.search ||
      parsed.hash
    ) {
      throw new Error('Recommendation endpoint must be the loopback draft-recommendation route.');
    }
    return parsed.href;
  }

  function shortErrorMessage(value) {
    const message = typeof value === 'string' ? value.trim() : '';
    return message ? message.slice(0, 240) : '';
  }

  async function fetchDraftRecommendationsForLeagueId(value, options = {}) {
    const leagueId = explicitLeagueId(value);
    const endpoint = safeRecommendationEndpoint(options.endpoint);
    const fetchImpl = options.fetchImpl || globalScope.fetch?.bind(globalScope);
    if (!fetchImpl) throw new Error('Fetch is unavailable.');

    const AbortControllerImpl = options.AbortControllerImpl || globalScope.AbortController;
    const controller = AbortControllerImpl ? new AbortControllerImpl() : null;
    const externalSignal = options.signal;
    const abortFromExternal = () => controller?.abort();
    if (externalSignal?.aborted) abortFromExternal();
    else externalSignal?.addEventListener?.('abort', abortFromExternal, { once: true });
    const timeoutMs = clampInteger(options.timeoutMs, 250, 30000, 10000);
    const timeout = globalScope.setTimeout?.(() => controller?.abort(), timeoutMs);
    try {
      const response = await fetchImpl(endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Fantasy-Draft-UI': '1',
        },
        body: JSON.stringify(buildRecommendationRequestForLeagueId(leagueId, options)),
        cache: 'no-store',
        credentials: 'omit',
        signal: controller?.signal || externalSignal,
      });
      let result;
      try {
        result = await response.json();
      } catch (_error) {
        result = null;
      }
      if (!response.ok) {
        const detail = shortErrorMessage(result?.message || result?.error);
        throw new Error(`Recommendation server returned HTTP ${response.status || 'error'}${detail ? `: ${detail}` : ''}`);
      }
      if (!result || typeof result !== 'object' || Array.isArray(result)) {
        throw new Error('Recommendation server returned an invalid JSON response.');
      }
      if (String(result.leagueId || '') !== leagueId) {
        throw new Error('Recommendation response did not match the selected Yahoo league.');
      }
      return result;
    } finally {
      externalSignal?.removeEventListener?.('abort', abortFromExternal);
      if (timeout !== undefined) globalScope.clearTimeout?.(timeout);
    }
  }

  async function fetchDraftRecommendations(session, options = {}) {
    const { leagueId } = explicitLeagueIdentity(session);
    return fetchDraftRecommendationsForLeagueId(leagueId, options);
  }

  const api = {
    DEFAULT_RECOMMENDATION_ENDPOINT,
    buildRecommendationRequestForLeagueId,
    buildRecommendationRequest,
    fetchDraftRecommendationsForLeagueId,
    fetchDraftRecommendations,
    safeRecommendationEndpoint,
  };
  globalScope.YahooDraftRecommendationClient = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
