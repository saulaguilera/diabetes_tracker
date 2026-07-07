// ui.jsx — primitivos visuales compartidos.
import { useState, useEffect } from 'react'
import { SANS } from '../theme.js'
import OrbitLogo from './OrbitLogo.jsx'

// Revela el texto como si se escribiera (calmo). Respeta prefers-reduced-motion.
// onProgress se llama en cada carácter (p.ej. para seguir el scroll en el chat).
export function Typewriter({ text, style, onDone, onProgress, total = 2600, minStep = 10 }) {
  const [n, setN] = useState(0)
  useEffect(() => {
    if (!text) { onDone && onDone(); return }
    let reduce = false
    try { reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches } catch {}
    if (reduce) { setN(text.length); onDone && onDone(); return }
    setN(0)
    const step = Math.max(minStep, Math.round(total / text.length))
    let i = 0
    const id = setInterval(() => {
      i += 1
      setN(i)
      onProgress && onProgress()
      if (i >= text.length) { clearInterval(id); onDone && onDone() }
    }, step)
    return () => clearInterval(id)
  }, [text])
  const done = !text || n >= text.length
  return (
    <span style={style}>
      {text ? text.slice(0, n) : ''}
      {!done && <span className="tw-caret" aria-hidden="true">▍</span>}
    </span>
  )
}

// Estado de carga: el logo de Orbit con el satélite orbitando (más rápido).
// Reemplaza el texto "Cargando…" en todas las pantallas.
export function Loading({ theme, label, size = 48 }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14, fontFamily: SANS }}>
      <div className="orbit-loading"><OrbitLogo size={size}/></div>
      {label && (
        <div style={{ color: theme ? theme.inkFaint : 'rgba(234,242,248,0.5)', fontSize: 12.5,
          letterSpacing: '0.16em', textTransform: 'uppercase' }}>{label}</div>
      )}
    </div>
  )
}

// Cierre animado de sheets/overlays: en vez de desmontar de golpe, anima la
// salida y recién entonces llama a onClose. [closing, requestClose]
export function useSheetClose(onClose, ms = 260) {
  const [closing, setClosing] = useState(false)
  const requestClose = () => {
    if (closing) return
    setClosing(true)
    setTimeout(onClose, ms)
  }
  return [closing, requestClose]
}

// estilos listos para backdrop + panel de sheet (entrada y salida suaves)
export const backdropAnim = (closing) =>
  closing ? 'fadeOut 0.24s ease forwards' : 'fadeIn 0.24s ease'
export const sheetAnim = (closing) =>
  closing ? 'slideDown 0.26s cubic-bezier(.4,0,.7,.4) forwards'
          : 'slideUp 0.32s cubic-bezier(.2,.8,.2,1)'

export function Eyebrow({ theme, children, style }) {
  return (
    <div style={{ fontSize: 11, letterSpacing: '0.14em', textTransform: 'uppercase',
      color: theme.inkFaint, fontWeight: 500, ...style }}>{children}</div>
  )
}

export function Card({ theme, children, onClick, glow, style, className }) {
  return (
    <div onClick={onClick} className={className} style={{
      background: theme.surface, border: `0.5px solid ${theme.border}`, borderRadius: 24,
      padding: 18, cursor: onClick ? 'pointer' : 'default',
      boxShadow: glow ? `0 0 40px ${glow}` : 'none', ...style }}>{children}</div>
  )
}

export function Meter({ pct, color, track }) {
  const p = Math.max(0, Math.min(100, pct || 0))
  return (
    <div style={{ height: 7, borderRadius: 100, background: track, overflow: 'hidden' }}>
      <div style={{ width: `${p}%`, height: '100%', background: color, borderRadius: 100,
        transition: 'width 0.6s ease' }}/>
    </div>
  )
}

// ── controles de formulario ───────────────────────────────────────────────────
export function Stepper({ theme, value, setValue, step = 1, min = 0, max = 999, unit, color, big }) {
  const clamp = v => Math.max(min, Math.min(max, +(v).toFixed(2)))
  const Btn = ({ d }) => (
    <button onClick={() => setValue(clamp(value + d * step))}
      style={{ width: big ? 44 : 36, height: big ? 44 : 36, borderRadius: '50%', flexShrink: 0,
        border: `0.5px solid ${theme.borderStrong}`, background: theme.surface, color: theme.ink,
        fontSize: big ? 22 : 18, fontWeight: 300, cursor: 'pointer', display: 'grid', placeItems: 'center', lineHeight: 1 }}>
      {d < 0 ? '−' : '+'}
    </button>
  )
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: big ? 20 : 14 }}>
      <Btn d={-1}/>
      <div style={{ minWidth: big ? 96 : 64, textAlign: 'center', display: 'flex', alignItems: 'baseline', justifyContent: 'center', gap: 4 }}>
        <span style={{ fontSize: big ? 46 : 24, fontWeight: 300, color: color || theme.ink,
          letterSpacing: '-0.02em', fontVariantNumeric: 'tabular-nums', lineHeight: 1 }}>{value}</span>
        {unit && <span style={{ fontSize: big ? 16 : 12, color: theme.inkSoft }}>{unit}</span>}
      </div>
      <Btn d={1}/>
    </div>
  )
}

export function Segmented({ theme, options, value, onChange, color }) {
  return (
    <div style={{ display: 'flex', gap: 4, padding: 4, borderRadius: 14,
      background: theme.dark ? 'rgba(0,0,0,0.22)' : 'rgba(0,0,0,0.05)', border: `0.5px solid ${theme.border}` }}>
      {options.map(o => {
        const on = value === o.id
        return (
          <button key={o.id} onClick={() => onChange(o.id)} style={{
            flex: 1, padding: '9px 6px', borderRadius: 11, border: 'none', cursor: 'pointer',
            background: on ? (color || theme.accent) : 'transparent', color: on ? '#0A0C1E' : theme.inkSoft,
            fontSize: 13, fontWeight: on ? 600 : 400, fontFamily: SANS, transition: 'all 0.2s' }}>
            {o.label}
          </button>
        )
      })}
    </div>
  )
}

// options: array de strings, o de { id, label } (id = valor estable, label = texto visible)
export function Chips({ theme, options, value, onChange, color }) {
  const c = color || theme.accent
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
      {options.map(o => {
        const id = typeof o === 'string' ? o : o.id
        const label = typeof o === 'string' ? o : o.label
        const on = value === id
        return (
          <button key={id} onClick={() => onChange(id)} style={{
            padding: '8px 14px', borderRadius: 100, cursor: 'pointer', fontFamily: SANS, fontSize: 13,
            background: on ? `${c}22` : theme.surface, color: on ? c : theme.inkSoft,
            border: `0.5px solid ${on ? c + '88' : theme.border}`, transition: 'all 0.2s' }}>{label}</button>
        )
      })}
    </div>
  )
}

export function Field({ theme, value, onChange, placeholder }) {
  return (
    <input value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder}
      style={{ width: '100%', padding: '12px 14px', borderRadius: 14, fontSize: 15, fontFamily: SANS,
        background: theme.surface, border: `0.5px solid ${theme.border}`, color: theme.ink, outline: 'none' }}/>
  )
}
