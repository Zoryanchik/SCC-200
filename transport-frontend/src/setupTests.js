import '@testing-library/jest-dom'

// Global test setup only; API modules are mocked explicitly in each test file.
// Provide a simple in-memory localStorage shim for the Node test environment
if (typeof globalThis.localStorage === 'undefined') {
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
