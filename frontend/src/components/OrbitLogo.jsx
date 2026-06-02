// OrbitLogo.jsx — marca Orbit: anillo orbital (cyan→violet) + satélite que orbita
// + núcleo glucosa luminoso + órbita interna punteada. Inspirado en el brand sheet.
import { PAL } from '../theme.js'

export default function OrbitLogo({ size = 26, animate = true }) {
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" style={{ overflow: 'visible' }}>
      <defs>
        <linearGradient id="orbRing" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor={PAL.glucosa.key}/>
          <stop offset="55%" stopColor={PAL.pancreas.key}/>
          <stop offset="100%" stopColor={PAL.ritmo.key}/>
        </linearGradient>
        <radialGradient id="orbCore" cx="50%" cy="42%" r="55%">
          <stop offset="0%" stopColor="#FFFFFF"/>
          <stop offset="45%" stopColor={PAL.glucosa.key}/>
          <stop offset="100%" stopColor={PAL.glucosa.mid} stopOpacity="0"/>
        </radialGradient>
        <filter id="orbGlow" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="1.4"/></filter>
        <filter id="orbGlowBig" x="-80%" y="-80%" width="260%" height="260%"><feGaussianBlur stdDeviation="3"/></filter>
      </defs>

      {/* halo del anillo */}
      <circle cx="20" cy="20" r="15" fill="none" stroke={PAL.pancreas.key} strokeWidth="3" opacity="0.25" filter="url(#orbGlowBig)"/>
      {/* anillo orbital principal con gradiente */}
      <circle cx="20" cy="20" r="15" fill="none" stroke="url(#orbRing)" strokeWidth="2.4" strokeLinecap="round" filter="url(#orbGlow)"/>
      {/* órbita interna punteada */}
      <circle cx="20" cy="20" r="8.5" fill="none" stroke={PAL.glucosa.key} strokeWidth="0.7" strokeDasharray="1.5 3" opacity="0.45"/>

      {/* satélite que orbita (grupo que rota alrededor del centro) */}
      <g className={animate ? 'logo-sat' : ''}>
        <circle cx="33.6" cy="13.5" r="2.4" fill={PAL.glucosa.key} filter="url(#orbGlow)"/>
        <circle cx="33.6" cy="13.5" r="1.4" fill="#FFFFFF"/>
      </g>

      {/* núcleo glucosa */}
      <circle cx="20" cy="20" r="7" fill="url(#orbCore)" className={animate ? 'logo-core' : ''}/>
      <circle cx="20" cy="20" r="3" fill="#FFFFFF" opacity="0.95"/>
    </svg>
  )
}
