// GlucoseWave.jsx — onda de glucosa 24h: curva suave, sin recuadro, fundida con
// el fondo. Destello "ahora" animado + tooltip al arrastrar el dedo (valor/hora).
import { useRef, useState } from 'react'
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

export default function GlucoseWave({ series, markers = [], theme, low = 70, high = 180, w = 320, h = 150 }) {
  const wrapRef = useRef(null)
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
  const marks = markers.map(mk => {
    const tm = new Date(mk.t).getTime()
    if (isNaN(tm)) return null
    // fuera del rango de la serie (ej. sensor offline y comida "ahora"):
    // se ancla al extremo más cercano en vez de desaparecer en silencio
    const tc = Math.max(times[0], Math.min(tm, times[times.length - 1]))
    let best = 0, dist = Infinity
    for (let i = 0; i < times.length; i++) {
      const d = Math.abs(times[i] - tc)
      if (d < dist) { dist = d; best = i }
    }
    return { cat: mk.cat, xPct: (best / (series.length - 1)) * 100 }
  }).filter(Boolean)
  // pares pegados (comida + bolo a los 5 min = el caso más común): pequeño
  // corrimiento horizontal en el carril para que ambos se vean
  marks.sort((a, b) => a.xPct - b.xPct)
  for (let i = 0; i < marks.length; i++) {
    marks[i].nudge = (i > 0 && marks[i].xPct - marks[i - 1].xPct < 4.5)
      ? (marks[i - 1].nudge + 1) : 0
  }

  return (
    <div ref={wrapRef} style={{ position: 'relative', touchAction: 'pan-y' }}
      onPointerDown={e => at(e.clientX)}
      onPointerMove={e => { if (e.pressure > 0 || e.buttons || e.pointerType === 'mouse') at(e.clientX) }}
      onPointerUp={() => setActive(null)}
      onPointerLeave={() => setActive(null)}>
      <svg width="100%" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ display: 'block', overflow: 'visible' }}>
        <defs>
          <linearGradient id="gwArea" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={c} stopOpacity="0.22"/>
            <stop offset="100%" stopColor={c} stopOpacity="0"/>
          </linearGradient>
          <filter id="gwGlow" x="-10%" y="-50%" width="120%" height="220%"><feGaussianBlur stdDeviation="2.4"/></filter>
        </defs>

        {/* guías 70/180 — hairlines tenues, sin recuadro */}
        <line x1="0" y1={yHigh} x2={w} y2={yHigh} stroke={theme.inkFaint} strokeWidth="0.5" strokeDasharray="1 8" opacity="0.4"/>
        <line x1="0" y1={yLow} x2={w} y2={yLow} stroke={theme.inkFaint} strokeWidth="0.5" strokeDasharray="1 8" opacity="0.4"/>

        {/* área + línea (se funde con el fondo) */}
        <path d={area} fill="url(#gwArea)"/>
        <path d={line} fill="none" stroke={c} strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" opacity="0.35" filter="url(#gwGlow)"/>
        <path d={line} fill="none" stroke={c} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>

        {/* hora de cada evento: hairline sutil del color de su categoría */}
        {marks.map((mk, i) => (
          <line key={`mk${i}`} x1={(mk.xPct / 100) * w} y1="0" x2={(mk.xPct / 100) * w} y2={h}
            stroke={MARKER_COLOR[mk.cat] || c} strokeWidth="0.7" opacity="0.22"/>
        ))}

        {/* guía vertical + punto al arrastrar */}
        {ap && (
          <g>
            <line x1={ap[0]} y1="0" x2={ap[0]} y2={h} stroke={c} strokeWidth="0.8" opacity="0.4"/>
            <circle cx={ap[0]} cy={ap[1]} r="4" fill="#FFFFFF"/>
            <circle cx={ap[0]} cy={ap[1]} r="7" fill="none" stroke={c} strokeWidth="1.2"/>
          </g>
        )}

        {/* destello "ahora" — pulso animado */}
        {!ap && (
          <g>
            <circle cx={last[0]} cy={last[1]} r="4" fill={c} className="now-pulse"/>
            <circle cx={last[0]} cy={last[1]} r="3.4" fill="#FFFFFF"/>
            <circle cx={last[0]} cy={last[1]} r="6" fill="none" stroke={c} strokeWidth="1" opacity="0.6"/>
          </g>
        )}
      </svg>

      {/* tooltip HTML al arrastrar */}
      {marks.length > 0 && (
        <div style={{ position: 'relative', height: 22, marginTop: 6 }}>
          {marks.map((mk, i) => (
            <span key={i} style={{ position: 'absolute', top: 0,
              left: `calc(${mk.xPct}% + ${mk.nudge * 15}px)`,
              transform: 'translateX(-50%)', pointerEvents: 'none' }}>
              <CatIcon cat={mk.cat} color={MARKER_COLOR[mk.cat] || PAL.glucosa.key} size={17}/>
            </span>
          ))}
        </div>
      )}

      {av && (
        <div style={{
          position: 'absolute', top: -4, left: `${(active / (series.length - 1)) * 100}%`,
          transform: 'translate(-50%, -100%)', pointerEvents: 'none', whiteSpace: 'nowrap',
          background: theme.dark ? 'rgba(12,14,34,0.92)' : 'rgba(255,255,255,0.95)',
          border: `0.5px solid ${theme.borderStrong}`, borderRadius: 12, padding: '6px 10px',
          boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
        }}>
          <span style={{ fontSize: 16, fontWeight: 500, color: theme.ink, fontVariantNumeric: 'tabular-nums' }}>{Math.round(av.v)}</span>
          <span style={{ fontSize: 11, color: theme.inkSoft, marginLeft: 4 }}>mg/dL</span>
          {hhmm(av.t) && <span style={{ fontSize: 11, color: theme.inkFaint, marginLeft: 8 }}>{hhmm(av.t)}</span>}
        </div>
      )}
    </div>
  )
}
