const CACHE_NAME = 'verivault-v2';

// Only things that are identical for every user. `/` is NOT in this list: it is
// behind @login_required and renders per-role, so caching it hands the next
// person on a shared classroom phone the previous account's landing page.
const ASSETS_TO_CACHE = [
    '/static/offline.html',
    '/static/manifest.json',
    '/static/icons/icon-192.png',
    '/static/icons/icon-512.png',
    '/static/icons/favicon-32.png'
];

// Streams, live APIs and biometric media must never be intercepted or stored.
const NEVER_CACHE = ['/video_feed', '/socket.io', '/api/', '/media/', '/logout',
                     '/login', '/static/profiles/', '/static/attendance_captures/'];

self.addEventListener('install', (event) => {
    event.waitUntil(
        // addAll rejects the whole install if any one entry fails, which would
        // leave the app with no service worker at all. Cache them individually.
        caches.open(CACHE_NAME).then((cache) => Promise.all(
            ASSETS_TO_CACHE.map((url) => cache.add(url).catch(() => null))
        )).then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(
                keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
            ))
            .then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    const req = event.request;
    if (req.method !== 'GET') return;

    const url = new URL(req.url);
    if (url.origin !== self.location.origin) return;
    if (NEVER_CACHE.some((path) => url.pathname.startsWith(path))) return;

    // A page request that cannot reach the network gets the offline notice,
    // never a stale copy of somebody's dashboard.
    if (req.mode === 'navigate') {
        event.respondWith(
            fetch(req).catch(() => caches.match('/static/offline.html'))
        );
        return;
    }

    // Static assets only: stale-while-revalidate.
    if (url.pathname.startsWith('/static/')) {
        event.respondWith(
            caches.open(CACHE_NAME).then(async (cache) => {
                const hit = await cache.match(req);
                const live = fetch(req).then((res) => {
                    if (res.ok) cache.put(req, res.clone());
                    return res;
                }).catch(() => hit);
                return hit || live;
            })
        );
    }
    // Everything else: untouched, straight to the network.
});
