// AgroStar service worker — caches the app shell (CSS/JS/icons) so the app
// still opens and looks right even with no signal, which matters a lot in
// rural areas with patchy connectivity. API data itself is NOT cached here
// (that's handled by /api/sync_status + the offline banner in base.html) —
// this worker only guarantees the app shell always loads.

const CACHE_NAME = "agrostar-shell-v2";
const SHELL_ASSETS = [
  "/static/css/style.css",
  "/static/js/app.js",
  "/static/js/reminders.js",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Cache-first for static shell assets, network-first for everything else
// (pages/API) so data stays fresh whenever there IS a connection.
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  if (SHELL_ASSETS.some((a) => url.pathname === a)) {
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetch(event.request))
    );
    return;
  }

  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request))
  );
});

// ---------------------------------------------------------------------------
// NEW: Push Notifications — receives a Web Push event (sent by the backend
// via /api/push_*), shows it as a native OS/browser notification, and
// focuses/opens the app to the relevant page when tapped.
// ---------------------------------------------------------------------------
self.addEventListener("push", (event) => {
  let payload = { title: "AgroStar", body: "You have a new update.", url: "/" };
  try {
    if (event.data) payload = { ...payload, ...event.data.json() };
  } catch (e) {
    // Non-JSON push payload (shouldn't happen from our backend) — fall back to defaults.
  }

  event.waitUntil(
    self.registration.showNotification(payload.title, {
      body: payload.body,
      icon: "/static/icons/icon-192.png",
      badge: "/static/icons/icon-192.png",
      data: { url: payload.url || "/" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || "/";

  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url.includes(targetUrl) && "focus" in client) return client.focus();
      }
      for (const client of clientList) {
        if ("focus" in client) { client.focus(); if ("navigate" in client) client.navigate(targetUrl); return; }
      }
      if (self.clients.openWindow) return self.clients.openWindow(targetUrl);
    })
  );
});
