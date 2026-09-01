const test = require('node:test');
const assert = require('node:assert/strict');

const {
  LOCK_PORT_NAME,
  createLockBroker,
  createSessionLeaseStore,
} = require('../lock-broker.js');
const {
  createDraftStorage,
  createSessionOperationLock,
} = require('../draft-storage.js');

function eventChannel() {
  const listeners = new Set();
  return {
    addListener(listener) { listeners.add(listener); },
    removeListener(listener) { listeners.delete(listener); },
    emit(value) {
      for (const listener of [...listeners]) listener(value);
    },
  };
}

function connectedPortPair(name = LOCK_PORT_NAME) {
  const clientMessages = eventChannel();
  const backgroundMessages = eventChannel();
  const clientDisconnect = eventChannel();
  const backgroundDisconnect = eventChannel();
  let disconnected = false;

  function disconnect() {
    if (disconnected) return;
    disconnected = true;
    clientDisconnect.emit();
    backgroundDisconnect.emit();
  }

  return {
    client: {
      name,
      onMessage: clientMessages,
      onDisconnect: clientDisconnect,
      postMessage(message) {
        if (disconnected) throw new Error('Port is disconnected');
        backgroundMessages.emit(message);
      },
      disconnect,
    },
    background: {
      name,
      onMessage: backgroundMessages,
      onDisconnect: backgroundDisconnect,
      postMessage(message) {
        if (disconnected) throw new Error('Port is disconnected');
        clientMessages.emit(message);
      },
      disconnect,
    },
  };
}

function brokerRuntime() {
  const broker = createLockBroker();
  return {
    connect(details) {
      const pair = connectedPortPair(details?.name);
      broker.attachPort(pair.background);
      return pair.client;
    },
  };
}

function flushBroker() {
  return new Promise((resolve) => setImmediate(resolve));
}

function memoryLeaseStore() {
  const leases = new Map();
  return {
    leases,
    async read(lockName) { return leases.get(lockName) || null; },
    async write(lockName, lease) { leases.set(lockName, lease); },
    async clear(lockName, nonce) {
      if (leases.get(lockName)?.nonce === nonce) leases.delete(lockName);
    },
  };
}

test('background broker serializes the same Yahoo session while other leagues proceed', async () => {
  const runtime = brokerRuntime();
  const firstTab = createSessionOperationLock(runtime);
  const secondTab = createSessionOperationLock(runtime);
  const operations = [];
  let releaseFirst;
  const firstGate = new Promise((resolve) => { releaseFirst = resolve; });

  const scan = firstTab.run('f1:10547893', async () => {
    operations.push('scan-start');
    await firstGate;
    operations.push('scan-end');
  });
  const repair = secondTab.run('f1:10547893', async () => operations.push('repair'));
  const otherLeague = secondTab.run('f1:10547894', async () => operations.push('other-league'));

  await otherLeague;
  assert.deepEqual(operations, ['scan-start', 'other-league']);
  releaseFirst();
  await Promise.all([scan, repair]);
  assert.deepEqual(operations, ['scan-start', 'other-league', 'scan-end', 'repair']);
});

test('background broker retains a disconnected holder fence until expiry', async () => {
  let now = 1000;
  let nonce = 0;
  const broker = createLockBroker({
    leaseStore: memoryLeaseStore(),
    now: () => now,
    makeNonce: () => `disconnect-${++nonce}`,
    leaseMs: 2000,
  });
  const first = connectedPortPair();
  const second = connectedPortPair();
  const firstReplies = [];
  const secondReplies = [];
  first.client.onMessage.addListener((message) => firstReplies.push(message));
  second.client.onMessage.addListener((message) => secondReplies.push(message));
  broker.attachPort(first.background);
  broker.attachPort(second.background);

  const acquire = {
    schemaVersion: 1,
    type: 'acquire',
    scope: 'session',
    sessionKey: 'f1:10547893',
  };
  first.client.postMessage(acquire);
  second.client.postMessage(acquire);
  await flushBroker();
  assert.equal(firstReplies.at(-1)?.type, 'granted');
  assert.equal(secondReplies.length, 0);

  first.client.disconnect();
  await flushBroker();
  assert.equal(secondReplies.length, 0);

  now += 2001;
  second.client.postMessage({ schemaVersion: 1, type: 'keepalive' });
  await flushBroker();
  assert.equal(secondReplies.at(-1)?.type, 'granted');
});

