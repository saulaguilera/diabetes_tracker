// ui.jsx — primitivos visuales compartidos (tarjeta, eyebrow, medidor).
export function Eyebrow({ theme, children, style }) {
  return (
    <div style={{ fontSize: 11, letterSpacing: '0.14em', textTransform: 'uppercase',
      color: theme.inkFaint, fontWeight: 500, ...style }}>{children}</div>
  )
}

export function Card({ theme, children, onClick, glow, style }) {
  return (
    <div onClick={onClick} style={{
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
