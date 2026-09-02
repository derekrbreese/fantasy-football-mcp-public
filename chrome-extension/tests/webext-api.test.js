const test = require('node:test');
const assert = require('node:assert/strict');

const { createWebExtensionApi } = require('../webext-api.js');

test('uses Firefox promise-based browser APIs', async () => {
  const browser = {
    runtime: { onMessage: {} },
    storage: {
      local: {
        async get(key) { return { [key]: { saved: true } }; },
        async set(value) { return value; },
        async remove(key) { return key; },
      },
      onChanged: {},
    },
    tabs: {
      async query(query) { return [{ id: 9, query }]; },
      async sendMessage(tabId, message) { return { tabId, message }; },
    },
    notifications: {
      async create(id, options) { return { id, options }; },
    },
  };

  const api = createWebExtensionApi({ browser });
  assert.deepEqual(await api.storageGet('drafts'), { drafts: { saved: true } });
  assert.deepEqual(await api.queryTabs({ active: true }), [{ id: 9, query: { active: true } }]);
  assert.deepEqual(await api.sendTabMessage(9, { type: 'STATUS' }), {
    tabId: 9,
    message: { type: 'STATUS' },
  });
  assert.equal(await api.storageRemove('drafts'), 'drafts');
  assert.deepEqual(await api.createNotification('turn', { title: 'Next' }), {
    id: 'turn', options: { title: 'Next' },
  });
  assert.equal(api.native, browser);
});

test('uses Chromium callback-based chrome APIs', async () => {
  const chrome = {
    runtime: { lastError: null, onMessage: {} },
    storage: {
      local: {
        get(key, callback) { callback({ [key]: [1, 2] }); },
        set(_value, callback) { callback(); },
        remove(_key, callback) { callback(); },
      },
      onChanged: {},
    },
    tabs: {
      query(_query, callback) { callback([{ id: 3 }]); },
      sendMessage(_tabId, _message, callback) { callback({ ok: true }); },
    },
    notifications: {
      create(id, options, callback) { callback(`${id}:${options.title}`); },
    },
  };

  const api = createWebExtensionApi({ chrome });
  assert.deepEqual(await api.storageGet('drafts'), { drafts: [1, 2] });
  assert.deepEqual(await api.queryTabs({ active: true }), [{ id: 3 }]);
  assert.deepEqual(await api.sendTabMessage(3, { type: 'STATUS' }), { ok: true });
  await api.storageSet({ drafts: [] });
  await api.storageRemove('drafts');
  assert.equal(await api.createNotification('turn', { title: 'Clock' }), 'turn:Clock');
  assert.equal(api.native, chrome);
});

test('rejects notification requests when the API is unavailable', async () => {
  const browser = {
    runtime: {},
    storage: { local: {}, onChanged: {} },
    tabs: {},
  };
  const api = createWebExtensionApi({ browser });
  await assert.rejects(
    api.createNotification('turn', { title: 'Clock' }),
    /notifications are unavailable/,
  );
});

test('rejects callback calls when Chromium reports runtime.lastError', async () => {
  const chrome = {
    runtime: { lastError: null },
    storage: { local: {}, onChanged: {} },
    tabs: {
      sendMessage(_tabId, _message, callback) {
        chrome.runtime.lastError = { message: 'Receiving end does not exist' };
        callback();
        chrome.runtime.lastError = null;
      },
    },
  };

  const api = createWebExtensionApi({ chrome });
  await assert.rejects(
    api.sendTabMessage(3, { type: 'STATUS' }),
    /Receiving end does not exist/,
  );
});
