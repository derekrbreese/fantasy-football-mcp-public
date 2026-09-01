const test = require('node:test');
const assert = require('node:assert/strict');

const {
  DEFAULT_RECOMMENDATION_ENDPOINT,
  buildRecommendationRequestForLeagueId,
  buildRecommendationRequest,
  fetchDraftRecommendationsForLeagueId,
  fetchDraftRecommendations,
} = require('../recommendation-client.js');

const session = {
  sport: 'f1',
  leagueId: '10462193',
  teamId: '6',
  sessionKey: 'f1:10462193',
  picks: [{ pickNumber: 1, player: 'Private draft data' }],
};

test('builds a small allowlisted request from an explicit active league', () => {
  const request = buildRecommendationRequest(session, {
    strategy: 'aggressive',
    count: 999,
    rankingCount: 2,
    simulations: 10000,
    pageUrl: 'https://football.fantasysports.yahoo.com/?auth=secret',
    cookie: 'secret',
  });

  assert.deepEqual(request, {
    schemaVersion: 1,
    leagueId: '10462193',
    strategy: 'aggressive',
    count: 20,
    rankingCount: 25,
    simulations: 512,
  });
  assert.equal(JSON.stringify(request).includes('Private draft data'), false);
  assert.equal(JSON.stringify(request).includes('secret'), false);
});

test('builds the same safe request from a dashboard-selected league ID', () => {
  assert.deepEqual(buildRecommendationRequestForLeagueId('10462193', {
    strategy: 'conservative',
    count: 8,
  }), {
    schemaVersion: 1,
    leagueId: '10462193',
    strategy: 'conservative',
    count: 8,
    rankingCount: 250,
    simulations: 256,
  });
  assert.throws(
    () => buildRecommendationRequestForLeagueId('../10462193'),
    /valid Yahoo league ID/,
  );
});

test('missing and boolean numeric settings use safe defaults instead of coercing to zero', () => {
  assert.deepEqual(buildRecommendationRequestForLeagueId('10462193', {
    count: null,
    rankingCount: '',
    simulations: false,
  }), {
    schemaVersion: 1,
    leagueId: '10462193',
    strategy: 'balanced',
    count: 5,
    rankingCount: 250,
    simulations: 256,
  });

  assert.equal(buildRecommendationRequestForLeagueId('10462193', { count: '8' }).count, 8);
});

test('links an external cancellation signal to the bounded loopback request', async () => {
  const controller = new AbortController();
  let requestSignal;
  const pending = fetchDraftRecommendationsForLeagueId('10462193', {
    signal: controller.signal,
    fetchImpl: async (_url, options) => {
      requestSignal = options.signal;
      return await new Promise((_resolve, reject) => {
        options.signal.addEventListener('abort', () => {
          const error = new Error('cancelled');
          error.name = 'AbortError';
          reject(error);
        });
      });
    },
  });

  controller.abort();
  await assert.rejects(pending, (error) => error.name === 'AbortError');
  assert.equal(requestSignal.aborted, true);
});

test('rejects absent, malformed, or mismatched league identity', () => {
  assert.throws(() => buildRecommendationRequest(null), /Choose a Yahoo league/);
  assert.throws(
    () => buildRecommendationRequest({ sport: 'f1', leagueId: '123', sessionKey: 'f1:456' }),
    /identity does not match/,
  );
  assert.throws(
    () => buildRecommendationRequest({ sport: 'f1', leagueId: '../123', sessionKey: 'f1:../123' }),
    /valid Yahoo league/,
  );
});

test('posts the allowlisted request to the loopback recommendation endpoint', async () => {
  let request;
  const response = await fetchDraftRecommendations(session, {
    fetchImpl: async (url, options) => {
      request = { url, options };
      return {
        ok: true,
        json: async () => ({ status: 'success', leagueId: '10462193', recommendations: [] }),
      };
    },
    timeoutMs: 25,
  });

  assert.equal(request.url, DEFAULT_RECOMMENDATION_ENDPOINT);
  assert.equal(request.options.method, 'POST');
  assert.equal(request.options.cache, 'no-store');
  assert.equal(request.options.credentials, 'omit');
  assert.equal(request.options.headers['Content-Type'], 'application/json');
  assert.equal(request.options.headers['X-Fantasy-Draft-UI'], '1');
  assert.deepEqual(JSON.parse(request.options.body), {
    schemaVersion: 1,
    leagueId: '10462193',
    strategy: 'balanced',
    count: 5,
    rankingCount: 250,
    simulations: 256,
  });
  assert.deepEqual(response, { status: 'success', leagueId: '10462193', recommendations: [] });
});

test('rejects non-loopback endpoints and reports bounded server errors', async () => {
  await assert.rejects(
    fetchDraftRecommendations(session, {
      endpoint: 'https://example.com/draft-recommendation',
      fetchImpl: async () => ({ ok: true, json: async () => ({}) }),
    }),
    /loopback/,
  );

  await assert.rejects(
    fetchDraftRecommendations(session, {
      fetchImpl: async () => ({
        ok: false,
        status: 422,
        json: async () => ({ message: '<b>invalid league</b>' }),
      }),
    }),
    /HTTP 422: <b>invalid league<\/b>/,
  );
});

test('supports the same-origin relative endpoint used by the local dashboard', async () => {
  let url;
  await fetchDraftRecommendations(session, {
    endpoint: '/draft-recommendation',
    fetchImpl: async (value) => {
      url = value;
      return {
        ok: true,
        json: async () => ({ status: 'success', leagueId: '10462193', recommendations: [] }),
      };
    },
  });
  assert.equal(url, '/draft-recommendation');
});

test('dashboard client uses an explicit league ID without fabricating recorder identity', async () => {
  let body;
  const result = await fetchDraftRecommendationsForLeagueId('10462193', {
    endpoint: '/draft-recommendation',
    count: 20,
    fetchImpl: async (_url, options) => {
      body = JSON.parse(options.body);
      return {
        ok: true,
        json: async () => ({ status: 'success', leagueId: '10462193', recommendations: [] }),
      };
    },
  });

  assert.equal(body.leagueId, '10462193');
  assert.equal(body.count, 20);
  assert.equal(Object.hasOwn(body, 'sport'), false);
  assert.equal(Object.hasOwn(body, 'sessionKey'), false);
  assert.equal(result.leagueId, '10462193');
});

test('rejects a cross-league response even when the server returns HTTP 200', async () => {
  await assert.rejects(
    fetchDraftRecommendations(session, {
      fetchImpl: async () => ({
        ok: true,
        json: async () => ({ status: 'success', leagueId: 'different', recommendations: [] }),
      }),
    }),
    /did not match the selected Yahoo league/,
  );
});
