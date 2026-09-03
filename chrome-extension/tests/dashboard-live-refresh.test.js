const test = require('node:test');
const assert = require('node:assert/strict');

const {
  createLiveDraftPoller,
  fetchDraftRevision,
} = require('../../src/dashboard/live-refresh.js');

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

function fakeClock() {
  let now = 0;
  let nextIdentifier = 1;
  const timers = new Map();
  function setTimeoutImpl(callback, delay) {
    const identifier = nextIdentifier;
    nextIdentifier += 1;
    timers.set(identifier, { callback, dueAt: now + delay });
    return identifier;
  }
  function clearTimeoutImpl(identifier) {
    timers.delete(identifier);
  }
  async function advance(milliseconds) {
    const target = now + milliseconds;
    while (true) {
      const due = [...timers.entries()]
        .filter(([, timer]) => timer.dueAt <= target)
        .sort((left, right) => left[1].dueAt - right[1].dueAt || left[0] - right[0])[0];
      if (!due) break;
      const [identifier, timer] = due;
      timers.delete(identifier);
      now = timer.dueAt;
      timer.callback();
      await new Promise((resolve) => setImmediate(resolve));
    }
    now = target;
    await new Promise((resolve) => setImmediate(resolve));
  }
  return { advance, clearTimeoutImpl, setTimeoutImpl };
}

function revision(generatedAt, leagueId = '10462193') {
  return {
    schemaVersion: 1,
    status: 'success',
    leagueId,
    sessionKey: `nfl:${leagueId}`,
    generatedAt,
    pickCount: 4,
    latestOverallPick: 4,
    captureBlocked: false,
  };
}

test('revision client posts only league identity and accepts one exact private response shape', async () => {
  let request;
  const result = await fetchDraftRevision('10462193', {
    fetchImpl: async (endpoint, options) => {
      request = { endpoint, options };
      return {
        ok: true,
        status: 200,
        async json() {
          return revision('2026-09-03T18:00:00Z');
        },
      };
    },
  });

  assert.deepEqual(result, revision('2026-09-03T18:00:00Z'));
  assert.equal(request.endpoint, '/draft-revision');
  assert.deepEqual(JSON.parse(request.options.body), {
    schemaVersion: 1,
    leagueId: '10462193',
  });
  assert.equal(request.options.credentials, 'omit');
  assert.equal(request.options.cache, 'no-store');
  assert.deepEqual(request.options.headers, {
    'Content-Type': 'application/json',
    'X-Fantasy-Draft-UI': '1',
  });
});

test('revision client rejects extra private state, mismatched identity, and invalid timestamps', async () => {
  const badResponses = [
    { ...revision('2026-09-03T18:00:00Z'), picks: [{ player: 'Private' }] },
    { ...revision('2026-09-03T18:00:00Z'), teamId: '6' },
    revision('2026-09-03T18:00:00Z', '999'),
    revision('not-a-date'),
    { schemaVersion: 1, status: 'success', leagueId: '10462193' },
  ];
  for (const responseBody of badResponses) {
    await assert.rejects(
      fetchDraftRevision('10462193', {
        fetchImpl: async () => ({
          ok: true,
          status: 200,
          async json() { return responseBody; },
        }),
      }),
      /invalid|match|shape|timestamp/i,
    );
  }
});

