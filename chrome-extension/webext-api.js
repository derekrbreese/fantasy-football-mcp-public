(function initWebExtensionApi(globalScope) {
  'use strict';

  function createWebExtensionApi(scope) {
    const promiseNative = scope?.browser;
    const native = promiseNative || scope?.chrome;
    if (!native) throw new Error('WebExtension APIs are unavailable');

    function callPromise(method, context, args) {
      try {
        return Promise.resolve(method.apply(context, args));
      } catch (error) {
        return Promise.reject(error);
      }
    }

    function callCallback(method, context, args) {
      return new Promise((resolve, reject) => {
        method.apply(context, [
          ...args,
          (result) => {
            const lastError = native.runtime?.lastError;
            if (lastError) reject(new Error(lastError.message || String(lastError)));
            else resolve(result);
          },
        ]);
      });
    }

    const call = promiseNative ? callPromise : callCallback;

    return {
      native,
      storageGet(key) {
        return call(native.storage.local.get, native.storage.local, [key]);
      },
      storageSet(value) {
        return call(native.storage.local.set, native.storage.local, [value]);
      },
      storageRemove(key) {
        return call(native.storage.local.remove, native.storage.local, [key]);
      },
      queryTabs(query) {
        return call(native.tabs.query, native.tabs, [query]);
      },
      sendTabMessage(tabId, message) {
        return call(native.tabs.sendMessage, native.tabs, [tabId, message]);
      },
      createNotification(notificationId, options) {
        if (!native.notifications?.create) {
          return Promise.reject(new Error('WebExtension notifications are unavailable'));
        }
        return call(native.notifications.create, native.notifications, [notificationId, options]);
      },
    };
  }

  const api = { createWebExtensionApi };
  globalScope.YahooDraftWebExtension = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
