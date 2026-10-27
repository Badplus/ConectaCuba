self.addEventListener('push', event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (_) {}
  const title = data.title || 'Mercado BETO Plus';
  const url = typeof data.url === 'string' && data.url.startsWith('/') && !data.url.startsWith('//') ? data.url : '/notifications';
  event.waitUntil(self.registration.showNotification(title, {
    body: data.body || 'Tienes una nueva notificación.',
    icon: '/static/logo_beto_plus_cabecera.webp',
    badge: '/static/logo_beto_plus_cabecera.webp',
    data: {url},
    tag: 'beto-' + Date.now()
  }));
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  const target = new URL(event.notification.data?.url || '/notifications', self.location.origin).href;
  event.waitUntil(clients.matchAll({type:'window', includeUncontrolled:true}).then(async windows => {
    for (const client of windows) {
      if (client.url === target) { await client.focus(); return; }
    }
    return clients.openWindow(target);
  }));
});