test('visible polling is single-flight and pauses while hidden', async () => {
  const clock = fakeClock();
  const first = deferred();
  let visible = true;
  let active = 0;
  let maximumActive = 0;
  let requests = 0;
  const poller = createLiveDraftPoller({
    pollIntervalMs: 500,
    quietDelayMs: 400,
    setTimeoutImpl: clock.setTimeoutImpl,
    clearTimeoutImpl: clock.clearTimeoutImpl,
    enabled: () => true,
    visible: () => visible,
    leagueId: () => '10462193',
    fetchRevision: async () => {
      requests += 1;
      active += 1;
      maximumActive = Math.max(maximumActive, active);
      if (requests === 1) await first.promise;
      active -= 1;
      return revision(`2026-09-03T18:00:0${requests}Z`);
    },
    refresh: async () => ({ applied: true }),
  });

  poller.start();
  await clock.advance(0);
  assert.equal(requests, 1);
  await clock.advance(5_000);
  assert.equal(requests, 1, 'recursive polling waits for the prior request');
  assert.equal(maximumActive, 1);

  first.resolve();
  await new Promise((resolve) => setImmediate(resolve));
  visible = false;
  poller.visibilityChanged();
  await clock.advance(5_000);
  assert.equal(requests, 1, 'hidden dashboards do not keep polling');

  visible = true;
  poller.visibilityChanged();
  await clock.advance(0);
  assert.equal(requests, 2);
  assert.equal(maximumActive, 1);
});

test('rapid revisions debounce to the latest value and never overlap recommendation refreshes', async () => {
  const clock = fakeClock();
  const refreshGate = deferred();
  let currentRevision = revision('2026-09-03T18:00:01Z');
  const refreshed = [];
  const applied = [];
  const pending = [];
  let refreshActive = 0;
  let maximumRefreshActive = 0;
  const poller = createLiveDraftPoller({
    pollIntervalMs: 100,
    quietDelayMs: 400,
    setTimeoutImpl: clock.setTimeoutImpl,
    clearTimeoutImpl: clock.clearTimeoutImpl,
    enabled: () => true,
    visible: () => true,
    leagueId: () => '10462193',
    fetchRevision: async () => currentRevision,
    refresh: async (value) => {
      refreshed.push(value.generatedAt);
      refreshActive += 1;
      maximumRefreshActive = Math.max(maximumRefreshActive, refreshActive);
      if (refreshed.length === 1) await refreshGate.promise;
      refreshActive -= 1;
      return { applied: true };
    },
    applied: (value) => applied.push(value.generatedAt),
    pending: (value, detail) => pending.push([value.generatedAt, detail.superseded]),
  });

  poller.start();
  await clock.advance(100);
  currentRevision = revision('2026-09-03T18:00:02Z');
  await clock.advance(100);
  currentRevision = revision('2026-09-03T18:00:03Z');
  await clock.advance(499);
  assert.deepEqual(refreshed, []);
  assert.deepEqual(pending, [
    ['2026-09-03T18:00:01Z', false],
    ['2026-09-03T18:00:02Z', true],
    ['2026-09-03T18:00:03Z', true],
  ]);
  await clock.advance(1);
  assert.deepEqual(refreshed, ['2026-09-03T18:00:03Z']);
  currentRevision = revision('2026-09-03T18:00:04Z');
  await clock.advance(500);
  assert.equal(refreshed.length, 1, 'new work waits for the active recommendation');
  refreshGate.resolve();
  await new Promise((resolve) => setImmediate(resolve));
  await clock.advance(400);
  assert.deepEqual(refreshed, [
    '2026-09-03T18:00:03Z',
    '2026-09-03T18:00:04Z',
  ]);
  assert.deepEqual(applied, ['2026-09-03T18:00:04Z'], 'the superseded result is never applied');
  assert.equal(maximumRefreshActive, 1);
});

test('repeated refresh-required results back off before becoming rendered', async () => {
  const clock = fakeClock();
  let refreshCount = 0;
  const applied = [];
  const poller = createLiveDraftPoller({
    pollIntervalMs: 500,
    quietDelayMs: 400,
    setTimeoutImpl: clock.setTimeoutImpl,
    clearTimeoutImpl: clock.clearTimeoutImpl,
    enabled: () => true,
    visible: () => true,
    leagueId: () => '10462193',
    fetchRevision: async () => revision('2026-09-03T18:00:01Z'),
    refresh: async () => {
      refreshCount += 1;
      return refreshCount <= 2 ? { retry: true } : { applied: true };
    },
    applied: (value) => applied.push(value.generatedAt),
  });

  poller.start();
  await clock.advance(400);
  assert.equal(refreshCount, 1);
  assert.deepEqual(applied, []);
  await clock.advance(400);
  assert.equal(refreshCount, 2);
  assert.deepEqual(applied, []);
  await clock.advance(799);
  assert.equal(refreshCount, 2, 'the second retry waits for exponential backoff');
  await clock.advance(1);
  assert.equal(refreshCount, 3);
  assert.deepEqual(applied, ['2026-09-03T18:00:01Z']);
});

