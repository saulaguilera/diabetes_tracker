// DriveModeScreen.jsx — contenedor de Drive Mode (pantalla completa).
// Consume GET /api/copilot/drive, auto-refresca, mantiene la pantalla encendida.
// Modo demo: previsualiza los 6 estados (toca para ciclar). No usa predicción.
import { useState, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { apiGet, apiPost } from '../api.js'
import { useLang } from '../i18n.jsx'
import { Loading } from '../components/ui.jsx'
import DriveModeExpanded from './DriveModeExpanded.jsx'
import { DEMO_STATES } from './demoStates.js'
import { startDriveActivity, updateDriveActivity, endDriveActivity, onDrivePushToken } from './liveActivityBridge.js'

const SANS = '"Outfit", -apple-system, system-ui, sans-serif'
const POLL_MS = 30000

export default function DriveModeScreen({ onClose, demo = false }) {
  const { t } = useLang()
  const [data, setData] = useState(demo ? DEMO_STATES[0] : null)
  const [err, setErr] = useState(false)
  const [demoIdx, setDemoIdx] = useState(0)
  const wakeRef = useRef(null)
  const laStartedRef = useRef(false)   // Live Activity ya iniciada?
  // salida suave: anima el fade-out antes de desmontar
  const [closing, setClosing] = useState(false)
  const requestClose = () => { if (!closing) { setClosing(true); setTimeout(onClose, 240) } }

  // datos en vivo (no en demo) + puente a la Live Activity nativa (si existe)
  useEffect(() => {
    if (demo) return
    let alive = true
    const load = () => apiGet('/drive')
      .then(d => {
        if (!alive) return
        setData(d.drive); setErr(false)
        // Live Activity: iniciar la primera vez, actualizar después.
        // En navegador/Railway el bridge es no-op → no rompe nada.
        if (!laStartedRef.current) { startDriveActivity(d.drive); laStartedRef.current = true }
        else { updateDriveActivity(d.drive) }
      })
      .catch(() => { if (alive) setErr(true) })
    load()
    const id = setInterval(load, POLL_MS)
    return () => {
      alive = false
      clearInterval(id)
      endDriveActivity()          // terminar al salir de Drive Mode
      laStartedRef.current = false
    }
  }, [demo])

  // push token de ActivityKit → registrarlo en el backend (updates por APNs
  // en background). En navegador el listener es no-op; si el POST falla no
  // pasa nada: la Live Activity sigue con updates locales.
  useEffect(() => {
    if (demo) return
    const off = onDrivePushToken(({ token }) => {
      if (token) apiPost('/drive/push-token', { token }).catch(() => {})
    })
    return off
  }, [demo])

  // mantener la pantalla encendida mientras se conduce
  useEffect(() => {
    let released = false
    async function lock() {
      try { wakeRef.current = await navigator.wakeLock?.request('screen') } catch {}
    }
    lock()
    const onVis = () => { if (document.visibilityState === 'visible') lock() }
    document.addEventListener('visibilitychange', onVis)
    return () => {
      released = true
      document.removeEventListener('visibilitychange', onVis)
      try { wakeRef.current?.release() } catch {}
    }
  }, [])

  const cycleDemo = () => {
    const n = (demoIdx + 1) % DEMO_STATES.length
    setDemoIdx(n); setData(DEMO_STATES[n])
  }

  let body
  if (data) {
    body = (
      <div onClick={demo ? cycleDemo : undefined} style={{ position: 'absolute', inset: 0,
        animation: closing ? 'scaleOut 0.24s ease forwards' : 'scaleIn 0.3s cubic-bezier(.2,.8,.2,1)' }}>
        <DriveModeExpanded data={data} onClose={requestClose}/>
        {demo && (
          <div style={{ position: 'absolute', bottom: 'max(54px, env(safe-area-inset-bottom))', left: 0, right: 0,
            textAlign: 'center', fontSize: 12, letterSpacing: '0.12em', color: 'rgba(234,242,248,0.4)',
            fontFamily: SANS, pointerEvents: 'none' }}>
            DEMO · {DEMO_STATES[demoIdx].label} · tocá para cambiar
          </div>
        )}
      </div>
    )
  } else {
    body = (
      <div style={{ position: 'fixed', inset: 0, zIndex: 300, background: '#04060C', color: 'rgba(234,242,248,0.6)',
        display: 'grid', placeItems: 'center', fontFamily: SANS, fontSize: 15,
        animation: closing ? 'scaleOut 0.24s ease forwards' : 'scaleIn 0.3s cubic-bezier(.2,.8,.2,1)' }}>
        {err ? t('drive.loadError') : <Loading label={t('common.loading')} size={56}/>}
        <button onClick={requestClose} style={{ position: 'absolute', top: 'max(20px,env(safe-area-inset-top))', right: 24,
          width: 40, height: 40, borderRadius: 20, border: '1px solid rgba(255,255,255,0.12)',
          background: 'transparent', color: 'inherit', fontSize: 20, cursor: 'pointer' }}>✕</button>
      </div>
    )
  }

  return createPortal(body, document.body)
}
