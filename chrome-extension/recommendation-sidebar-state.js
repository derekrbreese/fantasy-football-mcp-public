(function initRecommendationSidebarState(globalScope) {
  'use strict';

  function validSession(session) {
    const sport = typeof session?.sport === 'string' ? session.sport : '';
    const leagueId = typeof session?.leagueId === 'string' ? session.leagueId : '';
    return /^[a-z0-9_-]{1,16}$/i.test(sport) &&
      /^\d{1,32}$/.test(leagueId) &&
      session?.sessionKey === `${sport}:${leagueId}`;
  }

  function leagueChoices(sessions) {
    if (!sessions || typeof sessions !== 'object' || Array.isArray(sessions)) return [];
    return Object.values(sessions)
      .filter(validSession)
      .map((session) => ({
        sessionKey: session.sessionKey,
        leagueId: session.leagueId,
        label: `League ${session.leagueId}`,
      }))
      .sort((left, right) => left.leagueId.localeCompare(right.leagueId, undefined, { numeric: true }));
  }

  function resolveExplicitSelection(sessions, activeDiagnostics) {
    const key = typeof activeDiagnostics?.sessionKey === 'string'
      ? activeDiagnostics.sessionKey
      : '';
    return validSession(sessions?.[key]) ? key : null;
  }

  function recommendationStillMatchesSelection(
    requestedKey,
    selectedKey,
    requestedSession,
    currentSession,
    response,
  ) {
    const snapshot = typeof requestedSession?.updatedAt === 'string'
      ? requestedSession.updatedAt
      : '';
    return requestedKey === selectedKey &&
      validSession(requestedSession) &&
      validSession(currentSession) &&
      requestedSession.sessionKey === requestedKey &&
      currentSession.sessionKey === requestedKey &&
      Boolean(snapshot) &&
      currentSession.updatedAt === snapshot &&
      String(response?.leagueId || '') === requestedSession.leagueId &&
      (response?.status === 'error' || response?.generatedAt === snapshot);
  }

  function createRecommendationRequestGuard(options = {}) {
    const AbortControllerImpl = options.AbortControllerImpl || globalScope.AbortController;
    let generation = 0;
    let activeController = null;

    function cancel() {
      generation += 1;
      activeController?.abort();
      activeController = null;
    }

    function begin(session) {
      cancel();
      activeController = AbortControllerImpl ? new AbortControllerImpl() : null;
      return {
        generation,
        sessionKey: session?.sessionKey,
        updatedAt: session?.updatedAt,
        signal: activeController?.signal,
      };
    }

    function requestStillMatchesSelection(token, selectedKey) {
      return Boolean(token) &&
        token.generation === generation &&
        token.sessionKey === selectedKey &&
        token.signal?.aborted !== true;
    }

    function finish(token) {
      if (token?.generation === generation) activeController = null;
    }

    return { begin, cancel, finish, requestStillMatchesSelection };
  }

  const api = {
    createRecommendationRequestGuard,
    leagueChoices,
    recommendationStillMatchesSelection,
    resolveExplicitSelection,
    validSession,
  };
  globalScope.YahooDraftRecommendationSidebarState = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
