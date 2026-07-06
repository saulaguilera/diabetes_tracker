// Hoy.jsx — dashboard de estado presente (SIN predicciones).
// Consume GET /api/copilot/home. Reusa los cálculos del backend (IOB/COB/TIR).
import { useState, useEffect } from 'react'
import { apiGet } from '../api.js'
import { PAL, SANS } from '../theme.js'
import NebulaGuide from '../components/NebulaGuide.jsx'
import GlucoseWave from '../components/GlucoseWave.jsx'
import EventSheet from '../components/EventSheet.jsx'
import BriefSheet from '../components/BriefSheet.jsx'
import { Card, Eyebrow, Meter } from '../components/ui.jsx'

const STATUS = {
  hipo:  { dot: '#E0B057', label: 'Bajo' },
  hiper: { dot: '#D98A6A', label: 'Alto' },
  rango: { dot: '#5FC6A8', label: 'En rango' },
}
const CAT_COLOR = {
  comida: PAL.metabolismo.key,
  insulina: PAL.insulina.key,
  ejercicio: PAL.glucosa.key,
}

function Centered({ theme, children }) {
  return (
    <div style={{ minHeight: '60%', display: 'grid', placeItems: 'center', textAlign: 'center',
      padding: '0 32px', color: theme.inkSoft, fontSize: 14, fontFamily: SANS }}>{children}</div>
  )
}

function Metric({ theme, label, value, unit, color }) {
  return (
    <div style={{ flex: 1 }}>
      <div style={{ color: theme.inkFaint, fontSize: 11 }}>{label}</div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 4, marginTop: 5 }}>
        <span style={{ fontSize: 23, fontWeight: 300, color, letterSpacing: '-0.02em',
          fontVariantNumeric: 'tabular-nums' }}>{value}</span>
        <span style={{ fontSize: 12, color: theme.inkSoft }}>{unit}</span>
      </div>
    </div>
  )
}

