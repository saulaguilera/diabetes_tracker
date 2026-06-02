// _Placeholder.jsx — base reutilizable para las pantallas aún sin construir.
import NebulaGuide from '../components/NebulaGuide.jsx'
import { SANS } from '../theme.js'

export default function Placeholder({ theme, kind, title, note }) {
  return (
    <div style={{ padding: '4px 22px 120px', fontFamily: SANS, display: 'flex',
      flexDirection: 'column', alignItems: 'center', gap: 18, textAlign: 'center', minHeight: '70%' , justifyContent: 'center' }}>
      <NebulaGuide kind={kind} size={140} />
      <div style={{ fontSize: 26, fontWeight: 300, letterSpacing: '-0.02em', color: theme.ink }}>{title}</div>
      <div style={{ fontSize: 14, lineHeight: 1.5, color: theme.inkSoft, maxWidth: 260 }}>{note}</div>
      <div style={{ marginTop: 6, fontSize: 11, letterSpacing: '0.14em', textTransform: 'uppercase',
        color: theme.inkFaint, padding: '6px 14px', border: `0.5px solid ${theme.border}`, borderRadius: 100 }}>
        Pantalla por construir
      </div>
    </div>
  )
}
