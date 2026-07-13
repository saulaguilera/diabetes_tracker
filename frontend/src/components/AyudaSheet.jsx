// AyudaSheet.jsx — centro de ayuda: guía de uso y conexión de sensores.
// Sheet a pantalla completa con secciones desplegables (acordeón). Se abre
// desde Perfil («Centro de ayuda») y desde el paso del sensor del onboarding
// (con la sección del proveedor elegido ya abierta, vía `initial`).
// El contenido largo vive aquí en es/en en lugar de i18n.jsx: son párrafos
// completos, no etiquetas de UI.
// OJO: el copiloto tiene una versión condensada de esta guía en su prompt
// (blueprints/copilot_api.py, sección GUÍA DE USO DE ORBIT) — si cambias
// pasos aquí, actualízala también.
import { useState } from 'react'
import { createPortal } from 'react-dom'
import { SANS } from '../theme.js'
import { useLang } from '../i18n.jsx'
import { useSheetClose, backdropAnim, sheetAnim } from './ui.jsx'

const CONTENT = {
  es: {
    title: 'Centro de ayuda',
    sub: 'Todo lo que necesitas para sacarle el máximo a Orbit.',
    footer: '¿Sigues con dudas? Pregúntale al copiloto en el chat — está para eso. También puedes escribir a sauvlogs@gmail.com.',
    sections: [
      {
        id: 'primeros', icon: '🚀', title: 'Primeros pasos',
        blocks: [
          { p: 'Orbit es tu copiloto para la diabetes tipo 1: junta tu glucosa, comidas, insulina y ejercicio en un solo lugar, y los analiza por ti.' },
          { steps: [
            'Conecta tu sensor (aquí abajo te explicamos cómo) para que la glucosa llegue sola.',
            'Registra tus comidas, insulina y ejercicio en la pestaña Registro. Mientras más registres, mejor te conoce Orbit.',
            'En unos días, el copiloto empieza a encontrar patrones y te avisa con la campanita 🔔.',
          ] },
          { note: 'Orbit describe y acompaña: nunca calcula dosis ni reemplaza a tu equipo médico.' },
        ],
      },
      {
        id: 'libre', icon: '🟡', color: '#FFC72C', title: 'Conectar FreeStyle Libre',
        blocks: [
          { p: 'Orbit se conecta a través de LibreLinkUp, la app con la que familiares y amigos siguen tu glucosa — no con tu app LibreLink principal.' },
          { steps: [
            'En tu app LibreLink (donde ves tu glucosa), ve a Menú → Compartir → Aplicaciones conectadas → LibreLinkUp e invita un correo. Puede ser el de un familiar… o uno tuyo distinto al de LibreLink.',
            'Descarga la app LibreLinkUp, inicia sesión con ese correo invitado y acepta la invitación.',
            'En Orbit, elige FreeStyle Libre y escribe el email y la contraseña de esa cuenta de LibreLinkUp.',
          ] },
          { note: 'Tus credenciales se guardan cifradas y solo se usan para sincronizar. La glucosa llega mientras tu teléfono con LibreLink tenga internet.' },
        ],
      },
      {
        id: 'dexcom', icon: '🟢', color: '#58A618', title: 'Conectar Dexcom',
        blocks: [
          { p: 'Orbit usa Dexcom Share, la función de compartir de tu app Dexcom (G6 o G7).' },
          { steps: [
            'En tu app de Dexcom, activa Compartir (Share) e invita al menos a un seguidor. Puede ser un familiar, o tú mismo con otro correo. Share debe quedar activo.',
            'En Orbit, elige Dexcom y escribe el usuario y la contraseña de tu cuenta Dexcom — la tuya principal, no la del seguidor.',
          ] },
          { note: 'Funciona con cuentas de Latinoamérica, Europa y EE. UU. Se guarda cifrado.' },
        ],
      },
      {
        id: 'nightscout', icon: '🟣', color: '#8B5CF6', title: 'Conectar Nightscout',
        blocks: [
          { p: 'Si usas Nightscout (Loop, AndroidAPS, Omnipod DIY…), Orbit trae tu glucosa y también los bolos de tu bomba, automáticamente.' },
          { steps: [
            'En Orbit, elige Nightscout y escribe la dirección de tu sitio (por ejemplo misitio.up.railway.app). No hace falta el https://.',
            'Si tu sitio es privado, agrega un token de acceso (en Nightscout: Admin tools → Subjects). Si es público, déjalo vacío.',
          ] },
          { note: 'Los bolos que registra tu bomba aparecen en Orbit como insulina, sin que tengas que anotarlos.' },
        ],
      },
      {
        id: 'registro', icon: '📝', title: 'Registrar comidas, insulina y ejercicio',
        blocks: [
          { steps: [
            'En la pestaña Registro, toca lo que quieras anotar: comida, insulina, ejercicio o contexto (estrés, enfermedad, dormir mal…).',
            'A las comidas puedes sacarles una foto: el copiloto estima los carbohidratos por ti. Igual puedes ajustar el número.',
            'Las etiquetas de contexto valen oro: le explican al copiloto por qué un día se comportó distinto.',
          ] },
          { note: 'Ninguna cantidad tiene que ser perfecta. Un registro aproximado siempre es mejor que ninguno.' },
        ],
      },
      {
        id: 'copiloto', icon: '🧠', title: 'El copiloto y los patrones',
        blocks: [
          { steps: [
            'En la pestaña Copiloto puedes conversar: pídele el resumen del día, pregúntale por una comida o por qué amaneciste alto.',
            'Orbit busca patrones solo — fenómeno del alba, hipos después del ejercicio, franjas horarias difíciles — y te avisa con la campanita 🔔.',
            'En la pestaña Patrones ves todo lo que ha encontrado, con la explicación y qué comentar con tu médico.',
          ] },
          { note: 'También puedes descargar un reporte PDF en Perfil para llevar a tu consulta.' },
        ],
      },
      {
        id: 'drive', icon: '🚗', title: 'Orbit Drive',
        blocks: [
          { p: 'Orbit Drive muestra tu glucosa en vivo en la pantalla de bloqueo y la Dynamic Island mientras manejas u otra actividad donde no quieres abrir la app.' },
          { steps: [
            'Toca el botón de Orbit Drive arriba a la derecha para activarlo.',
            'iOS apaga estas actividades en vivo después de unas 8 horas: si se apagó, vuelve a abrir Drive y listo.',
          ] },
        ],
      },
      {
        id: 'problemas', icon: '🛟', title: 'Problemas comunes',
        blocks: [
          { qa: [
            ['No llegan lecturas', 'Revisa que las credenciales sean correctas, que el seguidor (LibreLinkUp) o Share (Dexcom) siga activo, y que el teléfono del sensor tenga internet. Orbit sincroniza cada ~5 minutos.'],
            ['Me equivoqué al conectar el sensor', 'En Perfil → Sensor, toca Desconectar y vuelve a conectar con los datos correctos.'],
            ['No me llegan notificaciones', 'Revisa que Orbit tenga permiso de notificaciones en Ajustes de tu iPhone.'],
            ['Quiero cambiar mi objetivo o mi basal', 'En Perfil, toca Editar arriba a la derecha.'],
          ] },
        ],
      },
    ],
  },
  en: {
    title: 'Help center',
    sub: 'Everything you need to get the most out of Orbit.',
    footer: 'Still stuck? Ask the copilot in the chat — that\'s what it\'s for. You can also write to sauvlogs@gmail.com.',
    sections: [
      {
        id: 'primeros', icon: '🚀', title: 'Getting started',
        blocks: [
          { p: 'Orbit is your type 1 diabetes copilot: it brings your glucose, meals, insulin and exercise together in one place, and analyzes them for you.' },
          { steps: [
            'Connect your sensor (explained below) so glucose flows in on its own.',
            'Log your meals, insulin and exercise in the Log tab. The more you log, the better Orbit knows you.',
            'Within a few days, the copilot starts finding patterns and pings you with the bell 🔔.',
          ] },
          { note: 'Orbit describes and accompanies: it never calculates doses and never replaces your medical team.' },
        ],
      },
      {
        id: 'libre', icon: '🟡', color: '#FFC72C', title: 'Connect FreeStyle Libre',
        blocks: [
          { p: 'Orbit connects through LibreLinkUp, the app family and friends use to follow your glucose — not your main LibreLink app.' },
          { steps: [
            'In your LibreLink app (where you see your glucose), go to Menu → Share → Connected apps → LibreLinkUp and invite an email. It can be a family member\'s… or a second one of yours.',
            'Download the LibreLinkUp app, sign in with that invited email and accept the invitation.',
            'In Orbit, pick FreeStyle Libre and enter that LibreLinkUp account\'s email and password.',
          ] },
          { note: 'Your credentials are stored encrypted and only used for syncing. Glucose flows as long as the phone running LibreLink is online.' },
        ],
      },
      {
        id: 'dexcom', icon: '🟢', color: '#58A618', title: 'Connect Dexcom',
        blocks: [
          { p: 'Orbit uses Dexcom Share, the sharing feature of your Dexcom app (G6 or G7).' },
          { steps: [
            'In your Dexcom app, turn on Share and invite at least one follower. It can be a family member, or yourself with another email. Share must stay on.',
            'In Orbit, pick Dexcom and enter your Dexcom account\'s username and password — your main one, not the follower\'s.',
          ] },
          { note: 'Works with accounts from Latin America, Europe and the US. Stored encrypted.' },
        ],
      },
      {
        id: 'nightscout', icon: '🟣', color: '#8B5CF6', title: 'Connect Nightscout',
        blocks: [
          { p: 'If you run Nightscout (Loop, AndroidAPS, DIY Omnipod…), Orbit pulls your glucose and your pump boluses, automatically.' },
          { steps: [
            'In Orbit, pick Nightscout and enter your site address (e.g. mysite.up.railway.app). No need for https://.',
            'If your site is private, add an access token (in Nightscout: Admin tools → Subjects). If it\'s public, leave it empty.',
          ] },
          { note: 'Boluses recorded by your pump show up in Orbit as insulin, no manual logging needed.' },
        ],
      },
      {
        id: 'registro', icon: '📝', title: 'Logging meals, insulin and exercise',
        blocks: [
          { steps: [
            'In the Log tab, tap whatever you want to record: meal, insulin, exercise or context (stress, illness, bad sleep…).',
            'You can snap a photo of meals: the copilot estimates the carbs for you. You can always adjust the number.',
            'Context tags are gold: they tell the copilot why a day behaved differently.',
          ] },
          { note: 'No amount has to be perfect. A rough log always beats no log.' },
        ],
      },
      {
        id: 'copiloto', icon: '🧠', title: 'The copilot and patterns',
        blocks: [
          { steps: [
            'In the Copilot tab you can chat: ask for today\'s summary, about a meal, or why you woke up high.',
            'Orbit hunts for patterns on its own — dawn phenomenon, post-exercise lows, tricky time windows — and pings you with the bell 🔔.',
            'The Patterns tab shows everything it has found, with the explanation and what to discuss with your doctor.',
          ] },
          { note: 'You can also download a PDF report in Profile to bring to your appointment.' },
        ],
      },
      {
        id: 'drive', icon: '🚗', title: 'Orbit Drive',
        blocks: [
          { p: 'Orbit Drive shows your live glucose on the lock screen and Dynamic Island while driving or any time you don\'t want to open the app.' },
          { steps: [
            'Tap the Orbit Drive button at the top right to start it.',
            'iOS ends live activities after about 8 hours: if it turned off, just open Drive again.',
          ] },
        ],
      },
      {
        id: 'problemas', icon: '🛟', title: 'Troubleshooting',
        blocks: [
          { qa: [
            ['No readings coming in', 'Check that your credentials are correct, that the follower (LibreLinkUp) or Share (Dexcom) is still active, and that the sensor\'s phone is online. Orbit syncs every ~5 minutes.'],
            ['I connected the sensor with the wrong details', 'In Profile → Sensor, tap Disconnect and connect again with the right details.'],
            ['I don\'t get notifications', 'Check that Orbit has notification permission in your iPhone Settings.'],
            ['I want to change my target or basal', 'In Profile, tap Edit at the top right.'],
          ] },
        ],
      },
    ],
  },
}