export default function Hoy({ theme, refreshKey = 0, onGoRegistro }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [sel, setSel] = useState(null)     // evento abierto para editar/borrar
  const [reload, setReload] = useState(0)
  const [showBrief, setShowBrief] = useState(false)
  // recordatorio de basal: descartable por hoy (persistido en localStorage)
  const todayKey = new Date().toISOString().slice(0, 10)
  const [basalDismissed, setBasalDismissed] = useState(() => {
    try { return localStorage.getItem('orbit_basal_dismiss') === todayKey } catch { return false }
  })
  const dismissBasal = () => {
    setBasalDismissed(true)
    try { localStorage.setItem('orbit_basal_dismiss', todayKey) } catch {}
  }

  useEffect(() => {
    let alive = true
    apiGet('/home')
      .then(d => { if (alive) setData(d) })
      .catch(e => { if (alive) setErr(e.message) })
    return () => { alive = false }
  }, [refreshKey, reload])

  if (err) return <Centered theme={theme}>No se pudieron cargar tus datos ahora.</Centered>
  if (!data) return <Centered theme={theme}>Cargando…</Centered>

  const g = data.glucose
  const st = STATUS[g && g.status] || STATUS.rango
  const div = <div style={{ width: '0.5px', background: theme.border }}/>

  const hour = new Date().getHours()
  const hello = hour < 12 ? 'Buenos días' : hour < 20 ? 'Buenas tardes' : 'Buenas noches'
  const briefTeaser = data.tir_today != null ? `${hello} · ${data.tir_today}% en rango hoy` : 'Tu resumen del día'

  // recordatorio amable: basal de hoy sin registrar, pasada tu hora habitual
  const basal = data.context && data.context.basal
  const showBasalReminder = basal && !basal.logged_today && basal.expected_units != null
    && basal.hora != null && hour >= basal.hora && !basalDismissed

  return (
    <div style={{ padding: '4px 22px 120px', fontFamily: SANS, display: 'flex', flexDirection: 'column', gap: 22 }}>
      {/* recordatorio de basal — amable, descartable por hoy, jamás alarmista */}
      {showBasalReminder && (
        <Card theme={theme} className="rise-in" style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '14px 16px',
          border: '0.5px solid rgba(224,176,87,0.4)' }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
            background: '#E0B057', boxShadow: '0 0 10px #E0B057' }}/>
          <div style={{ flex: 1 }}>
            <div style={{ color: theme.ink, fontSize: 14, lineHeight: 1.4 }}>
              Tu basal de hoy todavía no está registrada
            </div>
            <div style={{ color: theme.inkFaint, fontSize: 12, marginTop: 2 }}>
              Solés aplicarla a las {basal.hora}:00{basal.tipo ? ` (${basal.tipo} ${basal.expected_units}U)` : ''}
            </div>
          </div>
          {onGoRegistro && (
            <button onClick={onGoRegistro} style={{ padding: '8px 13px', borderRadius: 100, border: 'none', cursor: 'pointer',
              background: 'rgba(224,176,87,0.16)', color: '#E0B057', fontSize: 12.5, fontWeight: 600, fontFamily: SANS }}>
              Registrar
            </button>
          )}
          <button onClick={dismissBasal} aria-label="Descartar por hoy" style={{ background: 'none', border: 'none',
            color: theme.inkFaint, fontSize: 16, cursor: 'pointer', padding: '4px 2px', fontFamily: SANS }}>✕</button>
        </Card>
      )}
      {/* hero — glucosa actual */}
      <div>
        <div style={{ color: theme.inkSoft, fontSize: 14, marginBottom: 10 }}>Ahora</div>
        {g ? (
          <>
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: 12 }}>
              <span style={{ fontSize: 88, fontWeight: 200, letterSpacing: '-0.05em', color: theme.ink,
                lineHeight: 0.82, fontVariantNumeric: 'tabular-nums' }}>{g.value}</span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 8, color: theme.inkSoft, fontSize: 16, paddingBottom: 8 }}>
                mg/dL <span style={{ fontSize: 19, color: theme.inkFaint }}>{g.arrow}</span>
              </span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 16 }}>
              <span style={{ width: 7, height: 7, borderRadius: '50%', background: st.dot, boxShadow: `0 0 10px ${st.dot}` }}/>
              <span style={{ color: theme.inkSoft, fontSize: 15 }}>{st.label}</span>
              {g.age_min != null && <span style={{ color: theme.inkFaint, fontSize: 12 }}>· hace {g.age_min}m</span>}
            </div>
          </>
        ) : (
          <div style={{ color: theme.inkSoft, fontSize: 15 }}>Sin lecturas recientes.</div>
        )}
      </div>

      {/* onda de glucosa — últimas 24h (solo lecturas) */}
      {data.series && data.series.length > 1 && (
        <div>
          <GlucoseWave series={data.series} theme={theme}/>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8, padding: '0 2px' }}>
            {['00', '06', '12', '18', '24'].map(a => (
              <span key={a} style={{ fontSize: 10.5, letterSpacing: '0.1em', color: theme.inkFaint, fontVariantNumeric: 'tabular-nums' }}>{a}</span>
            ))}
          </div>
        </div>
      )}

      {/* contexto ahora — IOB / COB / tendencia + basal */}
      <Card theme={theme} style={{ padding: '16px 20px' }}>
        <Eyebrow theme={theme} style={{ fontSize: 10 }}>Contexto ahora</Eyebrow>
        <div style={{ display: 'flex', gap: 12, marginTop: 14 }}>
          <Metric theme={theme} label="Insulina activa" value={data.context.iob} unit="U" color={PAL.insulina.key}/>
          {div}
          <Metric theme={theme} label="Carbos activos" value={data.context.cob} unit="g" color={PAL.metabolismo.key}/>
          {div}
          <div style={{ flex: 1 }}>
            <div style={{ color: theme.inkFaint, fontSize: 11 }}>Tendencia</div>
            <div style={{ fontSize: 18, fontWeight: 400, color: theme.ink, marginTop: 7 }}>{data.context.trend}</div>
          </div>
        </div>
        {/* basal — antes no aparecía en el contexto */}
        {data.context.basal && data.context.basal.last_units != null && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 14,
            paddingTop: 12, borderTop: `0.5px solid ${theme.border}` }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
              background: data.context.basal.logged_today ? '#5FC6A8' : '#E0B057',
              boxShadow: `0 0 8px ${data.context.basal.logged_today ? '#5FC6A8' : '#E0B057'}` }}/>
            <span style={{ color: theme.inkSoft, fontSize: 12.5 }}>
              Basal{data.context.basal.tipo ? ` (${data.context.basal.tipo})` : ''} · {data.context.basal.last_units}U hace {data.context.basal.last_ago}
              {!data.context.basal.logged_today && ' · hoy sin registrar'}
            </span>
          </div>
        )}
      </Card>

      {/* tiempo en rango */}
      {data.tir_today != null && (
        <Card theme={theme} style={{ padding: '20px 22px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
            <span style={{ color: theme.inkSoft, fontSize: 14 }}>Tiempo en rango</span>
            <span style={{ color: theme.inkFaint, fontSize: 13 }}>Hoy · 24 h</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, margin: '14px 0 16px' }}>
            <span style={{ fontSize: 40, fontWeight: 300, color: theme.ink, letterSpacing: '-0.03em',
              lineHeight: 1, fontVariantNumeric: 'tabular-nums' }}>{data.tir_today}</span>
            <span style={{ fontSize: 18, color: theme.inkSoft }}>%</span>
          </div>
          <Meter pct={data.tir_today} color={PAL.insulina.key} track={theme.dark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.08)'}/>
        </Card>
      )}

      {/* actividad reciente */}
      {data.recent && data.recent.length > 0 && (
        <Card theme={theme} style={{ padding: '6px 18px 10px' }}>
          <Eyebrow theme={theme} style={{ fontSize: 10, padding: '12px 0 4px' }}>Actividad reciente</Eyebrow>
          {data.recent.map((e, i) => (
            <div key={i} onClick={() => e.id && setSel(e)} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '11px 0', borderTop: `0.5px solid ${theme.border}`, cursor: e.id ? 'pointer' : 'default' }}>
              <span style={{ width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
                background: CAT_COLOR[e.cat] || theme.accent, boxShadow: `0 0 8px ${CAT_COLOR[e.cat] || theme.accent}` }}/>
              <span style={{ flex: 1, color: theme.ink, fontSize: 14, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{e.title}</span>
              {e.badge && <span style={{ color: CAT_COLOR[e.cat] || theme.accent, fontSize: 13, fontWeight: 500 }}>{e.badge}</span>}
              <span style={{ color: theme.inkFaint, fontSize: 11, width: 42, textAlign: 'right' }}>{e.ago}</span>
            </div>
          ))}
        </Card>
      )}

      {/* brief diario — resumen del día (retrospectivo, sin predicción) */}
      <Card theme={theme} glow={PAL.ritmo.soft} onClick={() => setShowBrief(true)} style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '16px 18px' }}>
        <div style={{ marginLeft: -4, flexShrink: 0 }}><NebulaGuide kind="pancreas" size={52} light={!theme.dark}/></div>
        <div style={{ flex: 1 }}>
          <Eyebrow theme={theme} style={{ fontSize: 9.5 }}>Brief diario</Eyebrow>
          <div style={{ color: theme.ink, fontSize: 15, marginTop: 6, lineHeight: 1.4 }}>{briefTeaser}</div>
        </div>
        <span style={{ color: theme.inkFaint, fontSize: 20 }}>›</span>
      </Card>

      {sel && <EventSheet theme={theme} ev={sel} onClose={() => setSel(null)} onChanged={() => { setSel(null); setReload(r => r + 1) }}/>}
      {showBrief && <BriefSheet theme={theme} onClose={() => setShowBrief(false)}/>}
    </div>
  )
}