test('background broker rejects fields outside the small lock allowlist', async () => {
  const broker = createLockBroker();
  const pair = connectedPortPair();
  const replies = [];
  pair.client.onMessage.addListener((message) => replies.push(message));
  broker.attachPort(pair.background);

  pair.client.postMessage({
    schemaVersion: 1,
    type: 'acquire',
    scope: 'session',
    sessionKey: 'f1:10547893',
    url: 'https://example.invalid/?auth=secret',
  });

  await flushBroker();
  assert.equal(replies.at(-1)?.type, 'rejected');
});

test('a rejected operation releases the next waiter for that session', async () => {
  const runtime = brokerRuntime();
  const firstTab = createSessionOperationLock(runtime);
  const secondTab = createSessionOperationLock(runtime);
  const operations = [];

  const rejected = firstTab.run('f1:10547893', async () => {
    operations.push('first');
    throw new Error('repair failed safely');
  });
  const next = secondTab.run('f1:10547893', async () => {
    operations.push('second');
    return 'continued';
  });

  await assert.rejects(rejected, /repair failed safely/);
  assert.equal(await next, 'continued');
  assert.deepEqual(operations, ['first', 'second']);
});

test('a broker disconnect rejects without running the protected operation', async () => {
  let pair;
  const runtime = {
    connect(details) {
      pair = connectedPortPair(details?.name);
      return pair.client;
    },
  };
  const lock = createSessionOperationLock(runtime);
  let operationRuns = 0;
  const pending = lock.run('f1:10547893', async () => { operationRuns += 1; });

  pair.background.disconnect();

  await assert.rejects(pending, /lock broker disconnected/);
  assert.equal(operationRuns, 0);
});

test('invalid session keys are rejected before contacting the broker', async () => {
  let connectCalls = 0;
  const lock = createSessionOperationLock({
    connect() {
      connectCalls += 1;
      throw new Error('must not connect');
    },
  });

  await assert.rejects(
    lock.run('f1:10547893?auth=secret', async () => undefined),
    /valid Yahoo sessionKey/,
  );
  assert.equal(connectCalls, 0);
});

test('client ignores broker rejection replies with extra fields', async () => {
  let pair;
  const lock = createSessionOperationLock({
    connect(details) {
      pair = connectedPortPair(details?.name);
      return pair.client;
    },
  }, { acquireTimeoutMs: 100 });
  let operationRuns = 0;
  const pending = lock.run('f1:10547893', async () => { operationRuns += 1; });

  pair.background.postMessage({
    schemaVersion: 1,
    type: 'rejected',
    url: 'https://example.invalid/?auth=secret',
  });
  await Promise.resolve();
  assert.equal(operationRuns, 0);
  pair.background.postMessage({ schemaVersion: 1, type: 'rejected' });

  await assert.rejects(pending, /broker rejected/);
  assert.equal(operationRuns, 0);
});

test('draft storage construction fails closed without a broker or explicit lock', () => {
  assert.throws(
    () => createDraftStorage({ storageGet() {}, storageSet() {}, storageRemove() {} }),
    /lock broker is unavailable/,
  );
});