function Block({ theme, block, color }) {
  if (block.p) return (
    <p style={{ color: theme.inkSoft, fontSize: 13.5, lineHeight: 1.6, margin: '0 0 10px' }}>{block.p}</p>
  )
  if (block.steps) return (
    <ol style={{ margin: '0 0 10px', padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 8 }}>
      {block.steps.map((s, i) => (
        <li key={i} style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
          <span style={{ flexShrink: 0, width: 20, height: 20, borderRadius: '50%', marginTop: 1,
            background: `${color || theme.accent}22`, color: color || theme.accent,
            display: 'grid', placeItems: 'center', fontSize: 11, fontWeight: 700 }}>{i + 1}</span>
          <span style={{ color: theme.inkSoft, fontSize: 13.5, lineHeight: 1.55 }}>{s}</span>
        </li>
      ))}
    </ol>
  )
  if (block.note) return (
    <div style={{ color: theme.inkFaint, fontSize: 12.5, lineHeight: 1.55, padding: '8px 10px',
      borderLeft: `2px solid ${color || theme.accent}55`, margin: '2px 0 10px' }}>{block.note}</div>
  )
  if (block.qa) return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginBottom: 4 }}>
      {block.qa.map(([q, a], i) => (
        <div key={i}>
          <div style={{ color: theme.ink, fontSize: 13.5, fontWeight: 600, marginBottom: 3 }}>{q}</div>
          <div style={{ color: theme.inkSoft, fontSize: 13, lineHeight: 1.55 }}>{a}</div>
        </div>
      ))}
    </div>
  )
  return null
}

