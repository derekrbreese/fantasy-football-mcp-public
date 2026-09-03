(function initializeDashboardLiveRefresh(globalScope) {
  'use strict';

  const REVISION_ENDPOINT = '/draft-revision';
  const REVISION_FIELDS = new Set([
    'schemaVersion',
    'status',
    'leagueId',
    'sessionKey',
    'generatedAt',
    'pickCount',
    'latestOverallPick',
    'captureBlocked',
  ]);

  function boundedInteger(value, fallback, minimum, maximum) {
    if (!Number.isInteger(value)) return fallback;
    return Math.max(minimum, Math.min(value, maximum));
  }

  function validLeagueId(value) {
    return typeof value === 'string' && /^\d{1,32}$/.test(value);
  }

  function validRevisionTimestamp(value) {
    return typeof value === 'string' &&
      value.length > 0 &&
      value.length <= 64 &&
      /T.*(?:Z|[+-]\d{2}:\d{2})$/i.test(value) &&
      Number.isFinite(Date.parse(value));
  }

  function sanitizeRevision(value, expectedLeagueId) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      throw new Error('Draft revision response has an invalid shape.');
    }
    const fields = Object.keys(value);
    if (fields.length !== REVISION_FIELDS.size || fields.some((field) => !REVISION_FIELDS.has(field))) {
      throw new Error('Draft revision response has an invalid shape.');
    }
    if (value.schemaVersion !== 1 || value.status !== 'success') {
      throw new Error('Draft revision response has an invalid status.');
    }
    if (!validLeagueId(value.leagueId) || value.leagueId !== expectedLeagueId) {
      throw new Error('Draft revision response did not match the selected league.');
    }
    const sessionMatch = /^([A-Za-z0-9_-]{1,32}):(\d{1,32})$/.exec(value.sessionKey || '');
    if (!sessionMatch || sessionMatch[2] !== value.leagueId) {
      throw new Error('Draft revision response has an invalid session identity.');
    }
    if (!validRevisionTimestamp(value.generatedAt)) {
      throw new Error('Draft revision response has an invalid timestamp.');
    }
    if (
      !Number.isInteger(value.pickCount) || value.pickCount < 0 || value.pickCount > 500 ||
      !Number.isInteger(value.latestOverallPick) ||
      value.latestOverallPick < 0 || value.latestOverallPick > 500 ||
      typeof value.captureBlocked !== 'boolean'
    ) {
      throw new Error('Draft revision response has invalid counters.');
    }
    return {
      schemaVersion: 1,
      status: 'success',
      leagueId: value.leagueId,
      sessionKey: value.sessionKey,
      generatedAt: value.generatedAt,
      pickCount: value.pickCount,
      latestOverallPick: value.latestOverallPick,
      captureBlocked: value.captureBlocked,
    };
  }

  async function fetchDraftRevision(leagueId, options = {}) {
    if (!validLeagueId(leagueId)) {
      throw new Error('Choose a valid Yahoo league ID before checking draft revision.');
    }
    const endpoint = options.endpoint || REVISION_ENDPOINT;
    if (endpoint !== REVISION_ENDPOINT) {
      throw new Error('Draft revision endpoint must use the same-origin private route.');
    }
    const fetchImpl = options.fetchImpl || globalScope.fetch?.bind(globalScope);
    if (!fetchImpl) throw new Error('Fetch is unavailable.');
    const AbortControllerImpl = options.AbortControllerImpl || globalScope.AbortController;
    const controller = AbortControllerImpl ? new AbortControllerImpl() : null;
    const externalSignal = options.signal;
    const abortFromExternal = () => controller?.abort();
    if (externalSignal?.aborted) abortFromExternal();
    else externalSignal?.addEventListener?.('abort', abortFromExternal, { once: true });
    const timeoutMs = boundedInteger(options.timeoutMs, 2_000, 250, 5_000);
    const timeout = globalScope.setTimeout?.(() => controller?.abort(), timeoutMs);
    try {
      const response = await fetchImpl(endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Fantasy-Draft-UI': '1',
        },
        body: JSON.stringify({ schemaVersion: 1, leagueId }),
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
        const detail = typeof result?.message === 'string'
          ? result.message.trim().slice(0, 160)
          : '';
        throw new Error(
          `Draft revision server returned HTTP ${response.status || 'error'}${detail ? `: ${detail}` : ''}`,
        );
      }
      return sanitizeRevision(result, leagueId);
    } finally {
      externalSignal?.removeEventListener?.('abort', abortFromExternal);
      if (timeout !== undefined) globalScope.clearTimeout?.(timeout);
    }
  }

  function sameRevision(left, right) {
    return Boolean(left && right) &&
      left.leagueId === right.leagueId &&
      left.sessionKey === right.sessionKey &&
      left.generatedAt === right.generatedAt &&
      left.pickCount === right.pickCount &&
      left.latestOverallPick === right.latestOverallPick &&
      left.captureBlocked === right.captureBlocked;
  }

  function createLiveDraftPoller(options = {}) {
    if (typeof options.fetchRevision !== 'function' || typeof options.refresh !== 'function') {
      throw new Error('Live draft polling requires revision and recommendation callbacks.');
    }
    const setTimeoutImpl = options.setTimeoutImpl || globalScope.setTimeout?.bind(globalScope);
    const clearTimeoutImpl = options.clearTimeoutImpl || globalScope.clearTimeout?.bind(globalScope);
    if (!setTimeoutImpl || !clearTimeoutImpl) {
      throw new Error('Live draft polling timers are unavailable.');
    }
    const pollIntervalMs = boundedInteger(options.pollIntervalMs, 500, 100, 2_000);
    const quietDelayMs = boundedInteger(options.quietDelayMs, 400, 100, 1_000);
    const maximumBackoffMs = Math.max(
      pollIntervalMs,
      boundedInteger(options.maximumBackoffMs, 5_000, 500, 10_000),
    );
    let generation = 0;
    let pollTimer = null;
    let refreshTimer = null;
    let pollInFlight = false;
    let refreshInFlight = false;
    let restartPending = false;
    let failureCount = 0;
    let refreshFailureCount = 0;
    let latestRevision = null;
    let renderedRevision = null;
    let suppressedRevision = null;
    let pendingRevision = null;

    function enabledAndVisible() {
      return options.enabled?.() === true && options.visible?.() !== false;
    }

    function selectedLeagueId() {
      const value = options.leagueId?.();
      return validLeagueId(value) ? value : null;
    }

    function active() {
      return enabledAndVisible() && Boolean(selectedLeagueId());
    }

    function clearTimer(name) {
      const identifier = name === 'poll' ? pollTimer : refreshTimer;
      if (identifier !== null) clearTimeoutImpl(identifier);
      if (name === 'poll') pollTimer = null;
      else refreshTimer = null;
    }

    function schedulePoll(delay, expectedGeneration = generation) {
      clearTimer('poll');
      if (!active() || expectedGeneration !== generation) return;
      pollTimer = setTimeoutImpl(() => {
        pollTimer = null;
        void poll(expectedGeneration);
      }, Math.max(0, delay));
    }

    function scheduleRefresh(expectedGeneration = generation, delay = quietDelayMs) {
      if (refreshInFlight || !pendingRevision) return;
      clearTimer('refresh');
      if (!active() || expectedGeneration !== generation) return;
      refreshTimer = setTimeoutImpl(() => {
        refreshTimer = null;
        void refreshPending(expectedGeneration);
      }, Math.max(quietDelayMs, delay));
    }

    async function poll(expectedGeneration) {
      if (pollInFlight || expectedGeneration !== generation || !active()) return;
      const leagueId = selectedLeagueId();
      pollInFlight = true;
      let nextDelay = pollIntervalMs;
      try {
        const revision = sanitizeRevision(
          await options.fetchRevision(leagueId),
          leagueId,
        );
        if (
          expectedGeneration !== generation ||
          !active() ||
          selectedLeagueId() !== leagueId
        ) return;
        failureCount = 0;
        const changed = !sameRevision(latestRevision, revision);
        latestRevision = revision;
        if (
          changed &&
          !sameRevision(renderedRevision, revision) &&
          !sameRevision(suppressedRevision, revision)
        ) {
          const superseded = Boolean(pendingRevision) || refreshInFlight;
          refreshFailureCount = 0;
          pendingRevision = revision;
          options.pending?.(revision, { superseded });
          scheduleRefresh(expectedGeneration);
        }
      } catch (error) {
        if (expectedGeneration !== generation) return;
        failureCount += 1;
        nextDelay = Math.min(
          maximumBackoffMs,
          pollIntervalMs * (2 ** Math.min(failureCount - 1, 8)),
        );
        options.onError?.(error);
      } finally {
        pollInFlight = false;
        if (restartPending) {
          restartPending = false;
          schedulePoll(0);
        } else if (expectedGeneration === generation && active()) {
          schedulePoll(nextDelay, expectedGeneration);
        }
      }
    }

    async function refreshPending(expectedGeneration) {
      if (
        refreshInFlight ||
        expectedGeneration !== generation ||
        !active() ||
        !pendingRevision
      ) return;
      const target = pendingRevision;
      pendingRevision = null;
      refreshInFlight = true;
      let nextRefreshDelay = quietDelayMs;
      try {
        const outcome = await options.refresh(target);
        if (expectedGeneration !== generation || !active()) return;
        if (!sameRevision(latestRevision, target)) {
          pendingRevision = latestRevision;
          return;
        }
        if (outcome?.retry === true) {
          refreshFailureCount += 1;
          nextRefreshDelay = Math.min(
            maximumBackoffMs,
            quietDelayMs * (2 ** Math.min(refreshFailureCount - 1, 8)),
          );
          pendingRevision = latestRevision || target;
          return;
        }
        if (outcome?.applied === true) {
          refreshFailureCount = 0;
          renderedRevision = target;
          options.applied?.(target, outcome);
        }
      } catch (error) {
        if (expectedGeneration === generation) {
          if (error?.retryable === false) {
            refreshFailureCount = 0;
            suppressedRevision = target;
            options.terminal?.(error, target);
            return;
          }
          refreshFailureCount += 1;
          nextRefreshDelay = Math.min(
            maximumBackoffMs,
            quietDelayMs * (2 ** Math.min(refreshFailureCount - 1, 8)),
          );
          pendingRevision = latestRevision || target;
          options.onError?.(error);
        }
      } finally {
        refreshInFlight = false;
        if (pendingRevision && active()) {
          scheduleRefresh(generation, nextRefreshDelay);
        }
      }
    }

    function stop() {
      generation += 1;
      clearTimer('poll');
      clearTimer('refresh');
      restartPending = false;
      pendingRevision = null;
      latestRevision = null;
    }

    function restart() {
      generation += 1;
      clearTimer('poll');
      clearTimer('refresh');
      pendingRevision = null;
      latestRevision = null;
      failureCount = 0;
      refreshFailureCount = 0;
      if (!active()) return;
      if (pollInFlight) restartPending = true;
      else schedulePoll(0);
    }

    function markRendered(revision) {
      const leagueId = selectedLeagueId();
      if (!leagueId) return false;
      renderedRevision = sanitizeRevision(revision, leagueId);
      suppressedRevision = null;
      if (sameRevision(pendingRevision, renderedRevision)) pendingRevision = null;
      return true;
    }

    function forgetRendered() {
      renderedRevision = null;
      suppressedRevision = null;
    }

    function invalidate() {
      forgetRendered();
      restart();
    }

    function start() {
      restart();
    }

    function visibilityChanged() {
      restart();
    }

    return {
      forgetRendered,
      invalidate,
      markRendered,
      restart,
      start,
      stop,
      visibilityChanged,
    };
  }

  const api = {
    createLiveDraftPoller,
    fetchDraftRevision,
    sanitizeRevision,
    sameRevision,
  };
  globalScope.YahooDraftDashboardLiveRefresh = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
