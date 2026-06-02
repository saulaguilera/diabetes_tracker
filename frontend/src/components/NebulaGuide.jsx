// NebulaGuide.jsx — guías de Orbit (rediseño limpio y centrado).
// Base común: núcleo luminoso + anillo de órbita con estrellas simétricas
// (centradas en 100,100 → no más partículas descuadradas).
// Motivo distinto por guía: equilibrio / energía / clave / ciclo / transformación.
import { useMemo } from 'react'
import { PAL } from '../theme.js'

const SEED = { pancreas: 4, glucosa: 11, insulina: 7, ritmo: 2, metabolismo: 19 }
// inclinación del anillo de órbita por guía (grados)
const TILT = { pancreas: -18, glucosa: 14, insulina: -24, ritmo: 8, metabolismo: -12 }

export default function NebulaGuide({ kind = 'pancreas', size = 150, breathe = true, light = false }) {
  const c = PAL[kind]
  const id = 'g_' + kind
  const cls = breathe
    ? (kind === 'ritmo' || kind === 'pancreas' ? 'breathe-slow' : kind === 'glucosa' ? 'breathe-fast' : 'breathe')
    : 'guide-float'
  const tilt = (TILT[kind] || -18)

  // Estrellas repartidas uniformemente sobre una elipse inclinada, centradas en (100,100).
  const orbit = useMemo(() => {
    const n = 7, rx = 82, ry = 28
    const th = tilt * Math.PI / 180, ct = Math.cos(th), st = Math.sin(th)
    let s = (SEED[kind] || 1) * 1777
    const rnd = () => { s = (s * 9301 + 49297) % 233280; return s / 233280 }
    const off = rnd() * Math.PI * 2
    return Array.from({ length: n }, (_, i) => {
      const a = off + (i / n) * Math.PI * 2
      const px = rx * Math.cos(a), py = ry * Math.sin(a)
      return { x: 100 + px * ct - py * st, y: 100 + px * st + py * ct, r: 0.8 + (i % 3) * 0.5 }
    })
  }, [kind, tilt])

  return (
    <svg width={size} height={size} viewBox="0 0 200 200" className={cls} style={{ overflow: 'visible' }}>
      <defs>
        <radialGradient id={`${id}core`} cx="50%" cy="42%" r="56%">
          <stop offset="0%" stopColor="#FFFFFF" stopOpacity="0.95"/>
          <stop offset="30%" stopColor={c.key} stopOpacity="0.90"/>
          <stop offset="70%" stopColor={c.mid} stopOpacity="0.48"/>
          <stop offset="100%" stopColor={c.deep} stopOpacity="0"/>
        </radialGradient>
        <radialGradient id={`${id}halo`} cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor={c.key} stopOpacity="0.34"/>
          <stop offset="100%" stopColor={c.key} stopOpacity="0"/>
        </radialGradient>
        <filter id={`${id}soft`} x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="2.4"/></filter>
        <filter id={`${id}blur`} x="-90%" y="-90%" width="280%" height="280%"><feGaussianBlur stdDeviation="12"/></filter>
      </defs>

      {/* halo ambiental centrado */}
      {light && <circle cx="100" cy="100" r="70" fill={`rgba(${c.rgb},0.10)`}/>}
      <circle cx="100" cy="100" r="74" fill={`url(#${id}halo)`} filter={`url(#${id}blur)`} className="halo-pulse"/>

      {/* anillo de órbita (centrado, inclinado) */}
      <ellipse cx="100" cy="100" rx="82" ry="28" fill="none" stroke={c.key} strokeWidth="0.7"
        opacity="0.18" transform={`rotate(${tilt} 100 100)`}/>

      {/* estrellas que orbitan — centradas, giran alrededor de 100,100 */}
      <g className="guide-stars">
        {orbit.map((d, i) => (
          <g key={i}>
            <circle cx={d.x} cy={d.y} r={d.r + 1.3} fill={c.key} opacity="0.22" filter={`url(#${id}soft)`}/>
            <circle cx={d.x} cy={d.y} r={d.r} fill={light ? c.key : '#FFFFFF'} opacity={light ? 0.7 : 0.85}
              className="guide-twinkle" style={{ animationDelay: `${(i * 0.5).toFixed(1)}s` }}/>
          </g>
        ))}
      </g>

      {/* núcleo + motivo por guía (centrado / simétrico) */}
      {kind === 'pancreas' && (
        <g>
          {/* El equilibrio: núcleo + dos satélites simétricos */}
          <circle cx="100" cy="100" r="33" fill={`url(#${id}core)`} filter={`url(#${id}soft)`}/>
          <circle cx="70" cy="100" r="7.5" fill={`url(#${id}core)`} filter={`url(#${id}soft)`} opacity="0.82"/>
          <circle cx="130" cy="100" r="7.5" fill={`url(#${id}core)`} filter={`url(#${id}soft)`} opacity="0.82"/>
        </g>
      )}
      {kind === 'glucosa' && (
        <g>
          {/* La energía: gota + burbujas que ascienden, centradas */}
          <ellipse cx="100" cy="110" rx="30" ry="32" fill={`url(#${id}core)`} filter={`url(#${id}soft)`}/>
          {[[100, 64, 7], [83, 80, 4.5], [117, 78, 5.5]].map(([x, y, r], i) => (
            <circle key={i} cx={x} cy={y} r={r} fill={`url(#${id}core)`} filter={`url(#${id}soft)`} opacity="0.85"/>
          ))}
        </g>
      )}
      {kind === 'insulina' && (
        <g>
          {/* La clave: destello de cuatro puntas (precisión) sobre el núcleo */}
          <circle cx="100" cy="100" r="28" fill={`url(#${id}core)`} filter={`url(#${id}soft)`}/>
          <path d="M100,56 L107,93 L144,100 L107,107 L100,144 L93,107 L56,100 L93,93 Z"
            fill={`url(#${id}core)`} filter={`url(#${id}soft)`} opacity="0.92"/>
        </g>
      )}
      {kind === 'ritmo' && (
        <g>
          {/* El ciclo: luna creciente */}
          <circle cx="100" cy="100" r="34" fill={`url(#${id}core)`} filter={`url(#${id}soft)`}/>
          <circle cx="114" cy="91" r="30" fill={light ? c.mid : '#0B1324'} opacity={light ? 0.6 : 0.92}/>
        </g>
      )}
      {kind === 'metabolismo' && (
        <g>
          {/* La transformación: pétalo/llama ascendente + núcleo */}
          <path d="M100,62 C122,86 122,116 100,140 C78,116 78,86 100,62 Z"
            fill={`url(#${id}core)`} filter={`url(#${id}soft)`}/>
          <ellipse cx="100" cy="110" rx="13" ry="19" fill={`url(#${id}core)`} opacity="0.7"/>
        </g>
      )}
    </svg>
  )
}