export default function AyudaSheet({ theme, onClose, initial = null }) {
  const { lang } = useLang()
  const C = CONTENT[lang] || CONTENT.es
  const [open, setOpen] = useState(initial)
  const [closing, requestClose] = useSheetClose(onClose)

  return createPortal((
    <div onClick={requestClose} style={{ position: 'fixed', inset: 0, zIndex: 400, background: 'rgba(0,0,0,0.5)',
      display: 'flex', alignItems: 'flex-end', justifyContent: 'center', animation: backdropAnim(closing), fontFamily: SANS }}>
      <div onClick={e => e.stopPropagation()} style={{ width: '100%', maxWidth: 460, background: theme.dark ? '#0E1426' : '#fff',
        borderTopLeftRadius: 26, borderTopRightRadius: 26, padding: '22px 22px calc(28px + env(safe-area-inset-bottom))',
        animation: sheetAnim(closing), maxHeight: '88%', overflowY: 'auto' }}>
        <div style={{ width: 38, height: 4, borderRadius: 2, background: theme.border, margin: '0 auto 18px' }}/>

        <div style={{ color: theme.ink, fontSize: 20, fontWeight: 500 }}>{C.title}</div>
        <div style={{ color: theme.inkSoft, fontSize: 13, lineHeight: 1.5, margin: '4px 0 16px' }}>{C.sub}</div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {C.sections.map(sec => {
            const abierta = open === sec.id
            return (
              <div key={sec.id} style={{ borderRadius: 16, overflow: 'hidden',
                border: `0.5px solid ${abierta ? (sec.color || theme.accent) + '66' : theme.border}`,
                background: abierta ? (theme.dark ? 'rgba(255,255,255,0.03)' : 'rgba(0,0,0,0.02)') : 'transparent',
                transition: 'border-color .2s' }}>
                <button onClick={() => setOpen(abierta ? null : sec.id)} style={{
                  width: '100%', display: 'flex', alignItems: 'center', gap: 10, padding: '13px 14px',
                  background: 'none', border: 'none', cursor: 'pointer', fontFamily: SANS, textAlign: 'left' }}>
                  <span style={{ fontSize: 16 }}>{sec.icon}</span>
                  <span style={{ flex: 1, color: theme.ink, fontSize: 14.5, fontWeight: abierta ? 600 : 400 }}>{sec.title}</span>
                  <span style={{ color: theme.inkFaint, fontSize: 12, transform: abierta ? 'rotate(90deg)' : 'none', transition: 'transform .2s' }}>›</span>
                </button>
                {abierta && (
                  <div style={{ padding: '2px 14px 12px' }}>
                    {sec.blocks.map((b, i) => <Block key={i} theme={theme} block={b} color={sec.color}/>)}
                  </div>
                )}
              </div>
            )
          })}
        </div>

        <div style={{ color: theme.inkFaint, fontSize: 12.5, lineHeight: 1.55, marginTop: 18, textAlign: 'center' }}>
          {C.footer}
        </div>
      </div>
    </div>
  ), document.body)
}
