// BriefSheet.jsx — Brief diario: resumen retrospectivo del día (SIN predicción).
// Carga GET /api/copilot/brief: métricas calculadas de hoy + narrativa que el
// copiloto escribe SOLO para explicar y acompañar (nunca recomienda ni predice).
// Se renderiza con portal a document.body para quedar por encima de la nav.
import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { apiGet } from '../api.js'
import { PAL, SANS } from '../theme.js'
import { useLang } from '../i18n.jsx'
import NebulaGuide from './NebulaGuide.jsx'
import { useSheetClose, backdropAnim, sheetAnim, Loading } from './ui.jsx'

function fmtDate(dateStr, lang = 'es') {
  if (!dateStr) return ''
  try {
    const d = new Date(dateStr + 'T00:00:00')
    const s = d.toLocaleDateString(lang, { weekday: 'long', day: 'numeric', month: 'long' })
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
  const { t, lang, gVal, gDelta, gUnit } = useLang()
  const [data, setData] = useState(null)
  const [err, setErr] = useState(false)
  const [typed, setTyped] = useState(false)   // narrativa terminó de "escribirse"
  const [closing, requestClose] = useSheetClose(onClose)

  useEffect(() => {
    let alive = true
    apiGet('/brief').then(d => { if (alive) setData(d) }).catch(() => { if (alive) setErr(true) })
    return () => { alive = false }
  }, [])

  const s = data && data.stats
  const tiles = []
  if (s) {
    // la noche primero — es lo que más importa a la mañana
    if (s.overnight)
      tiles.push(<Tile key="night" theme={theme} label="Noche · 00–08h" value={s.overnight.tir} unit="%" color={PAL.ritmo.key}
        sub={`mín ${gVal(s.overnight.min)}${s.overnight.low_events ? ` · ${s.overnight.low_events} baja` : ' · sin bajas'}`}/>)
    if (s.readings_n) {
      tiles.push(<Tile key="tir" theme={theme} label="Tiempo en rango" value={s.tir} unit="%" color={PAL.insulina.key}
        sub={s.tir_ayer != null ? `ayer ${s.tir_ayer}% · ${s.tir - s.tir_ayer >= 0 ? '+' : ''}${s.tir - s.tir_ayer}` : null}/>)
      tiles.push(<Tile key="avg" theme={theme} label="Glucosa promedio" value={gVal(s.avg)} unit={gUnit}/>)
      if (s.min && s.max)
        tiles.push(<Tile key="rng" theme={theme} label="Rango de hoy" value={`${gVal(s.min.v)}–${gVal(s.max.v)}`} unit={gUnit}
          sub={`mín ${s.min.time} · máx ${s.max.time}`}/>)
      if (s.low_pct || s.high_pct)
        tiles.push(<Tile key="oor" theme={theme} label="Fuera de rango" value={s.low_pct + s.high_pct} unit="%"
          color={s.low_pct >= 4 ? '#E0B057' : '#D98A6A'} sub={`${s.low_pct}% bajo · ${s.high_pct}% alto`}/>)
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
    <div onClick={requestClose} style={{ position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(0,0,0,0.5)',
      display: 'flex', alignItems: 'flex-end', justifyContent: 'center', animation: backdropAnim(closing) }}>
      <div onClick={e => e.stopPropagation()} style={{ width: '100%', maxWidth: 460, background: theme.dark ? '#0E1426' : '#fff',
        borderTopLeftRadius: 26, borderTopRightRadius: 26, padding: '22px 22px calc(28px + env(safe-area-inset-bottom))',
        animation: sheetAnim(closing), maxHeight: '88%', overflowY: 'auto' }}>
        <div style={{ width: 38, height: 4, borderRadius: 2, background: theme.border, margin: '0 auto 18px' }}/>

        {!data && !err && <div style={{ padding: '34px 0' }}><Loading theme={theme} label={t('brief.preparing')}/></div>}
        {err && <div style={{ padding: '30px 0', textAlign: 'center', color: theme.inkSoft, fontSize: 14, fontFamily: SANS }}>{t('brief.loadError')}</div>}

        {data && (
          <div style={{ fontFamily: SANS }}>
            {/* encabezado — saludo + fecha */}
            <div style={{ marginBottom: 16 }}>
              <div style={{ color: theme.ink, fontSize: 22, fontWeight: 500, letterSpacing: '-0.01em' }}>{data.greeting}</div>
              <div style={{ color: theme.inkFaint, fontSize: 13, marginTop: 3 }}>{fmtDate(data.date, lang)}</div>
            </div>

            {/* narrativa del copiloto — aparece como si se escribiera, tranquila */}
            <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start', padding: '14px 0 18px' }}>
              <div style={{ flexShrink: 0, marginTop: -2 }}><NebulaGuide kind="pancreas" size={44} light={!theme.dark}/></div>
              <Typewriter text={data.narrative} onDone={() => setTyped(true)}
                style={{ margin: 0, color: theme.ink, fontSize: 15.5, lineHeight: 1.55 }}/>
            </div>

            {/* métricas de hoy — se revelan suave cuando termina de escribir */}
            {tiles.length > 0 ? (
              <div className={typed ? 'rise-in' : ''} style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 4,
                opacity: typed ? 1 : 0, transition: 'opacity 0.4s ease' }}>{tiles}</div>
            ) : (
              <div style={{ color: theme.inkSoft, fontSize: 13.5, lineHeight: 1.5, padding: '4px 2px 8px' }}>
                {t('brief.empty')}
              </div>
            )}

            {/* cómo respondió tu glucosa a las comidas de hoy (retrospectivo) */}
            {s && s.meal_responses && s.meal_responses.length > 0 && (
              <div style={{ marginTop: 16 }}>
                <div style={{ color: theme.inkFaint, fontSize: 11, letterSpacing: '0.14em',
                  textTransform: 'uppercase', marginBottom: 6 }}>{t('brief.yourMeals')}</div>
                {s.meal_responses.map((mr, i) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10,
                    padding: '10px 0', borderTop: `0.5px solid ${theme.border}` }}>
                    <span style={{ flex: 1, color: theme.ink, fontSize: 14, whiteSpace: 'nowrap',
                      overflow: 'hidden', textOverflow: 'ellipsis' }}>{mr.name}</span>
                    <span style={{ color: theme.inkFaint, fontSize: 12 }}>{mr.time} · {mr.carbs}g</span>
                    <span style={{ fontSize: 13, fontWeight: 600, fontVariantNumeric: 'tabular-nums',
                      color: mr.delta_2h > 60 ? '#D98A6A' : mr.delta_2h < -25 ? '#E0B057' : '#5FC6A8' }}>
                      {gDelta(mr.delta_2h)} <span style={{ fontWeight: 400, fontSize: 11, color: theme.inkFaint }}>2h</span>
                    </span>
                  </div>
                ))}
              </div>
            )}

            <div style={{ color: theme.inkFaint, fontSize: 11.5, lineHeight: 1.5, marginTop: 18 }}>
              {t('brief.disclaimer')}
            </div>
          </div>
        )}
      </div>
    </div>
  ), document.body)
}

// Revela el texto como si se escribiera (calmo). Respeta prefers-reduced-motion.
function Typewriter({ text, style, onDone }) {
  const [n, setN] = useState(0)
  useEffect(() => {
    if (!text) { onDone && onDone(); return }
    let reduce = false
    try { reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches } catch {}
    if (reduce) { setN(text.length); onDone && onDone(); return }
    setN(0)
    // ritmo tranquilo: ~2.6s en total, sin bajar de 10ms/carácter
    const step = Math.max(10, Math.round(2600 / text.length))
    let i = 0
    const id = setInterval(() => {
      i += 1
      setN(i)
      if (i >= text.length) { clearInterval(id); onDone && onDone() }
    }, step)
    return () => clearInterval(id)
  }, [text])
  const done = !text || n >= text.length
  return (
    <p style={style}>
      {text ? text.slice(0, n) : ''}
      {!done && <span className="tw-caret" aria-hidden="true">▍</span>}
    </p>
  )
}