test('manual retry can automatically recover the same revision after clearing its cards', async () => {
  const clock = fakeClock();
  const current = revision('2026-09-03T18:00:01Z');
  let refreshCount = 0;
  const poller = createLiveDraftPoller({
    pollIntervalMs: 500,
    quietDelayMs: 400,
    setTimeoutImpl: clock.setTimeoutImpl,
    clearTimeoutImpl: clock.clearTimeoutImpl,
    enabled: () => true,
    visible: () => true,
    leagueId: () => '10462193',
    fetchRevision: async () => current,
    refresh: async () => {
      refreshCount += 1;
      return refreshCount === 1 ? { retry: true } : { applied: true };
    },
  });

  poller.markRendered(current);
  poller.stop();
  poller.forgetRendered();
  poller.restart();
  await clock.advance(400);
  assert.equal(refreshCount, 1, 'the same revision is no longer suppressed after cards clear');
  await clock.advance(400);
  assert.equal(refreshCount, 2, 'the retry result automatically runs again');
});

test('manual transient error can automatically recover the same revision after clearing its cards', async () => {
  const clock = fakeClock();
  const current = revision('2026-09-03T18:00:01Z');
  let refreshCount = 0;
  const errors = [];
  const poller = createLiveDraftPoller({
    pollIntervalMs: 500,
    quietDelayMs: 400,
    setTimeoutImpl: clock.setTimeoutImpl,
    clearTimeoutImpl: clock.clearTimeoutImpl,
    enabled: () => true,
    visible: () => true,
    leagueId: () => '10462193',
    fetchRevision: async () => current,
    refresh: async () => {
      refreshCount += 1;
      if (refreshCount === 1) throw new Error('temporary recommendation failure');
      return { applied: true };
    },
    onError: (error) => errors.push(error.message),
  });

  poller.markRendered(current);
  poller.stop();
  poller.forgetRendered();
  poller.restart();
  await clock.advance(400);
  assert.equal(refreshCount, 1);
  assert.deepEqual(errors, ['temporary recommendation failure']);
  await clock.advance(400);
  assert.equal(refreshCount, 2, 'the same revision recovers without waiting for another pick');
});

test('terminal recommendation errors suppress only the same revision', async () => {
  const clock = fakeClock();
  let current = revision('2026-09-03T18:00:01Z');
  let refreshCount = 0;
  const terminal = [];
  const retryErrors = [];
  const poller = createLiveDraftPoller({
    pollIntervalMs: 100,
    quietDelayMs: 400,
    setTimeoutImpl: clock.setTimeoutImpl,
    clearTimeoutImpl: clock.clearTimeoutImpl,
    enabled: () => true,
    visible: () => true,
    leagueId: () => '10462193',
    fetchRevision: async () => current,
    refresh: async () => {
      refreshCount += 1;
      if (refreshCount === 1) {
        const error = new Error('profile action required');
        error.status = 422;
        error.retryable = false;
        throw error;
      }
      return { applied: true };
    },
    onError: (error) => retryErrors.push(error.message),
    terminal: (error, value) => terminal.push([error.message, value.generatedAt]),
  });

  poller.start();
  await clock.advance(400);
  assert.equal(refreshCount, 1);
  assert.deepEqual(terminal, [[
    'profile action required',
    '2026-09-03T18:00:01Z',
  ]]);
  assert.deepEqual(retryErrors, []);
  poller.restart();
  await clock.advance(2_000);
  assert.equal(refreshCount, 1, 'restart and polling do not hammer a terminal same-revision error');

  current = revision('2026-09-03T18:00:02Z');
  await clock.advance(500);
  assert.equal(refreshCount, 2, 'a genuinely new draft revision is eligible again');
});

