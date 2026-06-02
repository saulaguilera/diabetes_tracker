// GlucoseWave.jsx — onda de glucosa de 24h (solo lecturas, sin predicción).
// Banda objetivo 70–180, área con gradiente y línea luminosa. Calmo, minimalista.
import { PAL } from '../theme.js'

export default function GlucoseWave({ series, theme, low = 70, high = 180, w = 320, h = 150 }) {
  if (!series || series.length < 2) {
    return (
      <div style={{ height: h, display: 'grid', placeItems: 'center', color: theme.inkFaint, fontSize: 13 }}>
        Sin datos suficientes para la onda.
      </div>
    )
  }
  const c = PAL.glucosa.key
  const vals = series.map(p => p.v)
  const lo = Math.min(low - 15, ...vals)
  const hi = Math.max(high + 15, ...vals)
  const rng = Math.max(1, hi - lo)
  const X = i => (i / (series.length - 1)) * w
  const Y = v => h - ((v - lo) / rng) * h
  const pts = series.map((p, i) => [X(i), Y(p.v)])
  const line = pts.map((p, i) => `${i ? 'L' : 'M'} ${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ')
  const area = `${line} L ${w},${h} L 0,${h} Z`
  const yLow = Y(low), yHigh = Y(high)
  const last = pts[pts.length - 1]

  return (
    <svg width="100%" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ display: 'block', overflow: 'visible' }}>
      <defs>
        <linearGradient id="gwArea" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={c} stopOpacity="0.28"/>
          <stop offset="100%" stopColor={c} stopOpacity="0"/>
        </linearGradient>
        <filter id="gwGlow" x="-10%" y="-40%" width="120%" height="200%"><feGaussianBlur stdDeviation="2.2"/></filter>
      </defs>
      {/* banda objetivo 70–180 */}
      <rect x="0" y={yHigh} width={w} height={Math.max(0, yLow - yHigh)} fill={theme.dark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.04)'}/>
      <line x1="0" y1={yHigh} x2={w} y2={yHigh} stroke={theme.inkFaint} strokeWidth="0.5" strokeDasharray="1 7" opacity="0.5"/>
      <line x1="0" y1={yLow} x2={w} y2={yLow} stroke={theme.inkFaint} strokeWidth="0.5" strokeDasharray="1 7" opacity="0.5"/>
      {/* área + línea */}
      <path d={area} fill="url(#gwArea)"/>
      <path d={line} fill="none" stroke={c} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" opacity="0.4" filter="url(#gwGlow)"/>
      <path d={line} fill="none" stroke={c} strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
      {/* punto "ahora" */}
      <circle cx={last[0]} cy={last[1]} r="3.4" fill="#FFFFFF"/>
      <circle cx={last[0]} cy={last[1]} r="6" fill="none" stroke={c} strokeWidth="1" opacity="0.6"/>
    </svg>
  )
}
