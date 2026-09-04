// GlucoseWave.jsx — onda de glucosa 24h: curva suave, sin recuadro, fundida con
// el fondo. Destello "ahora" animado + tooltip al arrastrar el dedo (valor/hora).
import { useRef, useState, useId } from 'react'
import { PAL } from '../theme.js'
import { CatIcon } from './EventSheet.jsx'

// color por categoría para los marcadores sobre la onda
const MARKER_COLOR = {
  comida: PAL.metabolismo.key, insulina: PAL.insulina.key,
  ejercicio: PAL.glucosa.key, contexto: PAL.ritmo.key,
}

function smoothPath(pts) {
  if (pts.length < 2) return ''
  let d = `M ${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}`
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || pts[i + 1]
    const c1x = p1[0] + (p2[0] - p0[0]) / 6, c1y = p1[1] + (p2[1] - p0[1]) / 6
    const c2x = p2[0] - (p3[0] - p1[0]) / 6, c2y = p2[1] - (p3[1] - p1[1]) / 6
    d += ` C ${c1x.toFixed(1)},${c1y.toFixed(1)} ${c2x.toFixed(1)},${c2y.toFixed(1)} ${p2[0].toFixed(1)},${p2[1].toFixed(1)}`
  }
  return d
}

function hhmm(t) {
  const d = new Date(t)
  if (isNaN(d.getTime())) return ''
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

export default function GlucoseWave({ series, markers = [], theme, low = 70, high = 180, w = 320, h = 150, live = true, unitLabel = 'mg/dL', fmtVal = (v) => Math.round(v), focusT = null, animateIn = false, showMarkers = false }) {
  const wrapRef = useRef(null)
  // la animación de entrada se decide AL MONTAR y no cambia: si el mensaje
  // streaming se swapea por el final a mitad del trazado, la curva termina
  // de dibujarse igual (nada de cortes ni re-animaciones)
  const [anim] = useState(animateIn)
  // ids únicos: varias ondas en la misma página (chat) no pueden compartir defs
  const uid = useId().replace(/:/g, '')
  const [active, setActive] = useState(null)

  if (!series || series.length < 2) {
    return <div style={{ height: h, display: 'grid', placeItems: 'center', color: theme.inkFaint, fontSize: 13 }}>Sin datos suficientes para la onda.</div>
  }

  const c = PAL.glucosa.key
  const vals = series.map(p => p.v)
  const lo = Math.min(low - 15, ...vals)
  const hi = Math.max(high + 15, ...vals)
  const rng = Math.max(1, hi - lo)
  const X = i => (i / (series.length - 1)) * w
  const Y = v => h - ((v - lo) / rng) * h
  const pts = series.map((p, i) => [X(i), Y(p.v)])
  const line = smoothPath(pts)
  const area = `${line} L ${w},${h} L 0,${h} Z`
  const yLow = Y(low), yHigh = Y(high)
  const last = pts[pts.length - 1]

  // foco: "cuando pasó eso" — banda anclada al instante del evento en el
  // MISMO eje que la curva (índice + interpolación local): con huecos de
  // sensor, la proporción temporal global marcaría el tramo equivocado
  let fx = null
  if (focusT && series.length > 1) {
    const ft = new Date(focusT).getTime()
    const ts = series.map(p => new Date(p.t).getTime())
    if (!isNaN(ft) && ft >= ts[0] && ft <= ts[ts.length - 1]) {
      let j = 0
      while (j < ts.length - 2 && ts[j + 1] < ft) j++
      const span = ts[j + 1] - ts[j]
      const frac = span > 0 ? Math.min(1, Math.max(0, (ft - ts[j]) / span)) : 0
      fx = ((j + frac) / (ts.length - 1)) * w
    }
  }

  const at = (clientX) => {
    const r = wrapRef.current.getBoundingClientRect()
    const frac = Math.max(0, Math.min(1, (clientX - r.left) / r.width))
    setActive(Math.round(frac * (series.length - 1)))
  }
  const ap = active != null ? pts[active] : null
  const av = active != null ? series[active] : null

  // marcadores de eventos: cada uno se ancla al punto de la serie más
  // cercano en el tiempo → la comida queda SOBRE la curva a la hora que fue
  const times = series.map(p => new Date(p.t).getTime())
  const marks = markers
    .map(mk => ({ ...mk, tm: new Date(mk.t).getTime() }))
    .filter(mk => !isNaN(mk.tm))
  // marcadores SOBRE la curva (chat): cada evento anclado al punto más cercano
  // en el tiempo; el del foco lleva un sonar (anillo que se expande)
  const anclados = showMarkers ? marks.map(mk => {
    let best = 0, dmin = Infinity
    for (let i = 0; i < times.length; i++) {
      const d = Math.abs(times[i] - mk.tm)
      if (d < dmin) { dmin = d; best = i }
    }
    return dmin <= 20 * 60000 ? { ...mk, i: best } : null
  }).filter(Boolean) : []
  const fT = focusT ? new Date(focusT).getTime() : NaN
  const focoMk = !isNaN(fT) && anclados.length
    ? anclados.reduce((a, b) => Math.abs(b.tm - fT) < Math.abs(a.tm - fT) ? b : a)
    : null
  const focoOk = focoMk && Math.abs(focoMk.tm - fT) <= 25 * 60000 ? focoMk : null
  // los eventos van apareciendo a medida que la curva los alcanza
  const popDelay = (x) => `${(0.15 + (x / w) * 2.6).toFixed(2)}s`

  // eventos cerca del punto tocado (±25 min) — los muestra la lupa
  const cercanos = av ? marks.filter(mk =>
    Math.abs(mk.tm - new Date(av.t).getTime()) <= 25 * 60000) : []

  return (
    <div ref={wrapRef} style={{ position: 'relative', touchAction: 'pan-y' }}
      onPointerDown={e => at(e.clientX)}
      onPointerMove={e => { if (e.pressure > 0 || e.buttons || e.pointerType === 'mouse') at(e.clientX) }}
      onPointerUp={() => setActive(null)}
      onPointerLeave={() => setActive(null)}>
      <svg width="100%" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ display: 'block', overflow: 'visible' }}>
        <defs>
          <linearGradient id={`gwArea${uid}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={c} stopOpacity="0.22"/>
            <stop offset="100%" stopColor={c} stopOpacity="0"/>
          </linearGradient>
          <filter id={`gwGlow${uid}`} x="-10%" y="-50%" width="120%" height="220%"><feGaussianBlur stdDeviation="2.4"/></filter>
        </defs>

        {/* guías 70/180 — hairlines tenues, sin recuadro */}
        <line className={anim ? 'wave-fade' : undefined} x1="0" y1={yHigh} x2={w} y2={yHigh} stroke={theme.inkFaint} strokeWidth="0.5" strokeDasharray="1 8" opacity="0.4"/>
        <line className={anim ? 'wave-fade' : undefined} x1="0" y1={yLow} x2={w} y2={yLow} stroke={theme.inkFaint} strokeWidth="0.5" strokeDasharray="1 8" opacity="0.4"/>

        {/* banda de foco: el momento del que habla el copiloto */}
        {fx != null && (
          <g className={anim ? 'foco-in' : undefined}>
            <rect className="foco-breathe" x={Math.max(0, fx - 14)} y="0" width="28" height={h}
              fill={c} opacity="0.08" rx="6"/>
            <line x1={fx} y1="0" x2={fx} y2={h} stroke={c} strokeWidth="0.8"
              strokeDasharray="2 5" opacity="0.55"/>
          </g>
        )}

        {/* área + línea (se funde con el fondo) */}
        <path className={anim ? 'wave-area-in' : undefined} d={area} fill={`url(#gwArea${uid})`}/>
        <path className={anim ? 'wave-draw-glow' : 'wave-breathe'} pathLength="1" d={line} fill="none" stroke={c} strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" opacity="0.35" filter={`url(#gwGlow${uid})`}/>
        <path className={anim ? 'wave-draw' : undefined} pathLength="1" d={line} fill="none" stroke={c} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>

        {/* eventos sobre la curva + sonar en el foco */}
        {anclados.map((mk, k) => {
          const [mx, my] = pts[mk.i]
          const col = MARKER_COLOR[mk.cat] || c
          const esFoco = focoOk && mk === focoOk
          return (
            <g key={k}>
              {esFoco && (
                <>
                  <circle className="sonar" cx={mx} cy={my} r="4" fill="none" stroke={col}
                    strokeWidth="1.2" style={{ animationDelay: anim ? '3s' : '0s' }}/>
                  <circle className="sonar" cx={mx} cy={my} r="4" fill="none" stroke={col}
                    strokeWidth="1.2" style={{ animationDelay: anim ? '4.5s' : '1.5s' }}/>
                </>
              )}
              <circle className={anim ? 'mk-pop' : undefined} cx={mx} cy={my}
                r={esFoco ? 4.6 : 3.4} fill={col} stroke="#FFFFFF" strokeWidth="1.3"
                style={anim ? { animationDelay: popDelay(mx) } : undefined}/>
            </g>
          )
        })}

        {/* guía vertical + punto al arrastrar */}
        {ap && (
          <g>
            <line x1={ap[0]} y1="0" x2={ap[0]} y2={h} stroke={c} strokeWidth="0.8" opacity="0.4"/>
            <circle cx={ap[0]} cy={ap[1]} r="4" fill="#FFFFFF"/>
            <circle cx={ap[0]} cy={ap[1]} r="7" fill="none" stroke={c} strokeWidth="1.2"/>
          </g>
        )}

        {/* destello "ahora" — pulso animado */}
        {!ap && live && (
          <g>
            <circle cx={last[0]} cy={last[1]} r="4" fill={c} className="now-pulse"/>
            <circle cx={last[0]} cy={last[1]} r="3.4" fill="#FFFFFF"/>
            <circle cx={last[0]} cy={last[1]} r="6" fill="none" stroke={c} strokeWidth="1" opacity="0.6"/>
          </g>
        )}
      </svg>

      {/* lupa al mantener el dedo: lente que amplía el tramo + qué pasó ahí */}
      {av && ap && (
        <div style={{
          position: 'absolute', top: -10,
          left: `${Math.min(86, Math.max(14, (active / (series.length - 1)) * 100))}%`,
          transform: 'translate(-50%, -100%)', pointerEvents: 'none',
          display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6,
        }}>
          {/* lente circular: el mismo trazo, ampliado ~2.3x alrededor del punto */}
          <div style={{ width: 92, height: 92, borderRadius: '50%', overflow: 'hidden',
            background: theme.dark ? 'rgba(10,12,30,0.96)' : 'rgba(255,255,255,0.97)',
            border: `1.5px solid ${theme.borderStrong}`,
            boxShadow: '0 10px 30px rgba(0,0,0,0.5), inset 0 0 18px rgba(34,211,238,0.06)' }}>
            <svg width="92" height="92"
              viewBox={`${ap[0] - 20} ${ap[1] - 20} 40 40`} preserveAspectRatio="xMidYMid slice">
              <line x1={ap[0]} y1={ap[1] - 20} x2={ap[0]} y2={ap[1] + 20}
                stroke={c} strokeWidth="0.4" opacity="0.35"/>
              <path d={line} fill="none" stroke={c} strokeWidth="1.1"
                strokeLinecap="round" strokeLinejoin="round"/>
              <circle cx={ap[0]} cy={ap[1]} r="2.6" fill="#FFFFFF"/>
              <circle cx={ap[0]} cy={ap[1]} r="4.2" fill="none" stroke={c} strokeWidth="0.8"/>
            </svg>
          </div>
          {/* valor + hora */}
          <div style={{ whiteSpace: 'nowrap', textAlign: 'center',
            background: theme.dark ? 'rgba(12,14,34,0.92)' : 'rgba(255,255,255,0.95)',
            border: `0.5px solid ${theme.borderStrong}`, borderRadius: 12, padding: '5px 10px',
            boxShadow: '0 8px 24px rgba(0,0,0,0.4)' }}>
            <span style={{ fontSize: 16, fontWeight: 500, color: theme.ink, fontVariantNumeric: 'tabular-nums' }}>{fmtVal(av.v)}</span>
            <span style={{ fontSize: 11, color: theme.inkSoft, marginLeft: 4 }}>{unitLabel}</span>
            {hhmm(av.t) && <span style={{ fontSize: 11, color: theme.inkFaint, marginLeft: 8 }}>{hhmm(av.t)}</span>}
            {/* qué pasó en ese momento (±25 min): comida, bolo, ejercicio… */}
            {cercanos.slice(0, 3).map((mk, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 5 }}>
                <CatIcon cat={mk.cat} color={MARKER_COLOR[mk.cat] || c} size={16}/>
                <span style={{ fontSize: 11.5, color: theme.ink, maxWidth: 130,
                  overflow: 'hidden', textOverflow: 'ellipsis' }}>{mk.title || mk.cat}</span>
                {mk.badge && <span style={{ fontSize: 11, fontWeight: 600,
                  color: MARKER_COLOR[mk.cat] || c }}>{mk.badge}</span>}
                <span style={{ fontSize: 10.5, color: theme.inkFaint }}>{hhmm(mk.t)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
