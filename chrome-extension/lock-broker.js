(function initYahooDraftLockBroker(globalScope) {
  'use strict';

  const LOCK_PORT_NAME = 'yahoo-draft-recorder-lock-v1';
  const PROTOCOL_VERSION = 1;
  const LEASE_STORAGE_PREFIX = 'yahooDraftRecorderLockLease:';
  const DEFAULT_LEASE_MS = 4000;

  function hasExactKeys(value, expected) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    const actual = Object.keys(value).sort();
    return actual.length === expected.length &&
      expected.slice().sort().every((key, index) => actual[index] === key);
  }

  function lockNameFromAcquire(message) {
    if (
      message?.scope === 'session' &&
      hasExactKeys(message, ['schemaVersion', 'type', 'scope', 'sessionKey']) &&
      /^[a-z0-9_-]{1,16}:\d{1,32}$/i.test(message.sessionKey)
    ) {
      return `session:${message.sessionKey}`;
    }
    if (
      message?.scope === 'legacy-storage' &&
      hasExactKeys(message, ['schemaVersion', 'type', 'scope'])
    ) {
      return 'legacy-storage';
    }
    return null;
  }

  function isAcquireMessage(message) {
    return message?.schemaVersion === PROTOCOL_VERSION &&
      message?.type === 'acquire' &&
      Boolean(lockNameFromAcquire(message));
  }

  function isControlMessage(message, type) {
    return message?.schemaVersion === PROTOCOL_VERSION &&
      message?.type === type &&
      hasExactKeys(message, ['schemaVersion', 'type']);
  }

  function defaultNonce() {
    if (typeof globalScope.crypto?.randomUUID === 'function') {
      return globalScope.crypto.randomUUID();
    }
    const bytes = new Uint8Array(16);
    if (typeof globalScope.crypto?.getRandomValues !== 'function') {
      throw new Error('Secure randomness is unavailable for the lock fence.');
    }
    globalScope.crypto.getRandomValues(bytes);
    return [...bytes].map((value) => value.toString(16).padStart(2, '0')).join('');
  }

  function createMemoryLeaseStore() {
    const leases = new Map();
    return {
      async read(lockName) { return leases.get(lockName) || null; },
      async write(lockName, lease) { leases.set(lockName, lease); },
      async clear(lockName, nonce) {
        if (leases.get(lockName)?.nonce === nonce) leases.delete(lockName);
      },
    };
  }

  function createSessionLeaseStore(storageArea, options = {}) {
    if (!storageArea) throw new Error('Extension session storage is unavailable for lock fencing.');
    const promiseNative = options.promiseNative === true;
    const runtime = options.runtime;

    function storageKey(lockName) {
      return `${LEASE_STORAGE_PREFIX}${encodeURIComponent(lockName)}`;
    }

    function call(method, args) {
      if (promiseNative) return method.apply(storageArea, args);
      return new Promise((resolve, reject) => {
        method.apply(storageArea, [
          ...args,
          (result) => {
            const lastError = runtime?.lastError;
            if (lastError) reject(new Error('Extension session storage failed.'));
            else resolve(result);
          },
        ]);
      });
    }

    async function read(lockName) {
      const key = storageKey(lockName);
      const result = await call(storageArea.get, [key]);
      const lease = result?.[key];
      return lease &&
        typeof lease.nonce === 'string' && lease.nonce.length <= 80 &&
        Number.isFinite(lease.expiresAt)
        ? { nonce: lease.nonce, expiresAt: lease.expiresAt }
        : null;
    }

    async function write(lockName, lease) {
      const key = storageKey(lockName);
      await call(storageArea.set, [{ [key]: lease }]);
    }

    async function clear(lockName, nonce) {
      const key = storageKey(lockName);
      const current = await read(lockName);
      if (current?.nonce !== nonce) return;
      await call(storageArea.remove, [key]);
    }

    return { clear, read, write };
  }

  function createLockBroker(options = {}) {
    const queues = new Map();
    const grantsInFlight = new Set();
    const leaseStore = options.leaseStore || createMemoryLeaseStore();
    const now = options.now || Date.now;
    const makeNonce = options.makeNonce || defaultNonce;
    const leaseMs = Number.isInteger(options.leaseMs)
      ? Math.max(2000, Math.min(options.leaseMs, 10000))
      : DEFAULT_LEASE_MS;
    const expectedExtensionId = options.extensionId || null;

    function entryStillLeads(entry, lockName) {
      return !entry.removed && queues.get(lockName)?.[0] === entry;
    }

    function scheduleGrant(lockName) {
      if (!lockName || grantsInFlight.has(lockName)) return;
      grantsInFlight.add(lockName);
      Promise.resolve()
        .then(async () => {
          const queue = queues.get(lockName);
          const entry = queue?.[0];
          if (!entry || entry.granted || entry.removed) return;
          const existing = await leaseStore.read(lockName);
          if (!entryStillLeads(entry, lockName)) return;
          if (existing && existing.expiresAt > now()) return;

          const nonce = makeNonce();
          const lease = { nonce, expiresAt: now() + leaseMs };
          await leaseStore.write(lockName, lease);
          if (!entryStillLeads(entry, lockName)) {
            await leaseStore.clear(lockName, nonce);
            return;
          }
          entry.granted = true;
          entry.nonce = nonce;
          entry.leaseTask = Promise.resolve();
          try {
            entry.port.postMessage({ schemaVersion: PROTOCOL_VERSION, type: 'granted' });
          } catch (_error) {
            // The protected callback cannot have started when delivery of the
            // grant itself failed, so this holder can release its fence now.
            remove(entry, { clearLease: true });
          }
        })
        .catch(() => {
          const entry = queues.get(lockName)?.[0];
          if (entry) reject(entry.port);
        })
        .finally(() => grantsInFlight.delete(lockName));
    }

    function remove(entry, options = {}) {
      if (!entry?.lockName || entry.removed) return;
      entry.removed = true;
      const lockName = entry.lockName;
      const queue = queues.get(lockName);
      if (!queue) return;
      const wasHolder = queue[0] === entry;
      const index = queue.indexOf(entry);
      if (index >= 0) queue.splice(index, 1);
      if (queue.length === 0) queues.delete(lockName);

      const cleanup = wasHolder && entry.nonce
        ? (entry.leaseTask || Promise.resolve())
          .catch(() => undefined)
          .then(() => options.clearLease === true
            ? leaseStore.clear(lockName, entry.nonce)
            : undefined)
        : Promise.resolve();
      cleanup
        .catch(() => undefined)
        .finally(() => {
          if (wasHolder) scheduleGrant(lockName);
        });
    }

    function reject(port) {
      try {
        port.postMessage({ schemaVersion: PROTOCOL_VERSION, type: 'rejected' });
      } finally {
        try { port.disconnect(); } catch (_error) { /* already disconnected */ }
      }
    }

    function refresh(entry) {
      if (entry.removed) return;
      if (!entry.granted) {
        scheduleGrant(entry.lockName);
        return;
      }
      entry.leaseTask = (entry.leaseTask || Promise.resolve())
        .then(async () => {
          if (entry.removed) return;
          const existing = await leaseStore.read(entry.lockName);
          if (entry.removed) return;
          if (existing?.nonce !== entry.nonce) throw new Error('Lock fence was lost.');
          await leaseStore.write(entry.lockName, {
            nonce: entry.nonce,
            expiresAt: now() + leaseMs,
          });
        })
        .catch(() => {
          try { entry.port.disconnect(); } catch (_error) { /* already disconnected */ }
        });
    }

    function attachPort(port) {
      if (!port || port.name !== LOCK_PORT_NAME) return false;
      if (
        expectedExtensionId &&
        (port.sender?.id !== expectedExtensionId ||
          (port.sender?.tab && port.sender.frameId !== undefined && port.sender.frameId !== 0))
      ) {
        try { port.disconnect(); } catch (_error) { /* invalid sender */ }
        return false;
      }
      const entry = {
        port,
        lockName: null,
        granted: false,
        nonce: null,
        removed: false,
        leaseTask: Promise.resolve(),
      };

      port.onMessage.addListener((message) => {
        if (!entry.lockName) {
          if (!isAcquireMessage(message)) {
            reject(port);
            return;
          }
          const lockName = lockNameFromAcquire(message);
          entry.lockName = lockName;
          const queue = queues.get(lockName) || [];
          queue.push(entry);
          queues.set(lockName, queue);
          scheduleGrant(lockName);
          return;
        }
        if (isControlMessage(message, 'keepalive')) {
          refresh(entry);
          return;
        }
        if (!entry.granted || !isControlMessage(message, 'release')) {
          reject(port);
          return;
        }
        remove(entry, { clearLease: true });
        try { port.disconnect(); } catch (_error) { /* already disconnected */ }
      });
      port.onDisconnect.addListener(() => remove(entry));
      return true;
    }

    return { attachPort };
  }

  function installLockBroker(runtime, leaseStore, options = {}) {
    if (!runtime?.onConnect?.addListener) return null;
    const broker = createLockBroker({
      ...options,
      extensionId: runtime.id,
      leaseStore,
    });
    runtime.onConnect.addListener((port) => broker.attachPort(port));
    return broker;
  }

  const api = {
    LOCK_PORT_NAME,
    createLockBroker,
    createSessionLeaseStore,
    installLockBroker,
  };
  globalScope.YahooDraftLockBroker = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;

  const usingPromiseApi = typeof globalScope.browser !== 'undefined';
  const native = usingPromiseApi ? globalScope.browser : globalScope.chrome;
  if (native?.runtime && native?.storage?.session) {
    const leaseStore = createSessionLeaseStore(native.storage.session, {
      promiseNative: usingPromiseApi,
      runtime: native.runtime,
    });
    installLockBroker(native.runtime, leaseStore);
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
