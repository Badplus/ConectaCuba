(() => {
  const button = document.getElementById('push-toggle');
  if (!button) return;
  const supported = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  if (!supported) { button.textContent = 'Avisos no compatibles con este navegador'; button.disabled = true; return; }
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const toBytes = value => {
    const base64 = (value + '='.repeat((4 - value.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/');
    return Uint8Array.from(atob(base64), c => c.charCodeAt(0));
  };
  async function state() {
    const reg = await navigator.serviceWorker.register('/sw.js', {scope:'/'});
    const sub = await reg.pushManager.getSubscription();
    button.textContent = sub ? 'Desactivar avisos en este dispositivo' : 'Activar avisos en este dispositivo';
    return {reg, sub};
  }
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      const {reg, sub} = await state();
      if (sub) {
        const response = await fetch('/api/push/unsubscribe', {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({endpoint:sub.endpoint})});
        if (!response.ok) throw new Error('No se pudo desactivar');
        await sub.unsubscribe();
      } else {
        const permission = await Notification.requestPermission();
        if (permission !== 'granted') throw new Error('El navegador no autorizó las notificaciones');
        const response = await fetch('/api/push/public-key', {cache:'no-store'});
        if (!response.ok) throw new Error('No se pudo consultar la configuración');
        const data = await response.json();
        if (!data.publicKey) throw new Error('Falta configurar las claves VAPID en el servidor');
        const subscription = await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:toBytes(data.publicKey)});
        const saved = await fetch('/api/push/subscribe', {method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(subscription.toJSON())});
        if (!saved.ok) throw new Error('No se pudo guardar la suscripción');
      }
      await state();
    } catch (error) { alert(error.message || 'No se pudo configurar las notificaciones'); }
    finally { button.disabled = false; }
  });
  state().catch(() => {button.disabled = true;button.textContent = 'Notificaciones no disponibles';});
})();
