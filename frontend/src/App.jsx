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

// Barra superior global de marca: logo Orbit + wordmark.
function TopBar({ theme }) {
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
      <span style={{ fontFamily: SANS, fontSize: 20, fontWeight: 300, letterSpacing: '0.01em', color: theme.ink }}>Orbit</span>
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
  const theme = makeTheme(dark)
  const [tab, setTab] = useState('hoy')
  const [refreshKey, setRefreshKey] = useState(0)
  const vp = useViewport()

  // recarga la pantalla activa; el spinner queda ~650ms mientras refetchea
  const onRefresh = () => {
    setRefreshKey(k => k + 1)
    return new Promise(res => setTimeout(res, 650))
  }

  const screen = {
    hoy:      <Hoy theme={theme} refreshKey={refreshKey} />,
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
        <TopBar theme={theme}/>

        {tab === 'copiloto' ? (
          // height = viewport visible → al abrir el teclado, el input sube con él
          <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: vp.h, paddingTop: 'calc(52px + env(safe-area-inset-top))' }}>{screen}</div>
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
      </div>
    </div>
  )
}
