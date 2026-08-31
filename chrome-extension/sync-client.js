(function initDraftSyncClient(globalScope) {
  'use strict';

  const DEFAULT_ENDPOINT = 'http://127.0.0.1:8765/draft-sync';

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

  const api = { DEFAULT_ENDPOINT, syncDraftContext };
  globalScope.YahooDraftSyncClient = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
