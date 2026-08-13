// chiatienan service worker — app-shell offline support.
//
// Strategy:
//   • navigations (HTML)        → network-first, fall back to the cached page,
//                                 then to the cached app shell ("/"). Lets the
//                                 installed PWA open with no network.
//   • /_next/static/*           → cache-first (content-hashed, immutable).
//   • other same-origin GETs    → stale-while-revalidate (incl. the icons and
//                                 the manifest — see below).
//   • /api/*, /internal/*, SSE,
//     and any non-GET            → network-only, never cached (auth'd + dynamic;
//                                 the stream must not be buffered by a cache).
//
// Icons and the manifest used to be cache-first alongside /_next/static/*, but
// they are the one group of assets whose URL never changes when their bytes do:
// cache-first meant an installed PWA served the pre-rebrand icon until someone
// remembered to bump CACHE, which is exactly how the new icon failed to reach
// anybody. Stale-while-revalidate still renders them offline and instantly, and
// picks up new art on the next load without a cache name change.
//
// Bump CACHE to invalidate everything on the next activate.
// "phoenix-v2": drops the stale pre-rebrand icon entries cached under v1.
const CACHE = "phoenix-v2";
const SHELL = ["/", "/manifest.webmanifest", "/icon-192.png?v=2", "/icon-512.png?v=2"];

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

function isImmutableAsset(url) {
  // Content-hashed by the build, so the URL changes whenever the bytes do.
  // Fonts under /_next/static/media are covered by the same prefix.
  return url.pathname.startsWith("/_next/static/");
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
  if (isImmutableAsset(url)) {
    event.respondWith(cacheFirst(request));
    return;
  }
  event.respondWith(staleWhileRevalidate(request));
});
