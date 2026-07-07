// App.jsx — shell de Orbit Copilot: fondo cósmico + navegación + pantalla activa.
// Andamiaje: las pantallas son placeholders por ahora (sin datos cableados).
import { useState, useEffect, useRef } from 'react'
import { SANS, PAL, makeTheme } from './theme.js'
import Starfield from './components/Starfield.jsx'
import BottomNav from './components/BottomNav.jsx'
import OrbitLogo from './components/OrbitLogo.jsx'
import Hoy from './screens/Hoy.jsx'
import Registro from './screens/Registro.jsx'
import Patrones from './screens/Patrones.jsx'
import Copiloto from './screens/Copiloto.jsx'
import Perfil from './screens/Perfil.jsx'
import DriveModeScreen from './drive_mode/DriveModeScreen.jsx'
import { useLang } from './i18n.jsx'

// Alto del viewport visible (se achica al abrir el teclado en móvil). Permite
// que el chat del Copiloto deje el input sobre el teclado, sin mover el resto.
function useViewport() {
  const [vp, setVp] = useState(() => ({
    h: (typeof window !== 'undefined' && window.visualViewport) ? window.visualViewport.height : window.innerHeight,
    full: window.innerHeight,
  }))
  useEffect(() => {
    const vv = window.visualViewport
    const on = () => setVp({ h: vv ? vv.height : window.innerHeight, full: window.innerHeight })
    if (vv) { vv.addEventListener('resize', on); vv.addEventListener('scroll', on) }
    window.addEventListener('resize', on)
    return () => {
      if (vv) { vv.removeEventListener('resize', on); vv.removeEventListener('scroll', on) }
      window.removeEventListener('resize', on)
    }
  }, [])
  return vp
}

// Transición suave al cambiar de sección: fundido + deriva hacia arriba + leve
// escala. Calmo, en la temática "que respira". Se re-monta al cambiar de tab.
function SectionTransition({ tabKey, children }) {
  const [shown, setShown] = useState(false)
  useEffect(() => {
    setShown(false)
    const t = setTimeout(() => setShown(true), 16)
    return () => clearTimeout(t)
  }, [tabKey])
  return (
    <div style={{
      opacity: shown ? 1 : 0,
      transform: shown ? 'translateY(0) scale(1)' : 'translateY(14px) scale(0.985)',
      transition: shown
        ? 'opacity 0.5s ease, transform 0.5s cubic-bezier(.2,.8,.2,1)'
        : 'none',
    }}>
      {children}
    </div>
  )
}

// Pull-to-refresh: tirar hacia abajo desde el tope → recarga los datos de la
// pantalla activa (bump de refreshKey). Gesto táctil (móvil).
function PullToRefresh({ theme, onRefresh, children }) {
  const ref = useRef(null)
  const startY = useRef(null)
  const [pull, setPull] = useState(0)
  const [refreshing, setRefreshing] = useState(false)
  const THRESH = 64

  const onStart = (e) => {
    startY.current = (ref.current && ref.current.scrollTop <= 0) ? e.touches[0].clientY : null
  }
  const onMove = (e) => {
    if (startY.current == null || refreshing) return
    const dy = e.touches[0].clientY - startY.current
    setPull(dy > 0 && ref.current.scrollTop <= 0 ? Math.min(80, dy * 0.5) : 0)
  }
  const onEnd = async () => {
    if (refreshing) return
    if (pull >= THRESH * 0.6) {
      setRefreshing(true); setPull(40)
      try { await onRefresh() } finally { setRefreshing(false); setPull(0) }
    } else {
      setPull(0)
    }
    startY.current = null
  }

  return (
    <div ref={ref} onTouchStart={onStart} onTouchMove={onMove} onTouchEnd={onEnd}
      style={{ position: 'absolute', inset: 0, paddingTop: 'calc(52px + env(safe-area-inset-top))', overflowY: 'auto', WebkitOverflowScrolling: 'touch' }}>
      <div style={{ height: refreshing ? 40 : pull, display: 'grid', placeItems: 'center', overflow: 'hidden',
        transition: startY.current == null ? 'height 0.25s ease' : 'none' }}>
        {(pull > 8 || refreshing) && (
          <div className={refreshing ? 'ai-orbit' : ''} style={{ width: 18, height: 18, borderRadius: '50%',
            border: `2px solid ${theme.accent}44`, borderTopColor: theme.accent,
            transform: refreshing ? undefined : `rotate(${pull * 4}deg)`,
            opacity: refreshing ? 1 : Math.min(1, pull / 40) }}/>
        )}
      </div>
      {children}
    </div>
  )
}

