const test = require('node:test');
const assert = require('node:assert/strict');

const { syncDraftContext } = require('../sync-client.js');

test('posts agent context to the loopback MCP sync endpoint', async () => {
  let request;
  const result = await syncDraftContext(
    { schemaVersion: 1, draft: { sessionKey: 'f1:123' }, picks: [] },
    {
      fetchImpl: async (url, options) => {
        request = { url, options };
        return { ok: true, json: async () => ({ status: 'ok', pickCount: 0 }) };
      },
    },
  );

  assert.equal(request.url, 'http://127.0.0.1:8765/draft-sync');
  assert.equal(request.options.method, 'POST');
  assert.equal(request.options.headers['X-Yahoo-Draft-Recorder'], '1');
  assert.equal(JSON.parse(request.options.body).draft.sessionKey, 'f1:123');
  assert.deepEqual(result, { status: 'ok', pickCount: 0 });
});

test('reports a useful error when the MCP server is unavailable', async () => {
  await assert.rejects(
    syncDraftContext(
      { draft: { sessionKey: 'f1:123' }, picks: [] },
      { fetchImpl: async () => { throw new Error('connection refused'); } },
    ),
    /connection refused/,
  );
});
