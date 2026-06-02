// Perfil.jsx — datos del usuario, sensor, terapia, conexión con el médico y tema.
import { useState, useEffect } from 'react'
import { apiGet } from '../api.js'
import { PAL, SANS } from '../theme.js'
import { Card, Eyebrow } from '../components/ui.jsx'

function Centered({ theme, children }) {
  return <div style={{ minHeight: '50%', display: 'grid', placeItems: 'center', textAlign: 'center', padding: '0 32px', color: theme.inkSoft, fontSize: 14, fontFamily: SANS }}>{children}</div>
}

function Row({ theme, label, value, color }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', padding: '11px 0', borderTop: `0.5px solid ${theme.border}` }}>
      <span style={{ color: theme.inkSoft, fontSize: 14 }}>{label}</span>
      <span style={{ color: color || theme.ink, fontSize: 14, fontWeight: 500, fontVariantNumeric: 'tabular-nums' }}>{value}</span>
    </div>
  )
}

function Toggle({ on, onChange, color }) {
  return (
    <div onClick={onChange} style={{
      width: 46, height: 27, borderRadius: 100, cursor: 'pointer', flexShrink: 0,
      background: on ? color : 'rgba(120,120,140,0.35)', transition: 'background 0.25s', position: 'relative' }}>
      <div style={{ position: 'absolute', top: 3, left: on ? 22 : 3, width: 21, height: 21, borderRadius: '50%',
        background: '#fff', transition: 'left 0.25s', boxShadow: '0 1px 3px rgba(0,0,0,0.3)' }}/>
    </div>
  )
}

export default function Perfil({ theme, refreshKey = 0, dark = true, onToggleTheme }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    let alive = true
    setErr(null)
    apiGet('/profile')
      .then(d => { if (alive) setData(d) })
      .catch(e => { if (alive) setErr(e.message) })
    return () => { alive = false }
  }, [refreshKey])

  if (err) return <Centered theme={theme}>No se pudo cargar tu perfil ahora.</Centered>
  if (!data) return <Centered theme={theme}>Cargando…</Centered>

  const s = data.sensor || {}
  const c = data.config || {}
  const fmt = (v, unit) => (v == null ? '—' : `${v}${unit || ''}`)
  const basalHora = c.basal_hora != null ? Math.round(parseFloat(c.basal_hora)) : null
  const basalTxt = c.basal_dose != null
    ? `${c.basal_dose}U${c.basal_tipo ? ' ' + c.basal_tipo : ''}${basalHora != null ? ` · ${basalHora}:00` : ''}`
    : '—'

  return (
    <div style={{ padding: '4px 22px 120px', fontFamily: SANS, display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div>
        <Eyebrow theme={theme}>Perfil</Eyebrow>
        {data.name && <div style={{ fontSize: 26, fontWeight: 300, color: theme.ink, marginTop: 8, letterSpacing: '-0.02em' }}>Hola, {data.name}</div>}
      </div>

      {/* sensor */}
      <Card theme={theme}>
        <Eyebrow theme={theme} style={{ fontSize: 10, marginBottom: 4 }}>Tu sensor</Eyebrow>
        <Row theme={theme} label="Última lectura" value={s.last_reading != null ? `${s.last_reading} mg/dL · ${s.last_reading_ago}` : '—'}/>
        <Row theme={theme} label="Sincronización" value={s.last_sync_ago || '—'}/>
        <Row theme={theme} label="Fuente" value={s.source === 'cgm_libre' ? 'FreeStyle Libre' : (s.source || '—')}/>
      </Card>

      {/* terapia */}
      <Card theme={theme}>
        <Eyebrow theme={theme} style={{ fontSize: 10, marginBottom: 4 }}>Tu terapia</Eyebrow>
        <Row theme={theme} label="Objetivo" value={fmt(c.objetivo, ' mg/dL')}/>
        <Row theme={theme} label="Sensibilidad (ISF)" value={c.isf != null ? `${c.isf} mg/dL/U` : 'auto'}/>
        <Row theme={theme} label="Ratio (ICR)" value={c.icr != null ? `${c.icr} g/U` : 'auto'}/>
        <Row theme={theme} label="Basal" value={basalTxt}/>
        <div style={{ color: theme.inkFaint, fontSize: 11.5, marginTop: 12, lineHeight: 1.5 }}>
          Estos valores se ajustan en la app principal. Acá solo se muestran.
        </div>
      </Card>

      {/* equipo médico */}
      <Card theme={theme} glow={PAL.glucosa.soft}>
        <Eyebrow theme={theme} style={{ fontSize: 10, marginBottom: 10 }}>Tu equipo médico</Eyebrow>
        <div style={{ color: theme.ink, fontSize: 15, marginBottom: 4 }}>Conectá con tu médico</div>
        <div style={{ color: theme.inkSoft, fontSize: 13, lineHeight: 1.5, marginBottom: 14 }}>
          Compartí tus reportes y patrones con tu equipo de forma segura.
        </div>
        <button disabled style={{
          width: '100%', padding: '12px', borderRadius: 14, border: `0.5px solid ${theme.border}`,
          background: theme.surface, color: theme.inkFaint, fontSize: 14, fontFamily: SANS, cursor: 'default' }}>
          Próximamente
        </button>
      </Card>

      {/* apariencia */}
      <Card theme={theme} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <Eyebrow theme={theme} style={{ fontSize: 10 }}>Apariencia</Eyebrow>
          <div style={{ color: theme.ink, fontSize: 15, marginTop: 6 }}>Tema oscuro</div>
        </div>
        <Toggle on={dark} onChange={onToggleTheme} color={theme.accent}/>
      </Card>

      <div style={{ color: theme.inkFaint, fontSize: 11, textAlign: 'center', lineHeight: 1.5 }}>
        Orbit · copiloto metabólico
      </div>
    </div>
  )
}
