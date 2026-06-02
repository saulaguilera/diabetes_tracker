// Starfield.jsx — puntos de luz a la deriva (portado de cm-core.jsx)
import { useMemo } from 'react'

export default function Starfield({ count = 40, opacity = 1, seed = 1, color = '255,255,255' }) {
  const stars = useMemo(() => {
    let s = seed * 9301 + 49297
    const rnd = () => { s = (s * 9301 + 49297) % 233280; return s / 233280 }
    return Array.from({ length: count }, () => ({
      x: rnd() * 100, y: rnd() * 100,
      r: 0.4 + rnd() * 1.3, d: rnd() * 6, dur: 3 + rnd() * 5,
    }))
  }, [count, seed])

  return (
    <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', opacity }}>
      {stars.map((st, i) => (
        <div key={i} style={{
          position: 'absolute', left: `${st.x}%`, top: `${st.y}%`,
          width: st.r * 2, height: st.r * 2, borderRadius: '50%',
          background: `rgba(${color},0.9)`,
          boxShadow: `0 0 ${st.r * 3}px rgba(${color},0.7)`,
          animation: `twinkle ${st.dur}s ease-in-out ${st.d}s infinite`,
        }}/>
      ))}
    </div>
  )
}
