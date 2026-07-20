import "@testing-library/jest-dom/vitest";

// Node 25's built-in experimental Web Storage global (gated behind
// --localstorage-file) shadows jsdom's real localStorage with a broken stub
// (no getItem/setItem/clear) before jsdom's environment can install its own.
// Detect that broken stub and replace it with a working in-memory polyfill
// so tests relying on localStorage behave the same across Node versions.
if (typeof localStorage !== "undefined" && typeof localStorage.clear !== "function") {
  const store = new Map<string, string>();
  const polyfill: Storage = {
    getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
    setItem: (key: string, value: string) => {
      store.set(key, String(value));
    },
    removeItem: (key: string) => {
      store.delete(key);
    },
    clear: () => {
      store.clear();
    },
    key: (index: number) => Array.from(store.keys())[index] ?? null,
    get length() {
      return store.size;
    },
  };
  Object.defineProperty(globalThis, "localStorage", {
    value: polyfill,
    configurable: true,
    writable: true,
  });
}
