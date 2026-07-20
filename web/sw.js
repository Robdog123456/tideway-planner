/* sw.js — the service worker: what makes the app installable and
   offline-capable.

   Strategy (deliberately simple):
   - the SHELL (html/css/js/icons) is pre-cached at install and served
     cache-first: the app opens instantly, even in airplane mode.
   - the GRID (data/grid_v2.json) is network-first: fresh when online,
     last-good copy when not. app.js keeps a second copy in localStorage
     as belt-and-braces (iOS can evict this cache after weeks of disuse).
   - live externals (EA gauge, Open-Meteo) are NOT intercepted: app.js
     handles their failures itself and falls back to the grid's snapshot.

   Bump VERSION on any shell change — the old cache is dropped on activate. */

const VERSION = "v1";
const SHELL_CACHE = `tideway-shell-${VERSION}`;
const DATA_CACHE = "tideway-data";

const SHELL = [
  "./",
  "./index.html",
  "./styles.css",
  "./app.js",
  "./manifest.webmanifest",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "./icons/apple-touch-icon.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(SHELL_CACHE).then((c) => c.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((k) => k.startsWith("tideway-shell-") && k !== SHELL_CACHE)
          .map((k) => caches.delete(k))
    )).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.origin !== location.origin) return;   // live externals: hands off

  // grid: network-first, keep the last good copy
  if (url.pathname.endsWith("/data/grid_v2.json")) {
    e.respondWith(
      fetch(e.request)
        .then((r) => {
          const copy = r.clone();
          caches.open(DATA_CACHE).then((c) => c.put(e.request, copy));
          return r;
        })
        .catch(() => caches.match(e.request))
    );
    return;
  }

  // shell: cache-first
  e.respondWith(
    caches.match(e.request).then((hit) => hit || fetch(e.request))
  );
});
