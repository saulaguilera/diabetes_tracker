// NebulaGuide.jsx — guías-nebulosa (portado fiel de cm-core.jsx)
// 5 formas: pancreas / glucosa / insulina / ritmo / metabolismo.
import { useMemo } from 'react'
import { PAL } from '../theme.js'

const NB_SEED = { pancreas: 4, glucosa: 11, insulina: 7, ritmo: 2, metabolismo: 19 }

export default function NebulaGuide({ kind = 'pancreas', size = 150, breathe = true, light = false }) {
  const c = PAL[kind]
  const id = 'nb_' + kind
  const cls = breathe
    ? (kind === 'ritmo' || kind === 'pancreas' ? 'breathe-slow' : kind === 'glucosa' ? 'breathe-fast' : 'breathe')
    : 'guide-float'
  const floatDelay = ((NB_SEED[kind] || 1) % 5) * -0.9

  const dots = useMemo(() => {
    let s = (NB_SEED[kind] || 1) * 1777
    const rnd = () => { s = (s * 9301 + 49297) % 233280; return s / 233280 }
    const cfg = {
      pancreas:    { n: 7, cx: 150, cy: 96,  spread: 56, arc: true },
      glucosa:     { n: 5, cx: 64,  cy: 70,  spread: 46, arc: false },
      insulina:    { n: 8, cx: 100, cy: 100, spread: 78, arc: false },
      ritmo:       { n: 6, cx: 100, cy: 100, spread: 86, arc: false },
      metabolismo: { n: 6, cx: 150, cy: 100, spread: 70, arc: false },
    }[kind]
    return Array.from({ length: cfg.n }, (_, i) => cfg.arc
      ? { x: cfg.cx + Math.cos(i / cfg.n * 1.6 + 0.4) * (18 + i * 6), y: cfg.cy + i * 11, r: 1.3 - i * 0.08 }
      : { x: cfg.cx + (rnd() - 0.5) * cfg.spread, y: cfg.cy + (rnd() - 0.5) * cfg.spread, r: 0.7 + rnd() * 1.5 })
  }, [kind])

  return (
    <svg width={size} height={size} viewBox="0 0 200 200" className={cls}
      style={{ overflow: 'visible', animationDelay: breathe ? undefined : `${floatDelay}s` }}>
      <defs>
        <radialGradient id={`${id}core`} cx="44%" cy="38%" r="60%">
          <stop offset="0%" stopColor="#FFFFFF" stopOpacity="0.95"/>
          <stop offset="26%" stopColor={c.key} stopOpacity="0.90"/>
          <stop offset="62%" stopColor={c.mid} stopOpacity="0.62"/>
          <stop offset="100%" stopColor={c.deep} stopOpacity="0"/>
        </radialGradient>
        <radialGradient id={`${id}body`} cx="46%" cy="40%" r="62%">
          <stop offset="0%" stopColor={c.key} stopOpacity="0.78"/>
          <stop offset="52%" stopColor={c.mid} stopOpacity="0.48"/>
          <stop offset="100%" stopColor={c.deep} stopOpacity="0"/>
        </radialGradient>
        <radialGradient id={`${id}smoke`} cx="48%" cy="42%" r="60%">
          <stop offset="0%" stopColor="#EAEDF6" stopOpacity="0.50"/>
          <stop offset="48%" stopColor={c.mid} stopOpacity="0.30"/>
          <stop offset="100%" stopColor={c.deep} stopOpacity="0"/>
        </radialGradient>
        <radialGradient id={`${id}halo`} cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor={c.key} stopOpacity="0.36"/>
          <stop offset="100%" stopColor={c.key} stopOpacity="0"/>
        </radialGradient>
        <filter id={`${id}wisp`} x="-60%" y="-60%" width="220%" height="220%">
          <feTurbulence type="fractalNoise" baseFrequency="0.012 0.02" numOctaves="3" seed={NB_SEED[kind] || 1} result="n"/>
          <feDisplacementMap in="SourceGraphic" in2="n" scale="34" xChannelSelector="R" yChannelSelector="G"/>
          <feGaussianBlur stdDeviation="2.2"/>
        </filter>
        <filter id={`${id}soft`} x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3.5"/></filter>
        <filter id={`${id}blur`} x="-90%" y="-90%" width="280%" height="280%"><feGaussianBlur stdDeviation="13"/></filter>
      </defs>

      {light && <circle cx="100" cy="102" r="72" fill={`rgba(${c.rgb},0.10)`}/>}
      <ellipse cx="100" cy="102" rx="84" ry="82" fill={`url(#${id}halo)`} filter={`url(#${id}blur)`} className="halo-pulse"/>

      {kind === 'pancreas' && (
        <g>
          <g filter={`url(#${id}wisp)`}>
            <path d="M108,30C150,36,152,92,126,116C150,136,150,170,112,178C142,170,134,142,114,124C94,108,98,84,118,70C134,58,130,44,108,30Z" fill={`url(#${id}smoke)`} opacity="0.7"/>
            <ellipse cx="94" cy="74" rx="48" ry="54" fill={`url(#${id}body)`}/>
            <path d="M84,118C112,112,130,132,122,158C116,180,88,182,76,164C66,150,70,126,84,118Z" fill={`url(#${id}body)`}/>
          </g>
          <ellipse cx="92" cy="72" rx="28" ry="33" fill={`url(#${id}core)`} filter={`url(#${id}soft)`}/>
          <ellipse cx="96" cy="150" rx="17" ry="19" fill={`url(#${id}core)`} filter={`url(#${id}soft)`} opacity="0.85"/>
          <circle cx="150" cy="56" r="8" fill={`url(#${id}core)`} filter={`url(#${id}soft)`}/>
        </g>
      )}

      {kind === 'glucosa' && (
        <g>
          <g filter={`url(#${id}wisp)`}>
            <path d="M116,72C146,82,146,124,126,150C108,174,84,174,76,152C68,132,78,118,92,112C80,96,94,70,116,72Z" fill={`url(#${id}body)`}/>
            <path d="M104,96C120,94,128,110,122,128C117,144,100,148,90,138C80,128,86,100,104,96Z" fill={`url(#${id}smoke)`} opacity="0.7"/>
          </g>
          <ellipse cx="110" cy="116" rx="24" ry="28" fill={`url(#${id}core)`} filter={`url(#${id}soft)`} opacity="0.92"/>
          <path d="M98,150 Q116,128 112,100" stroke="#FFFFFF" strokeOpacity="0.45" strokeWidth="1.1" fill="none"/>
          {[[74,68,14],[58,50,9],[50,86,6.5],[126,150,7],[96,160,4.5]].map(([x,y,r],i)=>(
            <g key={i}>
              <circle cx={x} cy={y} r={r} fill={`url(#${id}core)`}/>
              <circle cx={x} cy={y} r={r} fill="none" stroke={c.key} strokeWidth="0.7" opacity="0.45"/>
              <ellipse cx={x-r*0.32} cy={y-r*0.34} rx={r*0.3} ry={r*0.36} fill="#FFFFFF" opacity="0.6"/>
            </g>
          ))}
        </g>
      )}

      {kind === 'insulina' && (
        <g>
          <g filter={`url(#${id}wisp)`}>
            <path d="M104,32C120,54,104,76,112,98C120,120,100,134,106,160C110,178,100,184,96,176C84,150,92,132,98,112C104,92,88,76,98,52C103,40,100,34,104,32Z" fill={`url(#${id}core)`}/>
            <path d="M92,60C84,82,96,98,90,122C85,142,74,150,72,140C70,124,80,110,84,94C88,78,80,66,92,60Z" fill={`url(#${id}smoke)`} opacity="0.65"/>
          </g>
          <circle cx="108" cy="44" r="7" fill={`url(#${id}core)`} filter={`url(#${id}soft)`}/>
          <path d="M100,160 Q106,120 100,84 Q96,60 106,44" stroke="#FFFFFF" strokeOpacity="0.4" strokeWidth="1" fill="none"/>
        </g>
      )}

      {kind === 'ritmo' && (
        <g>
          <circle cx="100" cy="102" r="58" fill={`url(#${id}halo)`} filter={`url(#${id}blur)`}/>
          <circle cx="100" cy="102" r="52" fill={`url(#${id}body)`} opacity="0.42"/>
          <g filter={`url(#${id}soft)`}>
            <circle cx="100" cy="102" r="47" fill={`url(#${id}core)`}/>
            <circle cx="116" cy="92" r="43" fill={c.deep}/>
          </g>
          <circle cx="116" cy="92" r="43" fill={light ? c.mid : '#070613'} opacity={light ? 0.55 : 0.78}/>
          <path d="M62,124 A47,47 0 0 1 80,60" stroke="#FFFFFF" strokeOpacity="0.6" strokeWidth="2.4" fill="none" filter={`url(#${id}soft)`}/>
          <circle cx="100" cy="102" r="52" fill="none" stroke={c.key} strokeWidth="0.6" opacity="0.28"/>
        </g>
      )}

      {kind === 'metabolismo' && (
        <g>
          <g filter={`url(#${id}wisp)`}>
            <path d="M116,28C140,44,134,70,114,86C94,102,92,120,114,136C136,152,138,176,112,186C130,172,126,152,106,138C84,122,84,100,106,84C128,68,134,44,116,28Z" fill={`url(#${id}body)`}/>
            <path d="M90,34C72,52,78,76,98,92C116,106,114,124,94,140C76,154,78,176,94,184C80,170,84,150,104,134C124,118,122,98,102,82C84,68,76,50,90,34Z" fill={`url(#${id}smoke)`} opacity="0.7"/>
          </g>
          <ellipse cx="110" cy="62" rx="20" ry="24" fill={`url(#${id}core)`} filter={`url(#${id}soft)`} opacity="0.85"/>
          <ellipse cx="104" cy="140" rx="18" ry="22" fill={`url(#${id}core)`} filter={`url(#${id}soft)`} opacity="0.8"/>
          <circle cx="120" cy="30" r="7" fill={`url(#${id}core)`} filter={`url(#${id}soft)`}/>
        </g>
      )}

      <g className="guide-stars" style={{ animationDelay: `${floatDelay}s` }}>
        {dots.map((d, i) => (
          <g key={i}>
            <circle cx={d.x} cy={d.y} r={Math.max(0.5, d.r) + 1.4} fill={c.key} opacity="0.20" filter={`url(#${id}soft)`}/>
            <circle cx={d.x} cy={d.y} r={Math.max(0.5, d.r)} fill={light ? c.key : '#FFFFFF'} opacity={light ? 0.65 : 0.85} className="guide-twinkle" style={{ animationDelay: `${(i * 0.6).toFixed(1)}s` }}/>
          </g>
        ))}
      </g>
    </svg>
  )
}
