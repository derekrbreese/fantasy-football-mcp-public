const test = require('node:test');
const assert = require('node:assert/strict');

const { createBoundedScanScheduler } = require('../scan-scheduler.js');

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

function flushTasks() {
  return new Promise((resolve) => setImmediate(resolve));
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
      await Promise.resolve();
    }
    now = target;
    await Promise.resolve();
  }

  return { advance, clearTimeoutImpl, setTimeoutImpl };
}

test('continuous mutation churn cannot postpone a scan beyond the maximum wait', async () => {
  const clock = fakeClock();
  let scans = 0;
  const scheduler = createBoundedScanScheduler({
    quietDelayMs: 400,
    maximumWaitMs: 1000,
    setTimeoutImpl: clock.setTimeoutImpl,
    clearTimeoutImpl: clock.clearTimeoutImpl,
    run: async () => { scans += 1; },
  });

  scheduler.request();
  await clock.advance(300);
  scheduler.request();
  await clock.advance(300);
  scheduler.request();
  await clock.advance(300);
  scheduler.request();
  assert.equal(scans, 0);

  await clock.advance(100);
  assert.equal(scans, 1, 'the original one-second deadline is not reset by later churn');
});

test('a burst of idle mutations coalesces behind the quiet debounce', async () => {
  const clock = fakeClock();
  let scans = 0;
  const scheduler = createBoundedScanScheduler({
    quietDelayMs: 400,
    maximumWaitMs: 1000,
    setTimeoutImpl: clock.setTimeoutImpl,
    clearTimeoutImpl: clock.clearTimeoutImpl,
    run: async () => { scans += 1; },
  });

  scheduler.request();
  scheduler.request();
  scheduler.request();
  await clock.advance(399);
  assert.equal(scans, 0);
  await clock.advance(1);
  assert.equal(scans, 1);
  await clock.advance(1000);
  assert.equal(scans, 1, 'the cleared maximum timer cannot produce a duplicate scan');
});

test('mutations during one active scan produce exactly one non-overlapping replay', async () => {
  const clock = fakeClock();
  const first = deferred();
  const second = deferred();
  let active = 0;
  let maximumActive = 0;
  let scans = 0;
  const scheduler = createBoundedScanScheduler({
    quietDelayMs: 400,
    maximumWaitMs: 1000,
    setTimeoutImpl: clock.setTimeoutImpl,
    clearTimeoutImpl: clock.clearTimeoutImpl,
    run: async () => {
      scans += 1;
      active += 1;
      maximumActive = Math.max(maximumActive, active);
      await (scans === 1 ? first.promise : second.promise);
      active -= 1;
    },
  });

  scheduler.request();
  await clock.advance(400);
  assert.equal(scans, 1);
  scheduler.request();
  scheduler.request();
  scheduler.request();
  await clock.advance(2000);
  assert.equal(scans, 1, 'an active scan is never overlapped by a timer');

  first.resolve();
  await flushTasks();
  assert.equal(scans, 2, 'all dirty notifications replay once after the active scan');
  assert.equal(maximumActive, 1);

  second.resolve();
  await flushTasks();
  await clock.advance(2000);
  assert.equal(scans, 2, 'no third scan appears without a new mutation');
});

test('runNow starts the initial scan without waiting for a timer', async () => {
  const clock = fakeClock();
  let scans = 0;
  const scheduler = createBoundedScanScheduler({
    setTimeoutImpl: clock.setTimeoutImpl,
    clearTimeoutImpl: clock.clearTimeoutImpl,
    run: async () => { scans += 1; },
  });

  await scheduler.runNow();
  assert.equal(scans, 1);
  await clock.advance(2000);
  assert.equal(scans, 1);
});
