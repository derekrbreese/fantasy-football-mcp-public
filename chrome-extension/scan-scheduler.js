(function initDraftScanScheduler(globalScope) {
  'use strict';

  function boundedDelay(value, fallback, minimum, maximum) {
    if (!Number.isInteger(value)) return fallback;
    return Math.max(minimum, Math.min(value, maximum));
  }

  function createBoundedScanScheduler(options = {}) {
    if (typeof options.run !== 'function') {
      throw new Error('A scan operation is required.');
    }
    const setTimeoutImpl = options.setTimeoutImpl || globalScope.setTimeout?.bind(globalScope);
    const clearTimeoutImpl = options.clearTimeoutImpl || globalScope.clearTimeout?.bind(globalScope);
    if (!setTimeoutImpl || !clearTimeoutImpl) {
      throw new Error('Bounded scan scheduling is unavailable.');
    }
    const quietDelayMs = boundedDelay(options.quietDelayMs, 400, 50, 2_000);
    const maximumWaitMs = Math.max(
      quietDelayMs,
      boundedDelay(options.maximumWaitMs, 1_000, 100, 5_000),
    );
    let quietTimer = null;
    let maximumTimer = null;
    let dirty = false;
    let running = false;
    let activePromise = null;

    function clearTimers() {
      if (quietTimer !== null) clearTimeoutImpl(quietTimer);
      if (maximumTimer !== null) clearTimeoutImpl(maximumTimer);
      quietTimer = null;
      maximumTimer = null;
    }

    function launch() {
      if (running) {
        dirty = true;
        return activePromise;
      }
      if (!dirty) return Promise.resolve();
      clearTimers();
      dirty = false;
      running = true;
      const operation = Promise.resolve()
        .then(() => options.run())
        .catch((error) => options.onError?.(error))
        .finally(() => {
          running = false;
          activePromise = null;
          if (dirty) launch();
        });
      activePromise = operation;
      return operation;
    }

    function request() {
      dirty = true;
      if (running) return;
      if (quietTimer !== null) clearTimeoutImpl(quietTimer);
      quietTimer = setTimeoutImpl(launch, quietDelayMs);
      if (maximumTimer === null) {
        maximumTimer = setTimeoutImpl(launch, maximumWaitMs);
      }
    }

    function runNow() {
      dirty = true;
      clearTimers();
      return launch();
    }

    return { request, runNow };
  }

  const api = { createBoundedScanScheduler };
  globalScope.YahooDraftScanScheduler = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
