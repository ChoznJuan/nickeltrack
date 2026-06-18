// NickelTrack service worker
//
// Cache strategy:
//   - HTML pages:        network-first, fall back to cache (offline UI shell)
//   - /static/* assets:  cache-first (immutable per deploy)
//   - /api/search, /api/food/*:
//                       stale-while-revalidate (option B — show cached
//                       results instantly, refresh in background when online)
//   - /api/config:       cache-first (rarely changes)
//   - /healthz:          network-only (no caching)

const CACHE_VERSION = "v1";
const STATIC_CACHE = `nickeltrack-static-${CACHE_VERSION}`;
const API_CACHE = `nickeltrack-api-${CACHE_VERSION}`;
const HTML_CACHE = `nickeltrack-html-${CACHE_VERSION}`;

const STATIC_ASSETS = [
  "/",
  "/static/manifest.webmanifest",
  "/static/style.css",
  "/static/app.js",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/icon-maskable-512.png",
  "/static/icons/apple-touch-icon.png",
  "/static/icons/favicon-32.png",
  "/static/icons/favicon.ico",
];

// ─────────────────────────────────────────────────────────────
// Install: precache the static shell so the app loads offline
// ─────────────────────────────────────────────────────────────
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) =>
      cache.addAll(STATIC_ASSETS).catch((err) => {
        // Don't fail the install if a single asset 404s — just log it.
        console.warn("[sw] precache partial failure", err);
      })
    )
  );
  // Take over immediately when a new version installs
  self.skipWaiting();
});

// ─────────────────────────────────────────────────────────────
// Activate: drop old caches
// ─────────────────────────────────────────────────────────────
self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys
          .filter((k) =>
            k.startsWith("nickeltrack-") && !k.endsWith(`-${CACHE_VERSION}`)
          )
          .map((k) => caches.delete(k))
      );
      await self.clients.claim();
    })()
  );
});

// ─────────────────────────────────────────────────────────────
// Fetch: route by request type
// ─────────────────────────────────────────────────────────────
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Only handle same-origin
  if (url.origin !== self.location.origin) return;

  // Skip non-GET
  if (event.request.method !== "GET") return;

  // /healthz — network only, never cache
  if (url.pathname === "/healthz") return;

  // HTML pages (root only, since this is a single-page app)
  if (event.request.mode === "navigate" || url.pathname === "/") {
    event.respondWith(networkFirst(event.request, HTML_CACHE));
    return;
  }

  // API: stale-while-revalidate for search/food
  if (url.pathname.startsWith("/api/search") || url.pathname.startsWith("/api/food/")) {
    event.respondWith(staleWhileRevalidate(event.request, API_CACHE));
    return;
  }

  // API: cache-first for config
  if (url.pathname.startsWith("/api/config")) {
    event.respondWith(cacheFirst(event.request, API_CACHE));
    return;
  }

  // Static assets: cache-first
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(cacheFirst(event.request, STATIC_CACHE));
    return;
  }
});

// ─────────────────────────────────────────────────────────────
// Strategy helpers
// ─────────────────────────────────────────────────────────────

async function networkFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const response = await fetch(request);
    if (response && response.ok) {
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    const cached = await cache.match(request);
    if (cached) return cached;
    // Last-resort: a minimal offline page so the user knows what's going on
    return new Response(
      "<!DOCTYPE html><html><body style='font-family:sans-serif;padding:2rem;'>" +
        "<h1>🥬 NickelTrack</h1>" +
        "<p>You're offline and we don't have this page cached yet.</p>" +
        "<p>Open the app once while online to cache it for next time.</p>" +
        "</body></html>",
      { status: 503, headers: { "Content-Type": "text/html" } }
    );
  }
}

async function cacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  if (cached) return cached;
  try {
    const response = await fetch(request);
    if (response && response.ok) {
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    return new Response("", { status: 503 });
  }
}

async function staleWhileRevalidate(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  // Always try to fetch in the background
  const fetchPromise = fetch(request)
    .then((response) => {
      if (response && response.ok) {
        cache.put(request, response.clone());
      }
      return response;
    })
    .catch(() => null);
  // Return cached immediately if we have it; otherwise wait for the network
  return cached || (await fetchPromise) || new Response(
    JSON.stringify({ results: [], count: 0, query: "", offline: true }),
    { status: 503, headers: { "Content-Type": "application/json" } }
  );
}

// ─────────────────────────────────────────────────────────────
// Message: allow the page to request a cache clear (e.g., on logout
// or when a new deploy lands)
// ─────────────────────────────────────────────────────────────
self.addEventListener("message", (event) => {
  if (event.data === "skipWaiting") {
    self.skipWaiting();
  } else if (event.data === "clearApiCache") {
    event.waitUntil(caches.delete(API_CACHE));
  }
});
