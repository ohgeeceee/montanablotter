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

  // Pass through non-GET requests (form POSTs, etc.) untouched. Intercepting a
  // form-submit navigation here and following its cross-origin 302 (e.g. to
  // checkout.stripe.com) yields a cross-origin body the browser cannot use as a
  // top-level navigation, silently dropping the redirect. Let the browser handle
  // these natively so server redirects are followed correctly.
  if (request.method !== 'GET') return;

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

// Push: show notification from server payload
self.addEventListener('push', event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) {}
  const title = data.title || 'Montana Blotter';
  const options = {
    body: data.body || 'New activity in your area.',
    icon: data.icon || '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png',
    data: { url: data.url || '/' },
    vibrate: [100, 50, 100],
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

// NotificationClick: focus existing tab or open new window
self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      for (const client of list) {
        if (client.url === url && 'focus' in client) return client.focus();
      }
      return clients.openWindow(url);
    })
  );
});
