// OrbitLogo.jsx — glifo de marca (órbita con núcleo + satélite).
import { PAL } from '../theme.js'

export default function OrbitLogo({ size = 22 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24">
      <circle cx="12" cy="12" r="9" fill="none" stroke={PAL.glucosa.key} strokeWidth="1.4"
        strokeDasharray="40 9" transform="rotate(-40 12 12)" opacity="0.85"/>
      <circle cx="12" cy="12" r="3.2" fill={PAL.glucosa.key}/>
      <circle cx="20.5" cy="9" r="1.6" fill={PAL.ritmo.key}/>
    </svg>
  )
}
