// BriefSheet.jsx — Brief diario: resumen retrospectivo del día (SIN predicción).
// Carga GET /api/copilot/brief: métricas calculadas de hoy + narrativa que el
// copiloto escribe SOLO para explicar y acompañar (nunca recomienda ni predice).
// Se renderiza con portal a document.body para quedar por encima de la nav.
import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { apiGet } from '../api.js'
import { PAL, SANS } from '../theme.js'
import NebulaGuide from './NebulaGuide.jsx'

function fmtDate(dateStr) {
  if (!dateStr) return ''
  try {
    const d = new Date(dateStr + 'T00:00:00')
    const s = d.toLocaleDateString('es', { weekday: 'long', day: 'numeric', month: 'long' })
    return s.charAt(0).toUpperCase() + s.slice(1)
  } catch { return '' }
}

function Tile({ theme, label, value, unit, color, sub }) {
  return (
    <div style={{ flex: '1 1 calc(50% - 5px)', minWidth: 0, background: theme.surface,
      border: `0.5px solid ${theme.border}`, borderRadius: 16, padding: '14px 16px' }}>
      <div style={{ color: theme.inkFaint, fontSize: 11 }}>{label}</div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 4, marginTop: 6 }}>
        <span style={{ fontSize: 26, fontWeight: 300, color: color || theme.ink,
          letterSpacing: '-0.02em', fontVariantNumeric: 'tabular-nums', lineHeight: 1 }}>{value}</span>
        {unit && <span style={{ fontSize: 12, color: theme.inkSoft }}>{unit}</span>}
      </div>
      {sub && <div style={{ color: theme.inkFaint, fontSize: 11, marginTop: 5 }}>{sub}</div>}
    </div>
  )
}

export default function BriefSheet({ theme, onClose }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(false)

  useEffect(() => {
    let alive = true
    apiGet('/brief').then(d => { if (alive) setData(d) }).catch(() => { if (alive) setErr(true) })
    return () => { alive = false }
  }, [])

  const s = data && data.stats
  const tiles = []
  if (s) {
    if (s.readings_n) {
      tiles.push(<Tile key="tir" theme={theme} label="Tiempo en rango" value={s.tir} unit="%" color={PAL.insulina.key}/>)
      tiles.push(<Tile key="avg" theme={theme} label="Glucosa promedio" value={s.avg} unit="mg/dL"/>)
      if (s.min && s.max)
        tiles.push(<Tile key="rng" theme={theme} label="Rango de hoy" value={`${s.min.v}–${s.max.v}`} unit="mg/dL"
          sub={`mín ${s.min.time} · máx ${s.max.time}`}/>)
      if (s.low_pct || s.high_pct)
        tiles.push(<Tile key="oor" theme={theme} label="Fuera de rango" value={`${s.low_pct}·${s.high_pct}`} unit="%"
          color={s.low_pct >= 4 ? '#E0B057' : '#D98A6A'} sub="bajo · alto"/>)
    }
    if (s.meals_n)
      tiles.push(<Tile key="ch" theme={theme} label="Carbohidratos" value={s.carbs_total} unit="g" color={PAL.metabolismo.key}
        sub={`${s.meals_n} comida${s.meals_n > 1 ? 's' : ''}`}/>)
    if (s.insulin_total)
      tiles.push(<Tile key="ins" theme={theme} label="Insulina" value={s.insulin_total} unit="U" color={PAL.insulina.key}
        sub={s.bolus_total && s.basal_total ? `${s.bolus_total} rápida · ${s.basal_total} basal` : null}/>)
    if (s.activity_min)
      tiles.push(<Tile key="act" theme={theme} label="Actividad" value={s.activity_min} unit="min" color={PAL.glucosa.key}
        sub={`${s.activity_n} sesión${s.activity_n > 1 ? 'es' : ''}`}/>)
  }

  return createPortal((
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(0,0,0,0.5)',
      display: 'flex', alignItems: 'flex-end', justifyContent: 'center' }}>
      <div onClick={e => e.stopPropagation()} style={{ width: '100%', maxWidth: 460, background: theme.dark ? '#0E1426' : '#fff',
        borderTopLeftRadius: 26, borderTopRightRadius: 26, padding: '22px 22px calc(28px + env(safe-area-inset-bottom))',
        animation: 'slideUp 0.32s cubic-bezier(.2,.8,.2,1)', maxHeight: '88%', overflowY: 'auto' }}>
        <div style={{ width: 38, height: 4, borderRadius: 2, background: theme.border, margin: '0 auto 18px' }}/>

        {!data && !err && <div style={{ padding: '30px 0', textAlign: 'center', color: theme.inkSoft, fontSize: 14, fontFamily: SANS }}>Preparando tu resumen…</div>}
        {err && <div style={{ padding: '30px 0', textAlign: 'center', color: theme.inkSoft, fontSize: 14, fontFamily: SANS }}>No se pudo cargar el resumen ahora.</div>}

        {data && (
          <div style={{ fontFamily: SANS }}>
            {/* encabezado — saludo + fecha */}
            <div style={{ marginBottom: 16 }}>
              <div style={{ color: theme.ink, fontSize: 22, fontWeight: 500, letterSpacing: '-0.01em' }}>{data.greeting}</div>
              <div style={{ color: theme.inkFaint, fontSize: 13, marginTop: 3 }}>{fmtDate(data.date)}</div>
            </div>

            {/* narrativa del copiloto */}
            <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start', padding: '14px 0 18px' }}>
              <div style={{ flexShrink: 0, marginTop: -2 }}><NebulaGuide kind="pancreas" size={44} light={!theme.dark}/></div>
              <p style={{ margin: 0, color: theme.ink, fontSize: 15.5, lineHeight: 1.55 }}>{data.narrative}</p>
            </div>

            {/* métricas de hoy */}
            {tiles.length > 0 ? (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 4 }}>{tiles}</div>
            ) : (
              <div style={{ color: theme.inkSoft, fontSize: 13.5, lineHeight: 1.5, padding: '4px 2px 8px' }}>
                Todavía no registraste nada hoy. Cuando agregues lecturas o eventos, los vas a ver acá.
              </div>
            )}

            <div style={{ color: theme.inkFaint, fontSize: 11.5, lineHeight: 1.5, marginTop: 18 }}>
              Orbit solo describe y acompaña tus datos. No reemplaza a tu equipo médico.
            </div>
          </div>
        )}
      </div>
    </div>
  ), document.body)
}
