(function initDraftStorage(globalScope) {
  'use strict';

  const LEGACY_SESSIONS_KEY = 'yahooDraftRecorderSessions';
  const SESSION_PREFIX = 'yahooDraftRecorderSession:';
  const TOMBSTONE_PREFIX = 'yahooDraftRecorderSessionDeleted:';
  const PENDING_REPAIR_PREFIX = 'yahooDraftRecorderPendingRepair:';
  const PENDING_RESET_PREFIX = 'yahooDraftRecorderPendingReset:';
  const LOCK_PORT_NAME = 'yahoo-draft-recorder-lock-v1';
  const LOCK_PROTOCOL_VERSION = 1;

  function suffix(prefix, sessionKey) {
    return `${prefix}${encodeURIComponent(sessionKey)}`;
  }

  function decodeSessionKey(storageKey, prefix) {
    if (!storageKey.startsWith(prefix)) return null;
    try {
      return decodeURIComponent(storageKey.slice(prefix.length));
    } catch (_error) {
      return null;
    }
  }

  function timestampMillis(value) {
    if (typeof value !== 'string' || !/(?:Z|[+-]\d{2}:\d{2})$/i.test(value)) return null;
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function normalizedPlayerKey(value) {
    if (typeof value !== 'string') return null;
    const playerKey = value.trim();
    return /^[1-9]\d{0,9}\.p\.[1-9]\d{0,9}$/.test(playerKey) ? playerKey : null;
  }

  function sanitizeSessionPlayerKeys(session) {
    if (!session || typeof session !== 'object' || !Array.isArray(session.picks)) return session;
    let changed = false;
    const picks = session.picks.map((pick) => {
      if (!pick || typeof pick !== 'object' || !Object.hasOwn(pick, 'playerKey')) return pick;
      const playerKey = normalizedPlayerKey(pick.playerKey);
      if (playerKey === pick.playerKey) return pick;
      changed = true;
      const sanitized = { ...pick };
      delete sanitized.playerKey;
      if (playerKey) sanitized.playerKey = playerKey;
      return sanitized;
    });
    return changed ? { ...session, picks } : session;
  }

  function sanitizePendingRepairPlayerKeys(pending) {
    if (!pending || typeof pending !== 'object' || !pending.session) return pending;
    const session = sanitizeSessionPlayerKeys(pending.session);
    return session === pending.session ? pending : { ...pending, session };
  }

  function isTabBlockedByReset(contentLoadedAt, resetAt, allowedResetAt) {
    const resetTime = timestampMillis(resetAt);
    if (resetTime === null) return false;
    const loadedTime = timestampMillis(contentLoadedAt);
    if (loadedTime === null) return true;
    if (loadedTime > resetTime) return false;
    return allowedResetAt !== resetAt;
  }

  function createDraftStorage(extensionApi, options = {}) {
    const operationLock = options.operationLock ||
      createSessionOperationLock(extensionApi?.native?.runtime);
    if (
      typeof operationLock.run !== 'function' ||
      typeof operationLock.runGlobal !== 'function'
    ) {
      throw new Error('A complete extension operation lock is required.');
    }
    const sessionStorageKey = (sessionKey) => suffix(SESSION_PREFIX, sessionKey);
    const tombstoneStorageKey = (sessionKey) => suffix(TOMBSTONE_PREFIX, sessionKey);
    const pendingRepairStorageKey = (sessionKey) => suffix(PENDING_REPAIR_PREFIX, sessionKey);
    const pendingResetStorageKey = (sessionKey) => suffix(PENDING_RESET_PREFIX, sessionKey);

    async function readValue(key) {
      const result = await extensionApi.storageGet(key);
      return result?.[key];
    }

    function clearedAtFromTombstone(tombstone) {
      const value = typeof tombstone === 'string' ? tombstone : tombstone?.clearedAt;
      return typeof value === 'string' && Number.isFinite(Date.parse(value)) ? value : null;
    }

    function isVisibleAfterClear(session, tombstone) {
      if (!session || typeof session !== 'object' || tombstone === true) return false;
      const clearedAt = clearedAtFromTombstone(tombstone);
      if (!clearedAt) return true;
      const updatedAt = Date.parse(session.updatedAt);
      return Number.isFinite(updatedAt) && updatedAt > Date.parse(clearedAt);
    }

    async function getSession(sessionKey) {
      const tombstone = await readValue(tombstoneStorageKey(sessionKey));
      const perSession = await readValue(sessionStorageKey(sessionKey));
      if (perSession && typeof perSession === 'object') {
        return isVisibleAfterClear(perSession, tombstone)
          ? sanitizeSessionPlayerKeys(perSession)
          : null;
      }
      const legacy = await readValue(LEGACY_SESSIONS_KEY);
      const legacySession = legacy?.[sessionKey];
      return isVisibleAfterClear(legacySession, tombstone)
        ? sanitizeSessionPlayerKeys(legacySession)
        : null;
    }

    async function getResetAt(sessionKey) {
      const tombstone = await readValue(tombstoneStorageKey(sessionKey));
      return clearedAtFromTombstone(tombstone);
    }

    async function listSessions() {
      const all = await extensionApi.storageGet(null);
      const sessions = {};
      const tombstones = new Map();
      for (const [key, value] of Object.entries(all || {})) {
        const deletedSessionKey = decodeSessionKey(key, TOMBSTONE_PREFIX);
        if (deletedSessionKey && (value === true || clearedAtFromTombstone(value))) {
          tombstones.set(deletedSessionKey, value);
        }
      }
      for (const [key, value] of Object.entries(all || {})) {
        const sessionKey = decodeSessionKey(key, SESSION_PREFIX);
        if (sessionKey && isVisibleAfterClear(value, tombstones.get(sessionKey))) {
          sessions[sessionKey] = sanitizeSessionPlayerKeys(value);
        }
      }
      const legacy = all?.[LEGACY_SESSIONS_KEY];
      if (legacy && typeof legacy === 'object') {
        for (const [sessionKey, session] of Object.entries(legacy)) {
          if (!(sessionKey in sessions) && isVisibleAfterClear(session, tombstones.get(sessionKey))) {
            sessions[sessionKey] = sanitizeSessionPlayerKeys(session);
          }
        }
      }
      return sessions;
    }

    function setSession(sessionKey, session) {
      return extensionApi.storageSet({
        [sessionStorageKey(sessionKey)]: sanitizeSessionPlayerKeys(session),
      });
    }

    function checkLease(lease) {
      lease?.throwIfLost?.();
    }

    async function clearSessionData(sessionKey, clearedAt, sessionLease) {
      checkLease(sessionLease);
      await extensionApi.storageSet({
        [sessionStorageKey(sessionKey)]: null,
        [tombstoneStorageKey(sessionKey)]: { clearedAt },
        [pendingRepairStorageKey(sessionKey)]: null,
      });
      checkLease(sessionLease);
      await operationLock.runGlobal(async (globalLease) => {
        checkLease(sessionLease);
        checkLease(globalLease);
        const legacy = await readValue(LEGACY_SESSIONS_KEY);
        checkLease(sessionLease);
        checkLease(globalLease);
        if (!legacy || typeof legacy !== 'object' || !(sessionKey in legacy)) return;
        const remaining = { ...legacy };
        delete remaining[sessionKey];
        checkLease(sessionLease);
        checkLease(globalLease);
        if (Object.keys(remaining).length === 0) {
          await extensionApi.storageRemove(LEGACY_SESSIONS_KEY);
        } else {
          await extensionApi.storageSet({ [LEGACY_SESSIONS_KEY]: remaining });
        }
        checkLease(sessionLease);
        checkLease(globalLease);
      });
      checkLease(sessionLease);
    }

    function clearSession(sessionKey, clearedAt = new Date().toISOString()) {
      return operationLock.run(
        sessionKey,
        (sessionLease) => clearSessionData(sessionKey, clearedAt, sessionLease),
      );
    }

    function finalizeReset(sessionKey, resetAt, sessionLease) {
      return clearSessionData(sessionKey, resetAt, sessionLease);
    }

    async function getPendingRepair(sessionKey) {
      const pending = await readValue(pendingRepairStorageKey(sessionKey));
      return pending && typeof pending === 'object'
        ? sanitizePendingRepairPlayerKeys(pending)
        : null;
    }

    function setPendingRepair(sessionKey, pending) {
      return extensionApi.storageSet({
        [pendingRepairStorageKey(sessionKey)]: sanitizePendingRepairPlayerKeys(pending),
      });
    }

    function clearPendingRepair(sessionKey) {
      return extensionApi.storageSet({ [pendingRepairStorageKey(sessionKey)]: null });
    }

    async function getPendingReset(sessionKey) {
      const pending = await readValue(pendingResetStorageKey(sessionKey));
      return pending && typeof pending === 'object' ? pending : null;
    }

    function setPendingReset(sessionKey, pending) {
      return extensionApi.storageSet({ [pendingResetStorageKey(sessionKey)]: pending });
    }

    function clearPendingReset(sessionKey) {
      return extensionApi.storageSet({ [pendingResetStorageKey(sessionKey)]: null });
    }

    return {
      clearPendingRepair,
      clearPendingReset,
      clearSession,
      finalizeReset,
      getPendingRepair,
      getPendingReset,
      getResetAt,
      getSession,
      listSessions,
      setPendingRepair,
      setPendingReset,
      setSession,
    };
  }

  function isRelevantStorageChange(changes) {
    return Object.keys(changes || {}).some((key) => (
      key === LEGACY_SESSIONS_KEY ||
      key.startsWith(SESSION_PREFIX) ||
      key.startsWith(TOMBSTONE_PREFIX) ||
      key.startsWith(PENDING_REPAIR_PREFIX) ||
      key.startsWith(PENDING_RESET_PREFIX)
    ));
  }

  function createLockError(message, code) {
    const error = new Error(message);
    error.name = 'YahooDraftLockError';
    error.code = code;
    return error;
  }

  function isExactBrokerReply(message, type) {
    return message?.schemaVersion === LOCK_PROTOCOL_VERSION &&
      message?.type === type &&
      Object.keys(message).length === 2;
  }

  function runWithBroker(runtime, acquireMessage, operation, options = {}) {
    if (!runtime || typeof runtime.connect !== 'function') {
      return Promise.reject(new Error('The extension lock broker is unavailable.'));
    }
    if (typeof operation !== 'function') {
      return Promise.reject(new TypeError('A lock operation function is required.'));
    }
    const AbortControllerImpl = globalScope.AbortController;
    if (typeof AbortControllerImpl !== 'function') {
      return Promise.reject(new Error('AbortController is required for brokered operations.'));
    }
    const heartbeatMs = Number.isInteger(options.heartbeatMs)
      ? Math.max(5, Math.min(options.heartbeatMs, 1000))
      : 1000;
    const acquireTimeoutMs = Number.isInteger(options.acquireTimeoutMs)
      ? Math.max(5, Math.min(options.acquireTimeoutMs, 30000))
      : 15000;
    const holdTimeoutMs = Number.isInteger(options.holdTimeoutMs)
      ? Math.max(5, Math.min(options.holdTimeoutMs, 30000))
      : 15000;

    return new Promise((resolve, reject) => {
      let port;
      let granted = false;
      let settled = false;
      let portDisconnected = false;
      let leaseLostError = null;
      let heartbeatTimer;
      let acquireTimer;
      let holdTimer;
      const controller = new AbortControllerImpl();
      const lease = {
        signal: controller.signal,
        throwIfLost() {
          if (leaseLostError) throw leaseLostError;
        },
      };

      function clearTimers() {
        if (heartbeatTimer !== undefined) globalScope.clearInterval?.(heartbeatTimer);
        if (acquireTimer !== undefined) globalScope.clearTimeout?.(acquireTimer);
        if (holdTimer !== undefined) globalScope.clearTimeout?.(holdTimer);
      }

      function disconnect() {
        try { port?.disconnect(); } catch (_error) { /* already disconnected */ }
      }

      function rejectBeforeGrant(error) {
        if (settled) return;
        settled = true;
        clearTimers();
        if (!portDisconnected) disconnect();
        reject(error);
      }

      function loseLease(error) {
        if (leaseLostError) return;
        leaseLostError = error;
        if (heartbeatTimer !== undefined) globalScope.clearInterval?.(heartbeatTimer);
        if (acquireTimer !== undefined) globalScope.clearTimeout?.(acquireTimer);
        try { controller.abort(error); } catch (_abortError) { controller.abort(); }
        if (!granted) rejectBeforeGrant(error);
      }

      function settleOperation(callback, value) {
        if (settled) return;
        settled = true;
        clearTimers();
        if (granted && !portDisconnected) {
          try {
            port.postMessage({ schemaVersion: LOCK_PROTOCOL_VERSION, type: 'release' });
          } catch (_error) {
            portDisconnected = true;
          }
        }
        if (!portDisconnected) disconnect();
        if (leaseLostError) reject(leaseLostError);
        else callback(value);
      }

      function sendKeepalive() {
        if (settled || portDisconnected) return;
        try {
          port.postMessage({ schemaVersion: LOCK_PROTOCOL_VERSION, type: 'keepalive' });
        } catch (_error) {
          portDisconnected = true;
          loseLease(createLockError(
            'The extension lock lease was lost; reconcile before retrying.',
            'LOCK_LEASE_LOST',
          ));
        }
      }

      try {
        port = runtime.connect({ name: LOCK_PORT_NAME });
        port.onMessage.addListener((message) => {
          if (
            !granted &&
            isExactBrokerReply(message, 'granted')
          ) {
            granted = true;
            if (acquireTimer !== undefined) globalScope.clearTimeout?.(acquireTimer);
            holdTimer = globalScope.setTimeout?.(() => loseLease(createLockError(
              'The extension lock lease timed out; reconcile before retrying.',
              'LOCK_HOLD_TIMEOUT',
            )), holdTimeoutMs);
            Promise.resolve()
              .then(() => operation(lease))
              .then(
                (value) => settleOperation(resolve, value),
                (error) => settleOperation(reject, error),
              );
            return;
          }
          if (isExactBrokerReply(message, 'rejected')) {
            const error = createLockError(
              'The extension lock broker rejected this request.',
              'LOCK_REJECTED',
            );
            if (granted) loseLease(error);
            else rejectBeforeGrant(error);
          }
        });
        port.onDisconnect.addListener(() => {
          if (settled) return;
          portDisconnected = true;
          const error = createLockError(
            granted
              ? 'The extension lock lease was lost; reconcile before retrying.'
              : 'The extension lock broker disconnected before granting the operation.',
            granted ? 'LOCK_LEASE_LOST' : 'LOCK_DISCONNECTED',
          );
          if (granted) loseLease(error);
          else rejectBeforeGrant(error);
        });
        heartbeatTimer = globalScope.setInterval?.(sendKeepalive, heartbeatMs);
        acquireTimer = globalScope.setTimeout?.(() => {
          rejectBeforeGrant(createLockError(
            'Timed out waiting for the extension lock broker.',
            'LOCK_ACQUIRE_TIMEOUT',
          ));
        }, acquireTimeoutMs);
        port.postMessage(acquireMessage);
      } catch (error) {
        rejectBeforeGrant(error);
      }
    });
  }

  function createSessionOperationLock(runtime, options = {}) {
    if (!runtime || typeof runtime.connect !== 'function') {
      throw new Error('The extension lock broker is unavailable.');
    }
    return {
      run(sessionKey, operation) {
        if (!/^[a-z0-9_-]{1,16}:\d{1,32}$/i.test(sessionKey || '')) {
          return Promise.reject(new Error('A valid Yahoo sessionKey is required for locking.'));
        }
        return runWithBroker(runtime, {
          schemaVersion: LOCK_PROTOCOL_VERSION,
          type: 'acquire',
          scope: 'session',
          sessionKey,
        }, operation, options);
      },
      runGlobal(operation) {
        return runWithBroker(runtime, {
          schemaVersion: LOCK_PROTOCOL_VERSION,
          type: 'acquire',
          scope: 'legacy-storage',
        }, operation, options);
      },
    };
  }

  const api = {
    LEGACY_SESSIONS_KEY,
    PENDING_REPAIR_PREFIX,
    PENDING_RESET_PREFIX,
    SESSION_PREFIX,
    TOMBSTONE_PREFIX,
    createDraftStorage,
    createSessionOperationLock,
    isRelevantStorageChange,
    isTabBlockedByReset,
  };
  globalScope.YahooDraftStorage = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
