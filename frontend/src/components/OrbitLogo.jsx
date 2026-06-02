// OrbitLogo.jsx — marca Orbit (fiel al brand sheet):
// anillo orbital cyan→violet con glow, satélite que orbita, núcleo glucosa
// luminoso y órbita interna punteada. Escalable; ids únicos por instancia.
import { useId } from 'react'

export default function OrbitLogo({ size = 26, animate = true }) {
  const raw = useId().replace(/[:]/g, '')
  const ring = 'ring' + raw, core = 'core' + raw, halo = 'halo' + raw
  const bS = 'bs' + raw, bL = 'bl' + raw

  return (
    <svg width={size} height={size} viewBox="0 0 120 120" style={{ overflow: 'visible' }}>
      <defs>
        <linearGradient id={ring} x1="0" y1="0" x2="1" y2="1.1">
          <stop offset="0%" stopColor="#22D3EE"/>
          <stop offset="45%" stopColor="#38BDF8"/>
          <stop offset="100%" stopColor="#8B5CF6"/>
        </linearGradient>
        <radialGradient id={core} cx="44%" cy="40%" r="60%">
          <stop offset="0%" stopColor="#FFFFFF"/>
          <stop offset="35%" stopColor="#5FE3F5"/>
          <stop offset="72%" stopColor="#22D3EE" stopOpacity="0.7"/>
          <stop offset="100%" stopColor="#22D3EE" stopOpacity="0"/>
        </radialGradient>
        <radialGradient id={halo} cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#22D3EE" stopOpacity="0.45"/>
          <stop offset="100%" stopColor="#22D3EE" stopOpacity="0"/>
        </radialGradient>
        <filter id={bS} x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="2"/></filter>
        <filter id={bL} x="-80%" y="-80%" width="260%" height="260%"><feGaussianBlur stdDeviation="6"/></filter>
      </defs>

      {/* glow del anillo */}
      <circle cx="60" cy="60" r="44" fill="none" stroke={`url(#${ring})`} strokeWidth="9" opacity="0.4" filter={`url(#${bL})`}/>
      {/* anillo orbital principal */}
      <circle cx="60" cy="60" r="44" fill="none" stroke={`url(#${ring})`} strokeWidth="6.5" strokeLinecap="round"/>
      {/* órbita interna punteada */}
      <circle cx="60" cy="60" r="25" fill="none" stroke="#22D3EE" strokeWidth="1" strokeDasharray="2 4.5" opacity="0.5"/>
      {/* halo + núcleo glucosa */}
      <circle cx="60" cy="60" r="26" fill={`url(#${halo})`} className={animate ? 'logo-core' : ''}/>
      <circle cx="60" cy="60" r="13" fill={`url(#${core})`}/>
      <circle cx="60" cy="60" r="6" fill="#FFFFFF"/>
      {/* satélite que orbita */}
      <g className={animate ? 'logo-sat' : ''}>
        <circle cx="94" cy="32" r="6" fill="#22D3EE" opacity="0.4" filter={`url(#${bS})`}/>
        <circle cx="94" cy="32" r="4" fill="#FFFFFF"/>
        <circle cx="94" cy="32" r="4" fill="none" stroke="#22D3EE" strokeWidth="1.5"/>
      </g>
    </svg>
  )
}
