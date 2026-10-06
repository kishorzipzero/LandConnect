// Offline support: the app shell is cached, and every result / map image the officer has opened stays
// available without a network (network-first, cache fallback). Imagery tiles from other hosts are cached
// opportunistically so a previously viewed area still shows a basemap offline.
// The app's own files are always fetched past the browser's HTTP cache ({cache: "no-cache"}), and a new
// version of this worker reloads open tabs once, so an update appears without clearing anything by hand.
const CACHE = "landconnect-v10004";
const SHELL = ["/", "/static/style.css", "/static/app.js", "/static/vendor/leaflet.js", "/static/vendor/protomaps-leaflet.js", "/static/vendor/leaflet.css",
  "/static/vendor/fonts/Geist-Variable.woff2", "/static/vendor/fonts/GeistMono-Variable.woff2", "/manifest.webmanifest"];

self.addEventListener("install", e => e.waitUntil(caches.open(CACHE)
  .then(c => c.addAll(SHELL.map(u => new Request(u, { cache: "reload" }))))
  .then(() => self.skipWaiting())));
self.addEventListener("activate", e => e.waitUntil(caches.keys()
  .then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
  .then(() => self.clients.claim())
  .then(() => self.clients.matchAll({ type: "window" }))
  .then(tabs => tabs.forEach(t => t.navigate(t.url).catch(() => { })))));   // show the new version at once

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.pathname.startsWith("/statewide/")) return;   // large tile files are read in byte ranges: leave to the browser
  const tile = /arcgisonline|cartocdn|googleapis/.test(url.host);
  if (url.origin !== location.origin && !tile) return;
  if (tile) {   // cache-first for imagery tiles
    e.respondWith(caches.open(CACHE).then(async c => (await c.match(req)) || fetch(req).then(r => { c.put(req, r.clone()); return r; })));
    return;
  }
  const fresh = req.mode === "navigate" ? fetch(req.url, { cache: "no-cache", credentials: "same-origin" }) : fetch(req, { cache: "no-cache" });
  e.respondWith(fresh.then(r => {
    if (r.ok) caches.open(CACHE).then(c => c.put(req, r.clone()));
    return r;
  }).catch(() => caches.match(req)));
});
