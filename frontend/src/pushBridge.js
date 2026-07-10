// pushBridge.js — puente al plugin nativo OrbitPush (notificaciones normales).
// Pide permiso + registra el dispositivo en APNs; el token llega por el evento
// "appPushToken" y se registra en el backend. En navegador/Railway el plugin
// no existe → todo es no-op (la web sigue igual).

function plugin() {
  try {
    return (window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.OrbitPush) || null
  } catch {
    return null
  }
}

// Inicia el flujo completo: listener del token + pedido de permiso/registro.
// cb recibe el token hex. Devuelve una función para remover el listener.
export function initAppPush(cb) {
  const p = plugin()
  if (!p || !p.addListener) return () => {}
  let handle = null
  try {
    const res = p.addListener('appPushToken', (d) => { if (d && d.token) cb(d.token) })
    if (res && typeof res.then === 'function') res.then(h => { handle = h }).catch(() => {})
    else handle = res
  } catch {}
  try { p.register() } catch {}
  return () => { try { handle && handle.remove && handle.remove() } catch {} }
}