test('queued and held clients send bounded keepalives through the private port', async () => {
  const broker = createLockBroker();
  const messageTypes = [];
  const runtime = {
    connect(details) {
      const pair = connectedPortPair(details?.name);
      pair.background.onMessage.addListener((message) => messageTypes.push(message.type));
      broker.attachPort(pair.background);
      return pair.client;
    },
  };
  const lock = createSessionOperationLock(runtime, {
    heartbeatMs: 5,
    acquireTimeoutMs: 100,
    holdTimeoutMs: 100,
  });
  let release;
  const gate = new Promise((resolve) => { release = resolve; });

  const operation = lock.run('f1:10547893', async () => gate);
  await new Promise((resolve) => setTimeout(resolve, 16));
  release();
  await operation;

  assert.ok(messageTypes.filter((type) => type === 'keepalive').length >= 2);
  assert.equal(messageTypes.at(-1), 'release');
});

test('post-grant disconnect aborts the lease and waits for callback unwind', async () => {
  const broker = createLockBroker();
  let pair;
  const runtime = {
    connect(details) {
      pair = connectedPortPair(details?.name);
      broker.attachPort(pair.background);
      return pair.client;
    },
  };
  const lock = createSessionOperationLock(runtime, {
    heartbeatMs: 5,
    acquireTimeoutMs: 100,
    holdTimeoutMs: 100,
  });
  let started;
  const didStart = new Promise((resolve) => { started = resolve; });
  let unwound = false;

  const operation = lock.run('f1:10547893', async (lease) => {
    started();
    await new Promise((resolve) => lease.signal.addEventListener('abort', resolve, { once: true }));
    unwound = true;
    lease.throwIfLost();
  });
  await didStart;
  pair.background.disconnect();

  await assert.rejects(operation, /lock lease was lost/i);
  assert.equal(unwound, true);
});

test('unexpected disconnect fences the next callback until abort unwind and expiry', async () => {
  let now = 1000;
  let nonce = 0;
  const broker = createLockBroker({
    leaseStore: memoryLeaseStore(),
    now: () => now,
    makeNonce: () => `callback-${++nonce}`,
    leaseMs: 2000,
  });
  const pairs = [];
  const runtime = {
    connect(details) {
      const pair = connectedPortPair(details?.name);
      pairs.push(pair);
      broker.attachPort(pair.background);
      return pair.client;
    },
  };
  const lock = createSessionOperationLock(runtime, {
    heartbeatMs: 100,
    acquireTimeoutMs: 5000,
    holdTimeoutMs: 5000,
  });
  let firstStarted;
  const didStart = new Promise((resolve) => { firstStarted = resolve; });
  let finishUnwind;
  const unwindGate = new Promise((resolve) => { finishUnwind = resolve; });
  let secondRuns = 0;

  const first = lock.run('f1:10547893', async (lease) => {
    firstStarted();
    await new Promise((resolve) => lease.signal.addEventListener('abort', resolve, { once: true }));
    await unwindGate;
    lease.throwIfLost();
  });
  await didStart;
  const second = lock.run('f1:10547893', async () => { secondRuns += 1; });

  pairs[0].background.disconnect();
  await flushBroker();
  assert.equal(secondRuns, 0);

  finishUnwind();
  await assert.rejects(first, /lock lease was lost/i);
  assert.equal(secondRuns, 0);

  now += 2001;
  pairs[1].client.postMessage({ schemaVersion: 1, type: 'keepalive' });
  await second;
  assert.equal(secondRuns, 1);
});

test('broker acquire and hold times are bounded and fail closed', async () => {
  let waitingPair;
  const waitingLock = createSessionOperationLock({
    connect(details) {
      waitingPair = connectedPortPair(details?.name);
      return waitingPair.client;
    },
  }, { acquireTimeoutMs: 5, heartbeatMs: 5 });
  let waitingRuns = 0;
  await assert.rejects(
    waitingLock.run('f1:10547893', async () => { waitingRuns += 1; }),
    /Timed out waiting/,
  );
  assert.equal(waitingRuns, 0);

  const runtime = brokerRuntime();
  const heldLock = createSessionOperationLock(runtime, {
    holdTimeoutMs: 5,
    heartbeatMs: 5,
  });
  let unwound = false;
  await assert.rejects(
    heldLock.run('f1:10547893', async (lease) => {
      await new Promise((resolve) => lease.signal.addEventListener('abort', resolve, { once: true }));
      unwound = true;
      lease.throwIfLost();
    }),
    /lock lease timed out/i,
  );
  assert.equal(unwound, true);
});