test('changing a selection while live refresh is off invalidates the handled revision', async () => {
  const clock = fakeClock();
  const current = revision('2026-09-03T18:00:01Z');
  let enabled = false;
  let refreshCount = 0;
  const poller = createLiveDraftPoller({
    pollIntervalMs: 500,
    quietDelayMs: 400,
    setTimeoutImpl: clock.setTimeoutImpl,
    clearTimeoutImpl: clock.clearTimeoutImpl,
    enabled: () => enabled,
    visible: () => true,
    leagueId: () => '10462193',
    fetchRevision: async () => current,
    refresh: async () => {
      refreshCount += 1;
      return { applied: true };
    },
  });

  poller.markRendered(current);
  poller.stop();
  poller.invalidate();
  await clock.advance(2_000);
  assert.equal(refreshCount, 0, 'invalidating while disabled starts no background work');

  enabled = true;
  poller.restart();
  await clock.advance(400);
  assert.equal(refreshCount, 1, 're-enabling recomputes even when the draft timestamp is unchanged');
});

test('a new league revision waits for an older refresh and then runs without stalling', async () => {
  const clock = fakeClock();
  const oldRefresh = deferred();
  let leagueId = '10462193';
  const refreshed = [];
  const poller = createLiveDraftPoller({
    pollIntervalMs: 500,
    quietDelayMs: 400,
    setTimeoutImpl: clock.setTimeoutImpl,
    clearTimeoutImpl: clock.clearTimeoutImpl,
    enabled: () => true,
    visible: () => true,
    leagueId: () => leagueId,
    fetchRevision: async (requestedLeague) => revision(
      requestedLeague === '10462193'
        ? '2026-09-03T18:00:01Z'
        : '2026-09-03T18:00:02Z',
      requestedLeague,
    ),
    refresh: async (value) => {
      refreshed.push(value.leagueId);
      if (value.leagueId === '10462193') await oldRefresh.promise;
      return { applied: true };
    },
  });

  poller.start();
  await clock.advance(400);
  assert.deepEqual(refreshed, ['10462193']);
  leagueId = '999';
  poller.restart();
  await clock.advance(0);
  await clock.advance(400);
  assert.deepEqual(refreshed, ['10462193'], 'new-league work never overlaps the old refresh');

  oldRefresh.resolve();
  await new Promise((resolve) => setImmediate(resolve));
  await clock.advance(400);
  assert.deepEqual(refreshed, ['10462193', '999']);
});

test('poll failures use bounded backoff and restarting cancels stale league work', async () => {
  const clock = fakeClock();
  let leagueId = '10462193';
  let requests = 0;
  const errors = [];
  const poller = createLiveDraftPoller({
    pollIntervalMs: 250,
    maximumBackoffMs: 1000,
    setTimeoutImpl: clock.setTimeoutImpl,
    clearTimeoutImpl: clock.clearTimeoutImpl,
    enabled: () => true,
    visible: () => true,
    leagueId: () => leagueId,
    fetchRevision: async () => {
      requests += 1;
      throw new Error('offline');
    },
    refresh: async () => ({ applied: true }),
    onError: (error) => errors.push(error.message),
  });

  poller.start();
  await clock.advance(0);
  await clock.advance(250);
  await clock.advance(500);
  await clock.advance(1000);
  const beforeRestart = requests;
  assert.ok(beforeRestart >= 4);
  assert.ok(errors.every((message) => message === 'offline'));
  leagueId = '999';
  poller.restart();
  await clock.advance(0);
  assert.equal(requests, beforeRestart + 1);
});
