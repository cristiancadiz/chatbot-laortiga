
/* Llama a Jaime — Service Worker para notificaciones push */
'use strict';

self.addEventListener('install', (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('push', (event) => {
  let payload = {};

  if (event.data) {
    try {
      payload = event.data.json();
    } catch (_) {
      payload = { body: event.data.text() };
    }
  }

  if (typeof payload !== 'object' || payload === null) {
    payload = {};
  }

  const title = String(
    payload.title || payload.titulo || 'Llama a Jaime'
  );

  const target = payload.url || payload.link || '/app/';
  const url = new URL(String(target), self.registration.scope);

  if (
    url.origin !== self.location.origin ||
    !url.pathname.startsWith('/app')
  ) {
    url.href = new URL('/app/', self.location.origin).href;
  }

  const options = {
    body: String(
      payload.body ||
      payload.mensaje ||
      'Tienes una nueva notificación.'
    ),
    icon: '/app/assets/icon-192.png',
    badge: '/app/assets/icon-192.png',
    tag: String(payload.tag || 'llama-a-jaime'),
    data: { url: url.href },
  };

  event.waitUntil(
    self.registration.showNotification(title, options)
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  const url =
    event.notification.data?.url ||
    new URL('/app/', self.location.origin).href;

  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({
      type: 'window',
      includeUncontrolled: true
    });

    for (const client of windows) {
      if (client.url === url && 'focus' in client) {
        return client.focus();
      }
    }

    if (self.clients.openWindow) {
      return self.clients.openWindow(url);
    }
  })());
});
