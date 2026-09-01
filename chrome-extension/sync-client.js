(function initDraftSyncClient(globalScope) {
  'use strict';

  const DEFAULT_ENDPOINT = 'http://127.0.0.1:8765/draft-sync';
  const DEFAULT_RESET_ENDPOINT = 'http://127.0.0.1:8765/draft-reset';

  function validIsoTimestamp(value) {
    return typeof value === 'string' &&
      value.length <= 40 &&
      /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) &&
      Number.isFinite(Date.parse(value));
  }

  function resetSnapshot(session) {
    const sport = typeof session?.sport === 'string' ? session.sport : '';
    const leagueId = typeof session?.leagueId === 'string' ? session.leagueId : '';
    const teamId = typeof session?.teamId === 'string' ? session.teamId : '';
    const sessionKey = typeof session?.sessionKey === 'string' ? session.sessionKey : '';
    if (
      !/^[a-z0-9_-]{1,16}$/i.test(sport) ||
      !/^\d{1,32}$/.test(leagueId) ||
      !/^\d{1,32}$/.test(teamId) ||
      sessionKey !== `${sport}:${leagueId}`
    ) {
      throw new Error('The active draft identity is missing or inconsistent.');
    }
    const expectedGeneratedAt = session?.lastSyncedAt === undefined
      ? session?.updatedAt
      : session.lastSyncedAt;
    if (!validIsoTimestamp(expectedGeneratedAt)) {
      throw new Error('The active draft has no valid synced snapshot timestamp. Rescan before reset.');
    }
    return {
      schemaVersion: 1,
      source: 'yahoo-draft-recorder',
      expectedGeneratedAt,
      draft: { sport, leagueId, teamId, sessionKey },
    };
  }

  function boundedError(value) {
    const message = typeof value === 'string' ? value.trim() : '';
    return message ? message.slice(0, 240) : '';
  }

  function safeResetEndpoint(value) {
    const endpoint = value || DEFAULT_RESET_ENDPOINT;
    let parsed;
    try {
      parsed = new URL(endpoint);
    } catch (_error) {
      throw new Error('Reset endpoint must be the loopback draft-reset route.');
    }
    if (
      parsed.protocol !== 'http:' ||
      parsed.hostname !== '127.0.0.1' ||
      parsed.pathname !== '/draft-reset' ||
      parsed.username ||
      parsed.password ||
      parsed.search ||
      parsed.hash
    ) {
      throw new Error('Reset endpoint must be the loopback draft-reset route.');
    }
    return parsed.href;
  }

  async function waitUntilAfterReset(resetAt, options = {}) {
    if (!validIsoTimestamp(resetAt)) return false;
    const now = options.now || Date.now;
    const target = Date.parse(resetAt);
    const waitMs = target - now() + 1;
    if (waitMs <= 0) return true;
    const maximumWaitMs = Number.isInteger(options.maximumWaitMs)
      ? Math.max(0, Math.min(options.maximumWaitMs, 5000))
      : 3000;
    if (waitMs > maximumWaitMs) return false;
    const delay = options.delay || ((milliseconds) => new Promise((resolve) => {
      globalScope.setTimeout(resolve, milliseconds);
    }));
    await delay(waitMs);
    return now() > target;
  }

  async function syncDraftContext(context, options = {}) {
    const fetchImpl = options.fetchImpl || globalScope.fetch?.bind(globalScope);
    if (!fetchImpl) throw new Error('Fetch is unavailable');

    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    const timeout = globalScope.setTimeout?.(() => controller?.abort(), options.timeoutMs || 2000);
    try {
      const response = await fetchImpl(options.endpoint || DEFAULT_ENDPOINT, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Yahoo-Draft-Recorder': '1',
        },
        body: JSON.stringify(context),
        signal: controller?.signal,
      });
      if (!response.ok) {
        throw new Error(`MCP draft sync returned HTTP ${response.status || 'error'}`);
      }
      return await response.json();
    } finally {
      if (timeout !== undefined) globalScope.clearTimeout?.(timeout);
    }
  }

  async function resetDraftSession(session, options = {}) {
    const fetchImpl = options.fetchImpl || globalScope.fetch?.bind(globalScope);
    if (!fetchImpl) throw new Error('Fetch is unavailable');
    const request = resetSnapshot(session);
    const endpoint = safeResetEndpoint(options.endpoint);
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    const timeout = globalScope.setTimeout?.(() => controller?.abort(), options.timeoutMs || 3000);
    try {
      const response = await fetchImpl(endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Yahoo-Draft-Recorder': '1',
        },
        body: JSON.stringify(request),
        cache: 'no-store',
        credentials: 'omit',
        signal: controller?.signal,
      });
      let result;
      try {
        result = await response.json();
      } catch (_error) {
        result = null;
      }
      if (!response.ok) {
        const detail = boundedError(result?.error || result?.message);
        const error = new Error(detail || `MCP draft reset returned HTTP ${response.status || 'error'}`);
        error.status = response.status;
        throw error;
      }
      if (
        !result ||
        result.status !== 'ok' ||
        result.sessionKey !== request.draft.sessionKey ||
        result.profilePreserved !== true ||
        !validIsoTimestamp(result.resetAt)
      ) {
        throw new Error('The local server returned an invalid reset acknowledgement. Browser state was not cleared.');
      }
      return {
        status: 'ok',
        sessionKey: result.sessionKey,
        resetAt: result.resetAt,
        profilePreserved: true,
      };
    } finally {
      if (timeout !== undefined) globalScope.clearTimeout?.(timeout);
    }
  }

  const api = {
    DEFAULT_ENDPOINT,
    DEFAULT_RESET_ENDPOINT,
    resetDraftSession,
    resetSnapshot,
    safeResetEndpoint,
    syncDraftContext,
    validIsoTimestamp,
    waitUntilAfterReset,
  };
  globalScope.YahooDraftSyncClient = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
