const CACHE = "llama-a-jaime-servicios-v5-3-3-boton-solicitud";
const SHELL = [
  "/app/",
  "/app/manifest.webmanifest",
  "/app/assets/icon-192.png",
  "/app/assets/icon-512.png",
  "/app/assets/icon-maskable-512.png",
  "/app/assets/apple-touch-icon.png",
  "/app/assets/logo-llama-a-jaime.png",
  "/app/assets/logo-llama-a-jaime-stacked.svg",
  "/app/assets/categories/hogar.webp",
  "/app/assets/categories/limpieza.webp",
  "/app/assets/categories/fletes.webp",
  "/app/assets/categories/jardineria.webp",
  "/app/assets/categories/belleza.webp",
  "/app/assets/categories/reciclaje.webp",
  "/app/assets/categories/otros.webp"
];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", event => {
  if (event.request.method !== "GET" || event.request.url.includes("/app/api/")) return;
  event.respondWith(
    fetch(event.request)
      .then(response => {
        const copy = response.clone();
        caches.open(CACHE).then(cache => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request).then(hit => hit || caches.match("/app/")))
  );
});

self.addEventListener("push", event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { data = {}; }
  const title = data.title || "Llama a Jaime";
  const options = {
    body: data.body || "Tienes una nueva oportunidad disponible.",
    icon: "/app/assets/icon-192.png",
    badge: "/app/assets/icon-192.png",
    tag: data.tag || "llama-a-jaime-oportunidad",
    renotify: true,
    data: { url: data.url || "/app/?view=provider" }
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const target = event.notification.data?.url || "/app/?view=provider";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(clients => {
      for (const client of clients) {
        if ("focus" in client) {
          client.navigate(target);
          return client.focus();
        }
      }
      return self.clients.openWindow ? self.clients.openWindow(target) : undefined;
    })
  );
});