test('broker restart honors the unexpired storage-session fence', async () => {
  let now = 1_000;
  let nonce = 0;
  const leaseStore = memoryLeaseStore();
  const firstBroker = createLockBroker({
    leaseStore,
    now: () => now,
    makeNonce: () => `nonce-${++nonce}`,
    leaseMs: 4000,
  });
  const first = connectedPortPair();
  const firstReplies = [];
  first.client.onMessage.addListener((message) => firstReplies.push(message));
  firstBroker.attachPort(first.background);
  first.client.postMessage({
    schemaVersion: 1, type: 'acquire', scope: 'session', sessionKey: 'f1:10547893',
  });
  await flushBroker();
  assert.equal(firstReplies.at(-1)?.type, 'granted');

  const restartedBroker = createLockBroker({
    leaseStore,
    now: () => now,
    makeNonce: () => `nonce-${++nonce}`,
    leaseMs: 4000,
  });
  const second = connectedPortPair();
  const secondReplies = [];
  second.client.onMessage.addListener((message) => secondReplies.push(message));
  restartedBroker.attachPort(second.background);
  second.client.postMessage({
    schemaVersion: 1, type: 'acquire', scope: 'session', sessionKey: 'f1:10547893',
  });
  await flushBroker();
  assert.equal(secondReplies.length, 0);

  now += 4001;
  second.client.postMessage({ schemaVersion: 1, type: 'keepalive' });
  await flushBroker();
  assert.equal(secondReplies.at(-1)?.type, 'granted');
});

test('storage-session fence contains only encoded lock identity, nonce, and expiry', async () => {
  const data = {};
  const storageArea = {
    async get(key) { return { [key]: data[key] }; },
    async set(values) { Object.assign(data, values); },
    async remove(key) { delete data[key]; },
  };
  const store = createSessionLeaseStore(storageArea, { promiseNative: true });
  await store.write('session:f1:10547893', { nonce: 'opaque-nonce', expiresAt: 5000 });

  assert.deepEqual(data, {
    'yahooDraftRecorderLockLease:session%3Af1%3A10547893': {
      nonce: 'opaque-nonce',
      expiresAt: 5000,
    },
  });
  assert.deepEqual(await store.read('session:f1:10547893'), {
    nonce: 'opaque-nonce',
    expiresAt: 5000,
  });
  await store.clear('session:f1:10547893', 'different-nonce');
  assert.equal(Object.keys(data).length, 1);
  await store.clear('session:f1:10547893', 'opaque-nonce');
  assert.deepEqual(data, {});
});

test('Chrome callback storage-session fence uses nonce-checked cleanup', async () => {
  const data = {};
  const storageArea = {
    get(key, callback) { callback({ [key]: data[key] }); },
    set(values, callback) { Object.assign(data, values); callback(); },
    remove(key, callback) { delete data[key]; callback(); },
  };
  const store = createSessionLeaseStore(storageArea, {
    promiseNative: false,
    runtime: { lastError: null },
  });
  await store.write('session:f1:10547893', { nonce: 'chrome-nonce', expiresAt: 7000 });

  assert.deepEqual(await store.read('session:f1:10547893'), {
    nonce: 'chrome-nonce',
    expiresAt: 7000,
  });
  await store.clear('session:f1:10547893', 'wrong-nonce');
  assert.equal(Object.keys(data).length, 1);
  await store.clear('session:f1:10547893', 'chrome-nonce');
  assert.deepEqual(data, {});
});
