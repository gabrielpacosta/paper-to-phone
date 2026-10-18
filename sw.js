/* Keeps the app, and once used the converter runtime, available offline. Papers live in IndexedDB. */
var V = 'p2p-shell-v2', RUNTIME = 'p2p-pyodide-0.28.3';
var SHELL = ['./', 'index.html', 'app.js', 'config.js', 'convert-worker.js', 'converter/paper2phone.py',
             'vendor/msal-browser-4.30.0.min.js', 'manifest.webmanifest', 'icons/icon-192.png', 'icons/apple-touch-icon.png'];
self.addEventListener('install', function (e) {
  e.waitUntil(caches.open(V).then(function (c) { return c.addAll(SHELL); }).then(function () { return self.skipWaiting(); }));
});
self.addEventListener('activate', function (e) {
  e.waitUntil(caches.keys().then(function (ks) {
    return Promise.all(ks.filter(function (k) { return k !== V && k !== RUNTIME; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});
self.addEventListener('fetch', function (e) {
  var u = new URL(e.request.url);
  if (e.request.method !== 'GET') return;
  if (u.host === 'cdn.jsdelivr.net' && u.pathname.indexOf('/pyodide/v0.28.3/') === 0) {     // never changes: cache first
    e.respondWith(caches.open(RUNTIME).then(function (c) {
      return c.match(e.request).then(function (hit) {
        return hit || fetch(e.request).then(function (r) { if (r.ok) c.put(e.request, r.clone()); return r; });
      });
    }));
    return;
  }
  if (u.origin !== location.origin) return;
  e.respondWith(fetch(e.request).then(function (r) {
    if (r.ok) { var copy = r.clone(); caches.open(V).then(function (c) { c.put(e.request, copy); }); }
    return r;
  }).catch(function () {
    return caches.match(e.request, { ignoreSearch: true }).then(function (r) { return r || caches.match('index.html'); });
  }));
});