// Barra superior global de marca: logo Orbit + wordmark + acceso a Drive Mode.
function TopBar({ theme, onDrive }) {
  const { t } = useLang()
  return (
    <div style={{
      position: 'absolute', top: 0, left: 0, right: 0, zIndex: 30,
      height: 'calc(52px + env(safe-area-inset-top))', paddingTop: 'env(safe-area-inset-top)',
      display: 'flex', alignItems: 'center', gap: 9, paddingLeft: 22, paddingRight: 22, pointerEvents: 'none',
      background: theme.dark
        ? 'linear-gradient(180deg, rgba(6,11,24,0.95) 0%, rgba(6,11,24,0) 100%)'
        : 'linear-gradient(180deg, rgba(230,238,250,0.95) 0%, rgba(230,238,250,0) 100%)',
      backdropFilter: 'blur(2px)', WebkitBackdropFilter: 'blur(2px)',
    }}>
      <OrbitLogo size={26}/>
      {/* wordmark: Orbit (blanco) · Copilot (acento) · AI (badge) */}
      <span style={{ fontFamily: SANS, fontSize: 19, fontWeight: 300, letterSpacing: '0.01em', color: theme.ink, display: 'flex', alignItems: 'center', gap: 6 }}>
        <span>Orbit <span style={{ color: theme.accent, fontWeight: 500 }}>Copilot</span></span>
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.06em', padding: '2px 6px',
          borderRadius: 6, color: theme.accent, background: `${theme.accent}1F`, border: `0.5px solid ${theme.accent}55` }}>AI</span>
      </span>
      <div style={{ flex: 1 }}/>
      {/* Drive Mode — siempre a mano, arriba a la derecha */}
      <button onClick={onDrive} aria-label={t('app.driveAria')} style={{
        pointerEvents: 'auto', width: 36, height: 36, borderRadius: 18, cursor: 'pointer',
        display: 'grid', placeItems: 'center', padding: 0,
        border: `0.5px solid ${theme.border}`,
        background: theme.dark ? 'rgba(255,255,255,0.05)' : 'rgba(255,255,255,0.6)',
      }}>
        <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke={theme.inkSoft}
          strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
          <path d="M5 11l1.5-4.5A2 2 0 0 1 8.4 5h7.2a2 2 0 0 1 1.9 1.5L19 11"/>
          <path d="M5 11h14a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-4a1 1 0 0 1 1-1z"/>
          <circle cx="7.5" cy="14.5" r="1"/><circle cx="16.5" cy="14.5" r="1"/>
        </svg>
      </button>
    </div>
  )
}

export default function App() {
  // tema oscuro/claro, persistido en localStorage (toggle en Perfil)
  const [dark, setDark] = useState(() => {
    try { return localStorage.getItem('orbit_theme') !== 'light' } catch (e) { return true }
  })
  useEffect(() => {
    try { localStorage.setItem('orbit_theme', dark ? 'dark' : 'light') } catch (e) {}
  }, [dark])

  // El documento NUNCA debe scrollear como página (solo scrollean las listas por
  // dentro). iOS desplaza el documento para mostrar el input enfocado → toda la
  // pantalla "sube". Forzamos scroll del documento a 0 para que no se mueva.
  useEffect(() => {
    const lock = () => {
      if (window.scrollY !== 0 || window.scrollX !== 0) window.scrollTo(0, 0)
    }
    window.addEventListener('scroll', lock, { passive: true })
    return () => window.removeEventListener('scroll', lock)
  }, [])

  const theme = makeTheme(dark)
  const [tab, setTab] = useState('hoy')
  const [refreshKey, setRefreshKey] = useState(0)
  const [showDrive, setShowDrive] = useState(false)

  // recarga la pantalla activa; el spinner queda ~650ms mientras refetchea
  const onRefresh = () => {
    setRefreshKey(k => k + 1)
    return new Promise(res => setTimeout(res, 650))
  }

  const screen = {
    hoy:      <Hoy theme={theme} refreshKey={refreshKey} onGoRegistro={() => setTab('registro')} />,
    patrones: <Patrones theme={theme} refreshKey={refreshKey} />,
    registro: <Registro theme={theme} onDone={() => { setRefreshKey(k => k + 1); setTab('hoy') }} />,
    copiloto: <Copiloto theme={theme} />,
    perfil:   <Perfil theme={theme} refreshKey={refreshKey} dark={dark} onToggleTheme={() => setDark(d => !d)} />,
  }[tab]

  return (
    // Marco: position:fixed inset:0 (CSS) → llena exacto la pantalla en standalone.
    <div className="app-frame">
      <div className="app-shell" style={{ background: theme.bg, color: theme.ink, fontFamily: SANS }}>
        {/* glows ambientales — dos capas que respiran en contrafase (profundidad) */}
        <div className="ambient-drift" style={{ position: 'absolute', inset: '-15%', pointerEvents: 'none',
          background: `radial-gradient(60% 32% at 18% 6%, rgba(${PAL.metabolismo.rgb},0.16) 0%, transparent 70%), radial-gradient(55% 30% at 92% 64%, rgba(${PAL.glucosa.rgb},0.12) 0%, transparent 70%), radial-gradient(50% 28% at 50% 100%, rgba(${PAL.ritmo.rgb},0.10) 0%, transparent 70%)` }}/>
        <div className="ambient-drift-2" style={{ position: 'absolute', inset: '-15%', pointerEvents: 'none',
          background: `radial-gradient(50% 30% at 80% 12%, rgba(${PAL.pancreas.rgb},0.10) 0%, transparent 70%), radial-gradient(55% 32% at 12% 78%, rgba(${PAL.ritmo.rgb},0.10) 0%, transparent 70%)` }}/>
        <Starfield count={46} opacity={0.55} seed={3}/>
        <TopBar theme={theme} onDrive={() => setShowDrive(true)}/>

        {tab === 'copiloto' ? (
          <div style={{ position: 'absolute', inset: 0, paddingTop: 'calc(52px + env(safe-area-inset-top))' }}>{screen}</div>
        ) : (
          <PullToRefresh theme={theme} onRefresh={onRefresh}>
            <SectionTransition tabKey={tab}>{screen}</SectionTransition>
          </PullToRefresh>
        )}

        {/* scrim inferior: el contenido se desvanece detrás de la nav (no asoma) */}
        {tab !== 'copiloto' && (
          <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: 'calc(58px + env(safe-area-inset-bottom))', zIndex: 35, pointerEvents: 'none',
            background: `linear-gradient(to top, ${theme.dark ? '#070C18' : '#D6E0EE'} 34%, transparent)` }}/>
        )}

        <BottomNav theme={theme} current={tab} onChange={setTab}/>
        {showDrive && <DriveModeScreen onClose={() => setShowDrive(false)}/>}
      </div>
    </div>
  )
}
