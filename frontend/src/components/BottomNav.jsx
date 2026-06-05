// BottomNav.jsx — navegación inferior (portado de cm-app.jsx)
import { SANS } from '../theme.js'

export const NAV = [
  { id: 'hoy',        label: 'Hoy',      icon: 'hoy'   },
  { id: 'patrones',   label: 'Patrones', icon: 'trend' },
  { id: 'registro',   label: 'Registro', icon: 'plus'  },
  { id: 'copiloto',   label: 'Copiloto', icon: 'spark' },
  { id: 'perfil',     label: 'Perfil',   icon: 'user'  },
]

function NavIcon({ kind, color }) {
  const s = { fill: 'none', stroke: color, strokeWidth: 1.5, strokeLinecap: 'round', strokeLinejoin: 'round' }
  return (
    <svg width="23" height="23" viewBox="0 0 24 24">
      {kind === 'hoy'   && <g {...s}><circle cx="12" cy="12" r="6.5"/><circle cx="12" cy="12" r="2" fill={color} stroke="none"/></g>}
      {kind === 'trend' && <g {...s}><path d="M3 16c3-1 4-7 7-7s4 5 7 1.5"/><circle cx="3" cy="16" r="1.1" fill={color} stroke="none"/><circle cx="17" cy="10.5" r="1.1" fill={color} stroke="none"/></g>}
      {kind === 'plus'  && <g {...s}><circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/></g>}
      {kind === 'spark' && <g {...s}><path d="M7.5 4.2 8.4 6.6 10.8 7.5 8.4 8.4 7.5 10.8 6.6 8.4 4.2 7.5 6.6 6.6Z"/><path d="M16 12l1.3 3.4 3.4 1.3-3.4 1.3L16 21.4l-1.3-3.4-3.4-1.3 3.4-1.3Z" strokeOpacity="0.6"/></g>}
      {kind === 'user'  && <g {...s}><circle cx="12" cy="8.5" r="3.3"/><path d="M5.5 19c.6-3.4 3.2-5 6.5-5s5.9 1.6 6.5 5"/></g>}
    </svg>
  )
}

export default function BottomNav({ theme, current, onChange }) {
  return (
    <div style={{
      position: 'absolute', left: 14, right: 14, bottom: 'max(8px, env(safe-area-inset-bottom))', zIndex: 40, borderRadius: 32,
      background: theme.dark ? 'rgba(14,16,34,0.66)' : 'rgba(255,255,255,0.7)',
      backdropFilter: 'blur(30px) saturate(180%)', WebkitBackdropFilter: 'blur(30px) saturate(180%)',
      border: `0.5px solid ${theme.border}`,
      boxShadow: theme.dark ? '0 16px 44px rgba(0,0,0,0.5)' : '0 12px 36px rgba(40,30,70,0.14)',
      padding: '10px 4px', display: 'flex', alignItems: 'center', fontFamily: SANS,
    }}>
      {NAV.map(item => {
        const on = current === item.id
        return (
          <div key={item.id} onClick={() => onChange(item.id)} style={{
            flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4,
            padding: '5px 0', cursor: 'pointer', position: 'relative',
            color: on ? theme.ink : theme.inkFaint, transition: 'color 0.3s',
          }}>
            {on && <div style={{ position: 'absolute', top: -2, width: 40, height: 40, borderRadius: 20, background: `radial-gradient(circle, ${theme.accent}33 0%, transparent 70%)` }}/>}
            <div style={{ position: 'relative', zIndex: 1 }}><NavIcon kind={item.icon} color={on ? theme.ink : theme.inkFaint}/></div>
            <span style={{ fontSize: 9.5, letterSpacing: '0.02em', position: 'relative', zIndex: 1 }}>{item.label}</span>
          </div>
        )
      })}
    </div>
  )
}
