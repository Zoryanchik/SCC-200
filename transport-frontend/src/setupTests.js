import '@testing-library/jest-dom'

// Global test setup only; API modules are mocked explicitly in each test file.
// Provide a simple in-memory localStorage shim for the Node test environment
if (typeof globalThis.localStorage === 'undefined') {
// Provide a light-weight shim for the liveUpdatesManager in tests so that
// components/hook code can call connect/subscribe/disconnect synchronously
// without attempting real network connections. Tests may still spyOn these
// methods to assert behavior.
try {
	// Import the manager dynamically so this file still runs in non-test envs
	// without bundling the STOMP client.
	// eslint-disable-next-line import/no-extraneous-dependencies, global-require
	const { liveUpdatesManager } = require('./services/liveUpdates');

	if (typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.MODE === 'test') {
		// If the real manager already exists, replace network methods with
		// synchronous, nevertheless-spyable implementations.
		liveUpdatesManager.connect = async (opts = {}) => {
			liveUpdatesManager.isConnected = true;
			return Promise.resolve();
		};

		liveUpdatesManager.subscribeToTrainMovements = (cb) => {
			// record last callback so tests can exercise it if needed
			liveUpdatesManager._lastCallback = cb;
			return 'mock-sub-train';
		};
		liveUpdatesManager.subscribeToBusMovements = (cb) => {
			liveUpdatesManager._lastCallback = cb;
			return 'mock-sub-bus';
		};
		liveUpdatesManager.subscribeToAlerts = (cb) => {
			liveUpdatesManager._lastCallback = cb;
			return 'mock-sub-alerts';
		};

		liveUpdatesManager.unsubscribe = (id) => {
			// no-op but keep signature
			return undefined;
		};

		liveUpdatesManager.disconnect = async () => {
			liveUpdatesManager.isConnected = false;
			return Promise.resolve();
		};
	}
} catch (e) {
	// best-effort shim; if it fails, tests can still mock the module directly.
}
	const _store = new Map();
	globalThis.localStorage = {
		getItem: (key) => {
			const v = _store.get(String(key));
			return v === undefined ? null : v;
		},
		setItem: (key, value) => {
			_store.set(String(key), String(value));
		},
		removeItem: (key) => {
			_store.delete(String(key));
		},
		clear: () => {
			_store.clear();
		},
	};
}
