// liveActivityBridge.js — puente hacia el plugin nativo OrbitDriveActivity.
// En navegador/Railway (sin plugin) todo es no-op → Drive Mode web sigue igual.
// El `state` que se pasa es el payload de /api/copilot/drive.

function plugin() {
  try {
    return (window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.OrbitDriveActivity) || null
  } catch {
    return null
  }
}

export async function isLiveActivityAvailable() {
  const p = plugin()
  if (!p) return false
  try {
    const r = await p.isLiveActivityAvailable()
    return !!(r && r.available)
  } catch {
    return false
  }
}

export async function startDriveActivity(state) {
  const p = plugin()
  if (!p) return false
  try { const r = await p.startDriveActivity({ state }); return !!(r && r.ok) }
  catch { return false }
}

export async function updateDriveActivity(state) {
  const p = plugin()
  if (!p) return false
  try { await p.updateDriveActivity({ state }); return true }
  catch { return false }
}

export async function endDriveActivity() {
  const p = plugin()
  if (!p) return
  try { await p.endDriveActivity() } catch {}
}

// Escucha el push token de ActivityKit (evento nativo "drivePushToken").
// cb recibe { token }. Devuelve una función para remover el listener.
// En navegador (sin plugin) → no-op.
export function onDrivePushToken(cb) {
  const p = plugin()
  if (!p || !p.addListener) return () => {}
  let handle = null
  try { handle = p.addListener('drivePushToken', cb) } catch { return () => {} }
  return () => { try { handle?.remove?.() } catch {} }
}
