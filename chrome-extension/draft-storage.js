(function initDraftStorage(globalScope) {
  'use strict';

  const LEGACY_SESSIONS_KEY = 'yahooDraftRecorderSessions';
  const SESSION_PREFIX = 'yahooDraftRecorderSession:';
  const TOMBSTONE_PREFIX = 'yahooDraftRecorderSessionDeleted:';
  const PENDING_REPAIR_PREFIX = 'yahooDraftRecorderPendingRepair:';
  const PENDING_RESET_PREFIX = 'yahooDraftRecorderPendingReset:';

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

  function isTabBlockedByReset(contentLoadedAt, resetAt, allowedResetAt) {
    const resetTime = timestampMillis(resetAt);
    if (resetTime === null) return false;
    const loadedTime = timestampMillis(contentLoadedAt);
    if (loadedTime === null) return true;
    if (loadedTime > resetTime) return false;
    return allowedResetAt !== resetAt;
  }

  function createDraftStorage(extensionApi, options = {}) {
    const operationLock = createSessionOperationLock(options.lockManager);
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
        return isVisibleAfterClear(perSession, tombstone) ? perSession : null;
      }
      const legacy = await readValue(LEGACY_SESSIONS_KEY);
      const legacySession = legacy?.[sessionKey];
      return isVisibleAfterClear(legacySession, tombstone) ? legacySession : null;
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
          sessions[sessionKey] = value;
        }
      }
      const legacy = all?.[LEGACY_SESSIONS_KEY];
      if (legacy && typeof legacy === 'object') {
        for (const [sessionKey, session] of Object.entries(legacy)) {
          if (!(sessionKey in sessions) && isVisibleAfterClear(session, tombstones.get(sessionKey))) {
            sessions[sessionKey] = session;
          }
        }
      }
      return sessions;
    }

    function setSession(sessionKey, session) {
      return extensionApi.storageSet({
        [sessionStorageKey(sessionKey)]: session,
      });
    }

    async function clearSessionData(sessionKey, clearedAt) {
      await extensionApi.storageSet({
        [sessionStorageKey(sessionKey)]: null,
        [tombstoneStorageKey(sessionKey)]: { clearedAt },
        [pendingRepairStorageKey(sessionKey)]: null,
      });
      await operationLock.runGlobal(async () => {
        const legacy = await readValue(LEGACY_SESSIONS_KEY);
        if (!legacy || typeof legacy !== 'object' || !(sessionKey in legacy)) return;
        const remaining = { ...legacy };
        delete remaining[sessionKey];
        if (Object.keys(remaining).length === 0) {
          await extensionApi.storageRemove(LEGACY_SESSIONS_KEY);
        } else {
          await extensionApi.storageSet({ [LEGACY_SESSIONS_KEY]: remaining });
        }
      });
    }

    function clearSession(sessionKey, clearedAt = new Date().toISOString()) {
      return clearSessionData(sessionKey, clearedAt);
    }

    function finalizeReset(sessionKey, resetAt) {
      return clearSessionData(sessionKey, resetAt);
    }

    async function getPendingRepair(sessionKey) {
      const pending = await readValue(pendingRepairStorageKey(sessionKey));
      return pending && typeof pending === 'object' ? pending : null;
    }

    function setPendingRepair(sessionKey, pending) {
      return extensionApi.storageSet({ [pendingRepairStorageKey(sessionKey)]: pending });
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

  function createSessionOperationLock(lockManager) {
    return {
      run(sessionKey, operation) {
        if (!lockManager || typeof lockManager.request !== 'function') return operation();
        const lockName = `yahoo-draft-recorder:${encodeURIComponent(sessionKey)}`;
        return lockManager.request(lockName, operation);
      },
      runGlobal(operation) {
        if (!lockManager || typeof lockManager.request !== 'function') return operation();
        return lockManager.request('yahoo-draft-recorder:legacy-storage', operation);
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
