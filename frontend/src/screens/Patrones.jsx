// Patrones.jsx — retrospectivo: TIR semanal, resumen y observaciones detectadas.
// Consume GET /api/copilot/patterns. Sin predicción.
import { useState, useEffect } from 'react'
import { apiGet } from '../api.js'
import { PAL, SANS } from '../theme.js'
import { Card, Eyebrow } from '../components/ui.jsx'

const NIVEL_COLOR = {
  danger: '#D98A6A', warning: '#E0B057', info: PAL.glucosa.key,
  favourable: '#5FC6A8', good: '#5FC6A8',
}
function tirColor(v) {
  if (v == null) return null
  return v >= 70 ? '#5FC6A8' : v >= 50 ? '#E0B057' : '#D98A6A'
}

function Centered({ theme, children }) {
  return <div style={{ minHeight: '60%', display: 'grid', placeItems: 'center', textAlign: 'center', padding: '0 32px', color: theme.inkSoft, fontSize: 14, fontFamily: SANS }}>{children}</div>
}

function WeeklyBars({ theme, weekly }) {
  const H = 92
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', gap: 8 }}>
      {weekly.values.map((v, i) => (
        <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 7 }}>
          <div style={{ width: '100%', maxWidth: 26, height: H, borderRadius: 8, overflow: 'hidden',
            background: theme.dark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.05)', display: 'flex', alignItems: 'flex-end' }}>
            {v != null && <div style={{ width: '100%', height: `${v}%`, background: tirColor(v), borderRadius: 8, transition: 'height 0.5s ease' }}/>}
          </div>
          <span style={{ fontSize: 11, color: theme.inkFaint }}>{weekly.labels[i]}</span>
          <span style={{ fontSize: 10, color: theme.inkFaint, fontVariantNumeric: 'tabular-nums' }}>{v != null ? `${v}%` : '—'}</span>
        </div>
      ))}
    </div>
  )
}

function Stat({ theme, label, value, unit, color }) {
  return (
    <div style={{ flex: 1 }}>
      <div style={{ color: theme.inkFaint, fontSize: 11 }}>{label}</div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 3, marginTop: 5 }}>
        <span style={{ fontSize: 22, fontWeight: 300, color: color || theme.ink, fontVariantNumeric: 'tabular-nums' }}>{value == null ? '—' : value}</span>
        {unit && value != null && <span style={{ fontSize: 12, color: theme.inkSoft }}>{unit}</span>}
      </div>
    </div>
  )
}

export default function Patrones({ theme, refreshKey = 0 }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    let alive = true
    setErr(null)
    apiGet('/patterns')
      .then(d => { if (alive) setData(d) })
      .catch(e => { if (alive) setErr(e.message) })
    return () => { alive = false }
  }, [refreshKey])

  if (err) return <Centered theme={theme}>No se pudieron cargar tus patrones ahora.</Centered>
  if (!data) return <Centered theme={theme}>Cargando…</Centered>

  const r = data.resumen || {}
  const div = <div style={{ width: '0.5px', background: theme.border }}/>

  return (
    <div style={{ padding: '4px 22px 120px', fontFamily: SANS, display: 'flex', flexDirection: 'column', gap: 20 }}>
      <Eyebrow theme={theme}>Patrones</Eyebrow>

      {/* TIR semanal */}
      <Card theme={theme} style={{ padding: '20px 20px 16px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 16 }}>
          <span style={{ color: theme.inkSoft, fontSize: 14 }}>Tiempo en rango</span>
          <span style={{ color: theme.inkFaint, fontSize: 13 }}>7 días</span>
        </div>
        <WeeklyBars theme={theme} weekly={data.weekly}/>
      </Card>

      {/* resumen */}
      <Card theme={theme} style={{ padding: '16px 20px' }}>
        <Eyebrow theme={theme} style={{ fontSize: 10 }}>Resumen · {r.days || 14} días</Eyebrow>
        <div style={{ display: 'flex', gap: 12, marginTop: 14 }}>
          <Stat theme={theme} label="Promedio" value={r.avg} unit="mg/dL"/>
          {div}
          <Stat theme={theme} label="Variabilidad" value={r.cv} unit="%" color={r.cv != null && r.cv > 36 ? '#E0B057' : theme.ink}/>
          {div}
          <Stat theme={theme} label="En rango" value={r.tir} unit="%" color={tirColor(r.tir)}/>
        </div>
      </Card>

      {/* observaciones detectadas */}
      <div>
        <Eyebrow theme={theme} style={{ marginBottom: 12 }}>Observaciones</Eyebrow>
        {(!data.patterns || data.patterns.length === 0) ? (
          <Card theme={theme} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#5FC6A8', boxShadow: '0 0 8px #5FC6A8' }}/>
            <span style={{ color: theme.inkSoft, fontSize: 14 }}>Sin patrones destacados — buena señal.</span>
          </Card>
        ) : data.patterns.map((p, i) => {
          const c = NIVEL_COLOR[p.nivel] || PAL.glucosa.key
          return (
            <Card key={i} theme={theme} style={{ marginBottom: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%', flexShrink: 0, background: c, boxShadow: `0 0 8px ${c}` }}/>
                <span style={{ color: theme.ink, fontSize: 15, fontWeight: 500 }}>{p.titulo}</span>
              </div>
              {p.detalle && <div style={{ color: theme.inkSoft, fontSize: 13, lineHeight: 1.5 }}>{p.detalle}</div>}
              {p.sugerencia && (
                <div style={{ color: theme.inkFaint, fontSize: 12.5, lineHeight: 1.5, marginTop: 10, paddingTop: 10, borderTop: `0.5px solid ${theme.border}` }}>
                  {p.sugerencia}
                </div>
              )}
            </Card>
          )
        })}
        <div style={{ color: theme.inkFaint, fontSize: 11, lineHeight: 1.5, marginTop: 4, textAlign: 'center' }}>
          Observaciones de tus datos. Conversá los ajustes con tu equipo médico.
        </div>
      </div>
    </div>
  )
}
