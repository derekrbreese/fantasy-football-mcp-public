const test = require('node:test');
const assert = require('node:assert/strict');

const {
  resetDraftSession,
  syncDraftContext,
  waitUntilAfterReset,
} = require('../sync-client.js');

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

test('lock lease cancellation aborts an in-flight draft sync', async () => {
  const external = new AbortController();
  let requestSignal;
  const pending = syncDraftContext(
    { schemaVersion: 1, draft: { sessionKey: 'f1:123' }, picks: [] },
    {
      signal: external.signal,
      fetchImpl: async (_url, options) => {
        requestSignal = options.signal;
        await new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => {
          const error = new Error('aborted');
          error.name = 'AbortError';
          reject(error);
        }, { once: true }));
      },
    },
  );

  await Promise.resolve();
  external.abort();
  await Promise.resolve();

  assert.equal(requestSignal.aborted, true);
  await assert.rejects(pending, /aborted/);
});

test('resets only the allowlisted exact draft identity at the synced snapshot', async () => {
  let request;
  const result = await resetDraftSession({
    sport: 'f1',
    leagueId: '10547893',
    teamId: '6',
    sessionKey: 'f1:10547893',
    updatedAt: '2026-09-01T23:15:00.000Z',
    picks: [{ pickNumber: 1 }],
    url: 'https://example.test/?auth=secret',
  }, {
    fetchImpl: async (url, options) => {
      request = { url, options };
      return {
        ok: true,
        json: async () => ({
          status: 'ok',
          sessionKey: 'f1:10547893',
          resetAt: '2026-09-01T23:16:00.000Z',
          profilePreserved: true,
        }),
      };
    },
  });

  assert.equal(request.url, 'http://127.0.0.1:8765/draft-reset');
  assert.equal(request.options.method, 'POST');
  assert.deepEqual(request.options.headers, {
    'Content-Type': 'application/json',
    'X-Yahoo-Draft-Recorder': '1',
  });
  assert.deepEqual(JSON.parse(request.options.body), {
    schemaVersion: 1,
    source: 'yahoo-draft-recorder',
    expectedGeneratedAt: '2026-09-01T23:15:00.000Z',
    draft: {
      sport: 'f1',
      leagueId: '10547893',
      teamId: '6',
      sessionKey: 'f1:10547893',
    },
  });
  assert.deepEqual(result, {
    status: 'ok',
    sessionKey: 'f1:10547893',
    resetAt: '2026-09-01T23:16:00.000Z',
    profilePreserved: true,
  });
});

test('reset prefers the proven last-synced revision over a newer local scan time', async () => {
  let body;
  await resetDraftSession({
    sport: 'f1',
    leagueId: '10547893',
    teamId: '6',
    sessionKey: 'f1:10547893',
    updatedAt: '2026-09-01T23:16:00.000Z',
    lastSyncedAt: '2026-09-01T23:15:00.000Z',
  }, {
    fetchImpl: async (_url, options) => {
      body = JSON.parse(options.body);
      return {
        ok: true,
        json: async () => ({
          status: 'ok',
          sessionKey: 'f1:10547893',
          resetAt: '2026-09-01T23:17:00.000Z',
          profilePreserved: true,
        }),
      };
    },
  });

  assert.equal(body.expectedGeneratedAt, '2026-09-01T23:15:00.000Z');
});

test('reset refuses mismatched identities before making a request', async () => {
  let requests = 0;
  await assert.rejects(
    resetDraftSession({
      sport: 'f1',
      leagueId: '10547893',
      teamId: '6',
      sessionKey: 'f1:other',
      updatedAt: '2026-09-01T23:15:00.000Z',
    }, { fetchImpl: async () => { requests += 1; } }),
    /identity/i,
  );
  assert.equal(requests, 0);
});

test('reset refuses non-loopback or parameterized endpoints before sending identity', async () => {
  const session = {
    sport: 'f1',
    leagueId: '10547893',
    teamId: '6',
    sessionKey: 'f1:10547893',
    updatedAt: '2026-09-01T23:15:00.000Z',
  };
  for (const endpoint of [
    'https://example.test/draft-reset',
    'http://127.0.0.1:8765/draft-reset?auth=secret',
    'http://127.0.0.1:8765/other',
  ]) {
    let requests = 0;
    await assert.rejects(
      resetDraftSession(session, {
        endpoint,
        fetchImpl: async () => { requests += 1; },
      }),
      /loopback draft-reset route/i,
    );
    assert.equal(requests, 0);
  }
});

test('reset surfaces the exact bounded server rejection', async () => {
  await assert.rejects(
    resetDraftSession({
      sport: 'f1',
      leagueId: '10547893',
      teamId: '6',
      sessionKey: 'f1:10547893',
      updatedAt: '2026-09-01T23:15:00.000Z',
    }, {
      fetchImpl: async () => ({
        ok: false,
        status: 409,
        json: async () => ({ error: 'Draft changed; rescan before reset.' }),
      }),
    }),
    /Draft changed; rescan before reset\./,
  );
});

test('lock lease cancellation aborts an in-flight draft reset', async () => {
  const external = new AbortController();
  let requestSignal;
  const pending = resetDraftSession({
    sport: 'f1',
    leagueId: '10547893',
    teamId: '6',
    sessionKey: 'f1:10547893',
    updatedAt: '2026-09-01T23:15:00.000Z',
  }, {
    signal: external.signal,
    fetchImpl: async (_url, options) => {
      requestSignal = options.signal;
      await new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => {
        const error = new Error('aborted');
        error.name = 'AbortError';
        reject(error);
      }, { once: true }));
    },
  });

  await Promise.resolve();
  external.abort();
  await Promise.resolve();

  assert.equal(requestSignal.aborted, true);
  await assert.rejects(pending, /aborted/);
});

test('post-reset rescan waits until its timestamp is strictly after server reset time', async () => {
  const resetAtMs = Date.parse('2026-09-01T23:16:00.123Z');
  let now = resetAtMs;
  const result = await waitUntilAfterReset('2026-09-01T23:16:00.123456Z', {
    now: () => now,
    delay: async (milliseconds) => { now += milliseconds; },
  });

  assert.equal(result, true);
  assert.ok(now > resetAtMs);
});

test('post-reset rescan fails closed instead of waiting through excessive clock skew', async () => {
  const resetAtMs = Date.parse('2026-09-01T23:16:00.000Z');
  let delayCalls = 0;
  const result = await waitUntilAfterReset('2026-09-01T23:16:00.000Z', {
    now: () => resetAtMs - 10_000,
    delay: async () => { delayCalls += 1; },
    maximumWaitMs: 3000,
  });

  assert.equal(result, false);
  assert.equal(delayCalls, 0);
});
