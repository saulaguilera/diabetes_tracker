// api.js — cliente del backend Flask.
// Mismo origen que Flask (en prod bajo /copilot; en dev vía proxy de Vite),
// así la cookie de sesión viaja sola. NO maneja predicciones: el producto
// solo consume datos de estado presente.

const BASE = '/api/copilot'

async function request(path, opts = {}) {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  })
  if (res.status === 401) {
    // sesión no iniciada → redirigir al login de Flask
    window.location.href = '/login'
    throw new Error('no-auth')
  }
  if (!res.ok) throw new Error(`API ${path} → ${res.status}`)
  return res.json()
}

export const apiGet  = (path)        => request(path)
export const apiPost = (path, body)  => request(path, { method: 'POST', body: JSON.stringify(body) })
