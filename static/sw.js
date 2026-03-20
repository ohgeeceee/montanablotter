const CACHE_VERSION = 'v1';
const STATIC_CACHE = 'mb-static-' + CACHE_VERSION;
const OFFLINE_URL = '/offline';

const STATIC_ASSETS = [
  '/offline',
];

// Install: pre-cache the offline fallback page
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then(cache => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});

// Activate: clean up old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(k => k !== STATIC_CACHE).map(k => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

// Fetch: network-first for navigation, cache-first for static assets
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // Only handle same-origin requests
  if (url.origin !== location.origin) return;

  // Static assets: cache-first
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(request).then(cached => cached || fetch(request).then(resp => {
        const clone = resp.clone();
        caches.open(STATIC_CACHE).then(cache => cache.put(request, clone));
        return resp;
      }))
    );
    return;
  }

  // Navigation requests: network-first, offline fallback
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() =>
        caches.match(OFFLINE_URL).then(fallback => fallback || new Response('<h1>You are offline</h1>', { status: 200, headers: { 'Content-Type': 'text/html' } }))
      )
    );
    return;
  }
});
