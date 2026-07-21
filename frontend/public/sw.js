// chiatienan service worker — app-shell offline support.
//
// Strategy:
//   • navigations (HTML)        → network-first, fall back to the cached page,
//                                 then to the cached app shell ("/"). Lets the
//                                 installed PWA open with no network.
//   • /_next/static/*, icons    → cache-first (content-hashed / immutable).
//   • other same-origin GETs    → stale-while-revalidate.
//   • /api/*, /internal/*, SSE,
//     and any non-GET            → network-only, never cached (auth'd + dynamic;
//                                 the stream must not be buffered by a cache).
//
// Bump CACHE to invalidate everything on the next activate.
const CACHE = "chiatienan-v1";
const SHELL = ["/", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE);
      // addAll is atomic-ish; ignore individual failures so a single 404 can't
      // wedge the whole install.
      await Promise.all(SHELL.map((u) => cache.add(u).catch(() => {})));
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)));
      await self.clients.claim();
    })(),
  );
});

function isStaticAsset(url) {
  return (
    url.pathname.startsWith("/_next/static/") ||
    url.pathname === "/manifest.webmanifest" ||
    /\.(?:png|jpg|jpeg|svg|gif|webp|ico|woff2?|ttf)$/.test(url.pathname)
  );
}

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  const res = await fetch(request);
  if (res && res.ok && res.type === "basic") {
    const cache = await caches.open(CACHE);
    cache.put(request, res.clone());
  }
  return res;
}

async function staleWhileRevalidate(request) {
  const cached = await caches.match(request);
  const network = fetch(request)
    .then((res) => {
      if (res && res.ok && res.type === "basic") {
        caches.open(CACHE).then((c) => c.put(request, res.clone()));
      }
      return res;
    })
    .catch(() => cached);
  return cached || network;
}

async function navigationHandler(request) {
  try {
    const res = await fetch(request);
    if (res && res.ok) {
      const cache = await caches.open(CACHE);
      cache.put(request, res.clone());
    }
    return res;
  } catch {
    return (await caches.match(request)) || (await caches.match("/")) || Response.error();
  }
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Only ever touch same-origin GETs. Everything else (POST, the auth'd API,
  // and the SSE stream) goes straight to the network, untouched.
  if (request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/internal/")) return;

  if (request.mode === "navigate") {
    event.respondWith(navigationHandler(request));
    return;
  }
  if (isStaticAsset(url)) {
    event.respondWith(cacheFirst(request));
    return;
  }
  event.respondWith(staleWhileRevalidate(request));
});
