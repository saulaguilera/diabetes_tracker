// CopilotAvatar.jsx — la cara del copiloto. MISMA forma que la guía de siempre
// (núcleo luminoso + destello de 4 puntas + anillo con estrellas que orbitan),
// pero pintada con la paleta de la marca: el glow va de blanco→cyan→violeta, y el
// destello lleva el degradado Orbit. Suave/nebulosa, no un planeta sólido.
// ids únicos por instancia (useId) → varias burbujas no colisionan.
import { useId, useMemo } from 'react'

export default function CopilotAvatar({ size = 34, animate = true }) {
  const raw = useId().replace(/[:]/g, '')
  const core = 'cc' + raw, spark = 'csp' + raw, halo = 'ch' + raw, ring = 'cr' + raw, soft = 'cs' + raw
  const tilt = -24

  // estrellas repartidas sobre el anillo inclinado (como la guía original)
  const orbit = useMemo(() => {
    const n = 7, rx = 82, ry = 28, th = tilt * Math.PI / 180, ct = Math.cos(th), st = Math.sin(th)
    let s = 7 * 1777
    const rnd = () => { s = (s * 9301 + 49297) % 233280; return s / 233280 }
    const off = rnd() * Math.PI * 2
    return Array.from({ length: n }, (_, i) => {
      const a = off + (i / n) * Math.PI * 2
      const px = rx * Math.cos(a), py = ry * Math.sin(a)
      return { x: 100 + px * ct - py * st, y: 100 + px * st + py * ct, r: 0.8 + (i % 3) * 0.5 }
    })
  }, [])

  return (
    <svg width={size} height={size} viewBox="0 0 200 200" className={animate ? 'breathe' : 'guide-float'} style={{ overflow: 'visible' }}>
      <defs>
        {/* núcleo: blanco → cyan → violeta (la paleta dentro del glow) */}
        <radialGradient id={core} cx="50%" cy="44%" r="58%">
          <stop offset="0%" stopColor="#FFFFFF" stopOpacity="0.95"/>
          <stop offset="34%" stopColor="#38BDF8" stopOpacity="0.85"/>
          <stop offset="72%" stopColor="#8B5CF6" stopOpacity="0.5"/>
          <stop offset="100%" stopColor="#8B5CF6" stopOpacity="0"/>
        </radialGradient>
        {/* destello de 4 puntas con el degradado de marca */}
        <linearGradient id={spark} x1="0.1" y1="0" x2="0.9" y2="1">
          <stop offset="0%" stopColor="#67E8F9"/>
          <stop offset="50%" stopColor="#38BDF8"/>
          <stop offset="100%" stopColor="#A78BFA"/>
        </linearGradient>
        <linearGradient id={ring} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#22D3EE"/>
          <stop offset="100%" stopColor="#8B5CF6"/>
        </linearGradient>
        <radialGradient id={halo} cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#8B5CF6" stopOpacity="0.3"/>
          <stop offset="100%" stopColor="#8B5CF6" stopOpacity="0"/>
        </radialGradient>
        <filter id={soft} x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="2.4"/></filter>
      </defs>

      {/* halo ambiental */}
      <circle cx="100" cy="100" r="74" fill={`url(#${halo})`} className="halo-pulse"/>

      {/* anillo de órbita, inclinado, en el degradado de marca */}
      <ellipse cx="100" cy="100" rx="82" ry="28" fill="none" stroke={`url(#${ring})`} strokeWidth="0.9"
        opacity="0.5" transform={`rotate(${tilt} 100 100)`}/>

      {/* estrellas que orbitan (como la guía original) */}
      <g className="guide-stars">
        {orbit.map((d, i) => (
          <g key={i}>
            <circle cx={d.x} cy={d.y} r={d.r + 1.3} fill="#67E8F9" opacity="0.22" filter={`url(#${soft})`}/>
            <circle cx={d.x} cy={d.y} r={d.r} fill="#FFFFFF" opacity="0.85"
              className="guide-twinkle" style={{ animationDelay: `${(i * 0.5).toFixed(1)}s` }}/>
          </g>
        ))}
      </g>

      {/* núcleo + destello de 4 puntas (precisión) */}
      <circle cx="100" cy="100" r="28" fill={`url(#${core})`} filter={`url(#${soft})`}/>
      <path d="M100,56 L107,93 L144,100 L107,107 L100,144 L93,107 L56,100 L93,93 Z"
        fill={`url(#${spark})`} filter={`url(#${soft})`} opacity="0.95"/>
    </svg>
  )
}
