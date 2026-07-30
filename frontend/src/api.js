// api.js — cliente del backend Flask.
// Mismo origen que Flask (en prod bajo /copilot; en dev vía proxy de Vite),
// así la cookie de sesión viaja sola. NO maneja predicciones: el producto
// solo consume datos de estado presente.

const BASE = '/api/copilot'

// zona horaria del dispositivo: viaja en cada request para que el backend
// calcule TODAS las horas en la zona real del usuario (clave al viajar)
const TZ = (() => {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || '' } catch { return '' }
})()

// auto-recarga tras un deploy: el backend manda el bundle vivo en cada
// respuesta; si esta página corre uno más viejo (quedó abierta días en el
// WebView), se recarga UNA vez para tomar la versión nueva
function checarVersion(res) {
  try {
    const vivo = res.headers.get('X-Orbit-Bundle')
    if (!vivo) return
    const mio = [...document.scripts].map(s => s.src).find(s => s.includes('/assets/index-'))
    if (mio && !mio.includes(vivo) && sessionStorage.getItem('orbit_recarga') !== vivo) {
      sessionStorage.setItem('orbit_recarga', vivo)   // evita loop si algo falla
      window.location.reload()
    }
  } catch {}
}

async function request(path, opts = {}) {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-Orbit-TZ': TZ, ...(opts.headers || {}) },
    ...opts,
  })
  checarVersion(res)
  if (res.status === 401) {
    // sesión no iniciada → login de Flask, volviendo a /copilot al terminar
    window.location.href = '/login?next=' + encodeURIComponent('/copilot')
    throw new Error('no-auth')
  }
  if (!res.ok) {
    // si el backend mandó un mensaje de error específico, propagarlo tal
    // cual (p. ej. «Tu cuenta LibreLinkUp no tiene sensores vinculados…»)
    let detalle = null
    try { detalle = (await res.json()).error || null } catch {}
    const e = new Error(detalle || `API ${path} → ${res.status}`)
    e.server = !!detalle
    throw e
  }
  return res.json()
}

export const apiGet    = (path)       => request(path)
export const apiPost   = (path, body) => request(path, { method: 'POST', body: JSON.stringify(body) })
export const apiPut    = (path, body) => request(path, { method: 'PUT', body: JSON.stringify(body) })
export const apiDelete = (path)       => request(path, { method: 'DELETE' })
