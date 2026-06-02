// App.jsx — shell de Orbit Copilot: fondo cósmico + navegación + pantalla activa.
// Andamiaje: las pantallas son placeholders por ahora (sin datos cableados).
import { useState } from 'react'
import { SANS, PAL, makeTheme } from './theme.js'
import Starfield from './components/Starfield.jsx'
import BottomNav from './components/BottomNav.jsx'
import Hoy from './screens/Hoy.jsx'
import Registro from './screens/Registro.jsx'
import Patrones from './screens/Patrones.jsx'
import Copiloto from './screens/Copiloto.jsx'
import Perfil from './screens/Perfil.jsx'

export default function App() {
  // dark fijo por ahora; el toggle de tema se agrega cuando portemos Perfil.
  const theme = makeTheme(true)
  const [tab, setTab] = useState('hoy')

  const screen = {
    hoy:      <Hoy theme={theme} />,
    patrones: <Patrones theme={theme} />,
    registro: <Registro theme={theme} />,
    copiloto: <Copiloto theme={theme} />,
    perfil:   <Perfil theme={theme} />,
  }[tab]

  return (
    // Marco tipo teléfono, centrado — coincide con el prototipo (402×874).
    <div style={{ width: '100vw', height: '100vh', display: 'grid', placeItems: 'center', background: '#060B18' }}>
      <div style={{
        position: 'relative', width: 'min(402px, 100vw)', height: 'min(874px, 100vh)',
        overflow: 'hidden', background: theme.bg, color: theme.ink, fontFamily: SANS,
        boxShadow: '0 30px 90px rgba(0,0,0,0.5)',
      }}>
        {/* glows ambientales de las nebulosas */}
        <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none',
          background: `radial-gradient(60% 32% at 18% 6%, rgba(${PAL.metabolismo.rgb},0.16) 0%, transparent 70%), radial-gradient(55% 30% at 92% 64%, rgba(${PAL.glucosa.rgb},0.12) 0%, transparent 70%), radial-gradient(50% 28% at 50% 100%, rgba(${PAL.ritmo.rgb},0.10) 0%, transparent 70%)` }}/>
        <Starfield count={46} opacity={0.55} seed={3}/>

        <div style={{ position: 'absolute', inset: 0, paddingTop: 52, overflowY: 'auto', WebkitOverflowScrolling: 'touch' }}>
          {screen}
        </div>

        <BottomNav theme={theme} current={tab} onChange={setTab}/>
      </div>
    </div>
  )
}
