// DriveModeCompact.jsx — tarjeta compacta tipo widget (Live Activity / lock screen
// preview). Misma fuente de datos que la vista expandida; mínima y glanceable.
const SANS = '"Outfit", -apple-system, system-ui, sans-serif'
const TINT = { positive: '#34D9A0', warning: '#E7A23C', critical: '#FF5A52', muted: '#7C8aa0' }

export default function DriveModeCompact({ data, onClick }) {
  const c = TINT[data.tint] || TINT.muted
  const dim = 'rgba(234,242,248,0.55)'
  return (
    <div onClick={onClick} style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '14px 16px',
      borderRadius: 18, background: '#0A0F1C', border: `1px solid ${c}40`, fontFamily: SANS,
      cursor: onClick ? 'pointer' : 'default', maxWidth: 360 }}>
      <span style={{ width: 8, height: 8, borderRadius: '50%', background: c, boxShadow: `0 0 8px ${c}`, flexShrink: 0 }}/>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
        <span style={{ fontSize: 34, fontWeight: 400, color: c, lineHeight: 1, fontVariantNumeric: 'tabular-nums' }}>{data.value}</span>
        <span style={{ fontSize: 22, color: c }}>{data.trend_arrow}</span>
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: '#EAF2F8', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{data.message}</div>
        <div style={{ fontSize: 11.5, color: dim }}>{data.updated_text}</div>
      </div>
      <span style={{ fontSize: 10, letterSpacing: '0.18em', color: '#9BB3C9', fontWeight: 600 }}>ORBIT</span>
    </div>
  )
}
