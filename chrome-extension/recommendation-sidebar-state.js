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

  function isRefreshShortcut(event) {
    const tagName = typeof event?.target?.tagName === 'string'
      ? event.target.tagName.toUpperCase()
      : '';
    const isEditable = event?.target?.isContentEditable === true ||
      ['INPUT', 'SELECT', 'TEXTAREA'].includes(tagName);
    return String(event?.key || '').toLowerCase() === 'r' &&
      event?.repeat !== true &&
      event?.altKey !== true &&
      event?.ctrlKey !== true &&
      event?.metaKey !== true &&
      event?.shiftKey !== true &&
      !isEditable;
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

  function storageChangeAffectsSession(changes, sessionKey) {
    if (
      !changes ||
      typeof changes !== 'object' ||
      typeof sessionKey !== 'string' ||
      !/^[a-z0-9_-]{1,16}:\d{1,32}$/i.test(sessionKey)
    ) return false;
    const encoded = encodeURIComponent(sessionKey);
    const perSessionKeys = [
      'yahooDraftRecorderSession:',
      'yahooDraftRecorderSessionDeleted:',
      'yahooDraftRecorderPendingRepair:',
      'yahooDraftRecorderPendingReset:',
    ];
    if (perSessionKeys.some((prefix) => `${prefix}${encoded}` in changes)) return true;
    const legacy = changes.yahooDraftRecorderSessions;
    const oldSessions = legacy?.oldValue;
    const newSessions = legacy?.newValue;
    const hadSession = Boolean(
      oldSessions &&
      typeof oldSessions === 'object' &&
      Object.prototype.hasOwnProperty.call(oldSessions, sessionKey),
    );
    const hasSession = Boolean(
      newSessions &&
      typeof newSessions === 'object' &&
      Object.prototype.hasOwnProperty.call(newSessions, sessionKey),
    );
    if (hadSession !== hasSession) return true;
    if (!hadSession) return false;
    return sessionSnapshot(oldSessions[sessionKey]) !== sessionSnapshot(newSessions[sessionKey]);
  }

  function sessionSnapshot(session) {
    const updatedAt = typeof session?.updatedAt === 'string' ? session.updatedAt : '';
    if (
      !validSession(session) ||
      !updatedAt ||
      updatedAt.length > 64 ||
      !Number.isFinite(Date.parse(updatedAt))
    ) return null;
    return `${session.sessionKey}\u0000${updatedAt}`;
  }

  function createRecommendationAutoRefreshScheduler(options = {}) {
    const requestedDelay = Number.isInteger(options.delayMs) ? options.delayMs : 350;
    const delayMs = Math.max(100, Math.min(requestedDelay, 1_000));
    const setTimeoutImpl = options.setTimeoutImpl || globalScope.setTimeout?.bind(globalScope);
    const clearTimeoutImpl = options.clearTimeoutImpl || globalScope.clearTimeout?.bind(globalScope);
    let timer = null;
    let generation = 0;
    let lastRequestedSnapshot = null;

    function cancelScheduled() {
      generation += 1;
      if (timer !== null && clearTimeoutImpl) clearTimeoutImpl(timer);
      timer = null;
    }

    function markRequested(session) {
      const snapshot = sessionSnapshot(session);
      if (snapshot) lastRequestedSnapshot = snapshot;
      return Boolean(snapshot);
    }

    function schedule(sessionKey) {
      if (
        typeof sessionKey !== 'string' ||
        !/^[a-z0-9_-]{1,16}:\d{1,32}$/i.test(sessionKey)
      ) return false;
      cancelScheduled();
      options.cancelInFlight?.();
      if (!setTimeoutImpl) {
        options.onError?.(new Error('Automatic refresh scheduling is unavailable.'));
        return false;
      }
      const scheduledGeneration = generation;
      timer = setTimeoutImpl(async () => {
        timer = null;
        if (
          scheduledGeneration !== generation ||
          options.selectedSessionKey?.() !== sessionKey
        ) return;
        try {
          const loaded = await options.reloadSessions?.();
          if (
            loaded === false ||
            scheduledGeneration !== generation ||
            options.selectedSessionKey?.() !== sessionKey
          ) return;
          const session = options.sessionForKey?.(sessionKey);
          const snapshot = sessionSnapshot(session);
          if (!snapshot || snapshot === lastRequestedSnapshot) {
            options.onUnchanged?.(session);
            return;
          }
          lastRequestedSnapshot = snapshot;
          await options.refresh?.();
        } catch (error) {
          options.onError?.(error);
        }
      }, delayMs);
      return true;
    }

    return { cancelScheduled, markRequested, schedule };
  }

  const api = {
    createRecommendationAutoRefreshScheduler,
    createRecommendationRequestGuard,
    isRefreshShortcut,
    leagueChoices,
    recommendationStillMatchesSelection,
    resolveExplicitSelection,
    storageChangeAffectsSession,
    validSession,
  };
  globalScope.YahooDraftRecommendationSidebarState = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
