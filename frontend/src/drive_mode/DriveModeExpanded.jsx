// DriveModeExpanded.jsx — vista glanceable de conducción (pantalla completa).
// Safety-first: número grande, flecha, estado, frescura. Sin gráficos/dosis/predicción.
// Recibe `data` = payload de /api/copilot/drive (o un demo state).
const SANS = '"Outfit", -apple-system, system-ui, sans-serif'

const TINT = {
  positive: '#34D9A0',   // estable
  warning:  '#E7A23C',   // atención
  critical: '#FF5A52',   // urgente
  muted:    '#7C8aa0',   // datos no confiables
}

function OrbitMark({ color }) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="5" stroke={color} strokeWidth="1.6"/>
      <circle cx="12" cy="12" r="1.7" fill={color}/>
      <ellipse cx="12" cy="12" rx="10" ry="4" stroke={color} strokeWidth="1.2" opacity="0.5" transform="rotate(28 12 12)"/>
    </svg>
  )
}

export default function DriveModeExpanded({ data, onClose }) {
  const c = TINT[data.tint] || TINT.muted
  const urgent = data.level === 'urgent'
  const dim = 'rgba(234,242,248,0.55)'

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 300, background: '#04060C',
      display: 'flex', flexDirection: 'column', fontFamily: SANS, color: '#EAF2F8',
      padding: 'max(20px, env(safe-area-inset-top)) 24px max(20px, env(safe-area-inset-bottom))',
      // halo sutil del color de estado (no animado, no distractivo)
      boxShadow: `inset 0 0 220px ${c}22` }}>

      {/* barra superior: marca · cerrar · sensor/conexión */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <OrbitMark color="#9BB3C9"/>
        <span style={{ fontSize: 14, letterSpacing: '0.22em', color: '#9BB3C9', fontWeight: 600 }}>ORBIT</span>
        <span style={{ fontSize: 12, letterSpacing: '0.14em', color: dim, marginLeft: 4 }}>DRIVE</span>
        <div style={{ flex: 1 }}/>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 13, color: dim }}>
          <span style={{ width: 9, height: 9, borderRadius: '50%',
            background: data.connected ? TINT.positive : TINT.muted,
            boxShadow: data.connected ? `0 0 8px ${TINT.positive}` : 'none' }}/>
          {data.sensor}{data.connected ? '' : ' · offline'}
        </span>
        <button onClick={onClose} aria-label="Cerrar" style={{ marginLeft: 16, width: 40, height: 40,
          borderRadius: 20, border: '1px solid rgba(255,255,255,0.12)', background: 'transparent',
          color: dim, fontSize: 20, lineHeight: 1, cursor: 'pointer' }}>✕</button>
      </div>

      {/* centro: número enorme + flecha */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', gap: 4 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 'clamp(8px,3vw,28px)' }}>
          <span style={{ fontSize: 'clamp(120px, 34vw, 280px)', fontWeight: 300, lineHeight: 0.85,
            letterSpacing: '-0.04em', color: c, fontVariantNumeric: 'tabular-nums' }}>{data.value}</span>
          <span style={{ fontSize: 'clamp(72px, 18vw, 150px)', color: c, lineHeight: 0.85 }}>{data.trend_arrow}</span>
        </div>
        <span style={{ fontSize: 'clamp(16px,4vw,26px)', color: dim, marginTop: 8 }}>{data.unit}</span>

        {/* mensaje de seguridad (corto, claro) */}
        <div style={{ marginTop: 'clamp(18px,5vh,40px)', display: 'inline-flex', alignItems: 'center', gap: 12,
          padding: '12px 26px', borderRadius: 100,
          background: urgent ? c : `${c}1f`, border: `1.5px solid ${urgent ? c : c + '66'}` }}>
          <span style={{ width: 11, height: 11, borderRadius: '50%', background: urgent ? '#fff' : c,
            boxShadow: urgent ? '0 0 0 0' : `0 0 10px ${c}` }}/>
          <span style={{ fontSize: 'clamp(20px,5vw,30px)', fontWeight: 600,
            color: urgent ? '#0A0C12' : '#EAF2F8' }}>{data.message}</span>
        </div>
      </div>

      {/* pie: frescura */}
      <div style={{ textAlign: 'center', fontSize: 14, color: dim, fontVariantNumeric: 'tabular-nums' }}>
        {data.updated_text}
      </div>
    </div>
  )
}
