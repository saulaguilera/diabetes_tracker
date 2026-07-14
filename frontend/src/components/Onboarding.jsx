// Onboarding.jsx — bienvenida de un usuario nuevo (3 pasos, overlay a pantalla
// completa): 1) nombre · 2) objetivo + basal · 3) conectar sensor (Libre /
// Dexcom / Nightscout). Aparece cuando /profile dice onboarded:false; al
// terminar marca onboarded. Saltar el sensor es válido (Perfil lo permite después).
import { useState } from 'react'
import { createPortal } from 'react-dom'
import { apiPut } from '../api.js'
import { SANS } from '../theme.js'
import { useLang } from '../i18n.jsx'
import OrbitLogo from './OrbitLogo.jsx'
import Starfield from './Starfield.jsx'
import AyudaSheet from './AyudaSheet.jsx'

// texto que entra palabra por palabra (esencia: la historia se va contando)
function PalabrasVivas({ texto, base = 0.15 }) {
  return texto.split(' ').map((w, i) => (
    <span key={i} className="onb-word" style={{ animationDelay: `${(base + i * 0.09).toFixed(2)}s` }}>
      {w}{' '}
    </span>
  ))
}

// la curva de glucosa que se dibuja sola — el corazón visual de la marca.
// Con `marcadores`, los eventos del día (comida/insulina/movimiento) aparecen
// sobre la curva: "Orbit te ayuda a entenderla".
function OndaHistoria({ marcadores = false }) {
  const d = 'M4,56 C34,52 48,20 74,18 C100,16 110,46 136,52 C162,58 172,28 198,24 C224,20 234,50 258,54 C282,58 298,36 316,32'
  const eventos = [[74, 18, '🍎'], [136, 52, '💧'], [198, 24, '🏃']]
  return (
    <svg viewBox="0 0 320 78" style={{ width: '100%', maxWidth: 300, height: 74, display: 'block',
      margin: '0 auto 26px', overflow: 'visible' }}>
      <defs>
        <linearGradient id="ondaOnb" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#22D3EE"/>
          <stop offset="55%" stopColor="#38BDF8"/>
          <stop offset="100%" stopColor="#8B5CF6"/>
        </linearGradient>
      </defs>
      <path d={d} fill="none" stroke="url(#ondaOnb)" strokeWidth="3.5" strokeLinecap="round"
        opacity="0.28" filter="blur(5px)"
        style={{ strokeDasharray: 560, strokeDashoffset: 560, animation: 'waveDraw 2.1s cubic-bezier(.45,0,.2,1) .25s forwards' }}/>
      <path d={d} fill="none" stroke="url(#ondaOnb)" strokeWidth="3" strokeLinecap="round"
        style={{ strokeDasharray: 560, strokeDashoffset: 560, animation: 'waveDraw 2.1s cubic-bezier(.45,0,.2,1) .25s forwards' }}/>
      {marcadores && eventos.map(([x, y, emoji], i) => (
        <g key={i} className="onb-pop" style={{ animationDelay: `${(0.9 + i * 0.35).toFixed(2)}s` }}>
          <circle cx={x} cy={y} r="5.5" fill="#0B1324" stroke="#22D3EE" strokeWidth="1.5"/>
          <circle cx={x} cy={y} r="2.4" fill="#22D3EE"/>
          <text x={x} y={y - 13} textAnchor="middle" fontSize="15">{emoji}</text>
        </g>
      ))}
    </svg>
  )
}

export default function Onboarding({ theme, onDone }) {
  const { t } = useLang()
  const [story, setStory] = useState(0)   // 0..2 historia · 3 → formulario
  const [step, setStep] = useState(0)
  const [name, setName] = useState('')
  const [perfil, setPerfil] = useState('estandar')   // deportista | cuidador | estandar
  const [objetivo, setObjetivo] = useState('100')
  const [basalTipo, setBasalTipo] = useState('')
  const [basalDose, setBasalDose] = useState('')
  const [basalHora, setBasalHora] = useState('22:00')
  const [provider, setProvider] = useState('libre')
  const [email, setEmail] = useState('')
  const [pass, setPass] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [helpOpen, setHelpOpen] = useState(false)

  // proveedores CGM compatibles (mismo mapa que Perfil → LibreConnect)
  const P = {
    libre:      { name: 'FreeStyle Libre', c: '#FFC72C', f1: t('perfil.libre.email'), f2: t('perfil.libre.password'), t1: 'email' },
    dexcom:     { name: 'Dexcom',          c: '#58A618', f1: t('sensor.dexcomUser'), f2: t('perfil.libre.password'), t1: 'text' },
    nightscout: { name: 'Nightscout',      c: '#8B5CF6', f1: t('sensor.nsUrl'), f2: t('sensor.nsToken'), t1: 'url' },
  }

  const input = {
    width: '100%', padding: '13px 15px', borderRadius: 14, fontSize: 15, fontFamily: SANS,
    background: theme.surface, border: `0.5px solid ${theme.borderStrong || theme.border}`,
    color: theme.ink, outline: 'none',
  }
  const label = { color: theme.inkSoft, fontSize: 13, margin: '0 0 6px 2px', display: 'block' }
  const btn = (primary) => ({
    padding: '13px 22px', borderRadius: 100, cursor: 'pointer', fontFamily: SANS,
    fontSize: 15, fontWeight: 600, border: primary ? 'none' : `0.5px solid ${theme.border}`,
    background: primary ? theme.accent : 'transparent',
    color: primary ? '#0A0C1E' : theme.inkSoft,
  })

  const saveBasics = async () => {
    if (busy) return
    setBusy(true); setErr(null)
    try {
      await apiPut('/profile', {
        name: name.trim(), objetivo, perfil_vida: perfil,
        basal_tipo: basalTipo.trim(), basal_dose: basalDose || null,
        basal_hora: String(parseInt(basalHora.split(':')[0] || '22', 10)),
      })
      setStep(2)
    } catch { setErr(t('onb.saveError')) } finally { setBusy(false) }
  }

  const connectSensor = async () => {
    if (busy) return
    setBusy(true); setErr(null)
    try {
      await apiPut('/libre', { provider, email: email.trim(), password: pass })
      await finish()
    } catch { setErr(t('perfil.libre.badCreds')); setBusy(false) }
  }

  const finish = async () => {
    try { await apiPut('/profile', { onboarded: true }) } catch {}
    onDone()
  }

  return createPortal((
    <div style={{ position: 'fixed', inset: 0, zIndex: 300, fontFamily: SANS,
      background: theme.dark ? 'radial-gradient(125% 90% at 50% -8%, #16243F 0%, #0B1324 46%, #060B18 100%)' : theme.bg,
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24, overflow: 'hidden' }}>
      {/* esencia de marca: nebulosa a la deriva + estrellas que titilan */}
      {theme.dark && (
        <>
          <div className="onb-nebula" style={{ position: 'absolute', inset: '-12%', pointerEvents: 'none',
            background: 'radial-gradient(55% 32% at 18% 8%, rgba(139,92,246,0.15) 0%, transparent 70%), ' +
                        'radial-gradient(50% 30% at 88% 62%, rgba(34,211,238,0.11) 0%, transparent 70%), ' +
                        'radial-gradient(48% 28% at 50% 102%, rgba(56,189,248,0.09) 0%, transparent 70%)' }}/>
          <Starfield count={46} seed={7} opacity={0.85}/>
        </>
      )}
      <div style={{ width: '100%', maxWidth: 420, position: 'relative' }}>
        {story < 3 ? (
          /* ── historia: tres pantallas, pocas palabras, cada una con su visual ── */
          <div key={'st' + story} className="rise-in" onClick={() => setStory(v => v + 1)}
            style={{ textAlign: 'center', cursor: 'pointer', padding: '40px 8px' }}>
            <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 30 }}>
              <OrbitLogo size={60}/>
            </div>

            {/* visual temático: la curva se dibuja → los eventos la explican → el corazón */}
            <div style={{ minHeight: 86 }}>
              {story === 0 && <OndaHistoria/>}
              {story === 1 && <OndaHistoria marcadores/>}
              {story === 2 && (
                <div className="onb-pop" style={{ fontSize: 42, marginBottom: 18, animationDelay: '0.2s' }}>
                  <span className="onb-heart">💙</span>
                </div>
              )}
            </div>

            <div style={{ color: theme.ink, fontSize: 30, fontWeight: 300, lineHeight: 1.3,
              letterSpacing: '-0.02em', minHeight: 118 }}>
              {story === 0 && <PalabrasVivas texto={t('onb.story1')} base={0.5}/>}
              {story === 1 && <PalabrasVivas texto={t('onb.story2')} base={0.5}/>}
              {story === 2 && (
                <>
                  <PalabrasVivas texto={t('onb.story3')} base={0.4}/>
                  <div style={{ fontSize: 17, color: theme.inkSoft, marginTop: 14 }}>
                    <PalabrasVivas texto={t('onb.story3b')} base={1.1}/>
                  </div>
                </>
              )}
            </div>
            <div style={{ display: 'flex', gap: 6, justifyContent: 'center', margin: '30px 0 26px' }}>
              {[0, 1, 2].map(i => (
                <div key={i} style={{ width: i === story ? 22 : 8, height: 8, borderRadius: 4,
                  background: i <= story ? theme.accent : theme.border, transition: 'all .3s' }}/>
              ))}
            </div>
            <button onClick={e => { e.stopPropagation(); setStory(v => v + 1) }} style={{
              padding: '13px 34px', borderRadius: 100, cursor: 'pointer', border: 'none',
              background: theme.accent, color: '#0A0C1E', fontFamily: SANS, fontSize: 15, fontWeight: 600,
              boxShadow: '0 0 26px rgba(34,211,238,0.35)' }}>
              {story === 2 ? t('onb.start') : t('onb.continue')}
            </button>
          </div>
        ) : (
        <>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, justifyContent: 'center', marginBottom: 8 }}>
          <OrbitLogo size={30}/>
          <span style={{ fontSize: 21, fontWeight: 300, color: theme.ink }}>Orbit <span style={{ fontWeight: 500,
            background: 'linear-gradient(100deg, #22D3EE 0%, #38BDF8 42%, #8B5CF6 100%)',
            WebkitBackgroundClip: 'text', backgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>Copilot</span></span>
        </div>
        {/* progreso */}
        <div style={{ display: 'flex', gap: 6, justifyContent: 'center', marginBottom: 26 }}>
          {[0, 1, 2].map(i => (
            <div key={i} style={{ width: i === step ? 22 : 8, height: 8, borderRadius: 4,
              background: i <= step ? theme.accent : theme.border, transition: 'all .3s' }}/>
          ))}
        </div>

        {step === 0 && (
          <div key="s0" className="rise-in">
            <div style={{ color: theme.ink, fontSize: 24, fontWeight: 300, marginBottom: 6 }}>{t('onb.hi')}</div>
            <div style={{ color: theme.inkSoft, fontSize: 14.5, lineHeight: 1.55, marginBottom: 24 }}>{t('onb.intro')}</div>
            <label style={label}>{t('onb.name')}</label>
            <input style={input} value={name} onChange={e => setName(e.target.value)} autoFocus/>

            {/* perfil de vida: adapta la voz del copiloto */}
            <label style={{ ...label, marginTop: 18 }}>{t('onb.perfil')}</label>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {[
                { id: 'estandar',   emoji: '💙', titulo: t('onb.perfilStd'),  desc: t('onb.perfilStdD') },
                { id: 'deportista', emoji: '🏃', titulo: t('onb.perfilDep'),  desc: t('onb.perfilDepD') },
                { id: 'cuidador',   emoji: '🤝', titulo: t('onb.perfilCui'),  desc: t('onb.perfilCuiD') },
              ].map(p => (
                <button key={p.id} onClick={() => setPerfil(p.id)} style={{
                  display: 'flex', alignItems: 'center', gap: 11, textAlign: 'left',
                  padding: '11px 13px', borderRadius: 14, cursor: 'pointer', fontFamily: SANS,
                  border: `0.5px solid ${perfil === p.id ? theme.accent : theme.border}`,
                  background: perfil === p.id ? `${theme.accent}18` : 'transparent' }}>
                  <span style={{ fontSize: 20 }}>{p.emoji}</span>
                  <span>
                    <div style={{ color: theme.ink, fontSize: 14, fontWeight: perfil === p.id ? 600 : 400 }}>{p.titulo}</div>
                    <div style={{ color: theme.inkFaint, fontSize: 11.5, marginTop: 1 }}>{p.desc}</div>
                  </span>
                </button>
              ))}
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 22 }}>
              <button style={btn(true)} disabled={!name.trim()} onClick={() => setStep(1)}
                onMouseDown={e => e.preventDefault()}>{t('onb.next')}</button>
            </div>
          </div>
        )}

        {step === 1 && (
          <div key="s1" className="rise-in">
            <div style={{ color: theme.ink, fontSize: 22, fontWeight: 300, marginBottom: 18 }}>{t('onb.therapy')}</div>
            <label style={label}>{t('onb.target')}</label>
            <input style={{ ...input, marginBottom: 14 }} type="number" inputMode="numeric" value={objetivo} onChange={e => setObjetivo(e.target.value)}/>
            <label style={label}>{t('onb.basalType')}</label>
            <input style={{ ...input, marginBottom: 14 }} placeholder="lantus, toujeo, tresiba…" value={basalTipo} onChange={e => setBasalTipo(e.target.value)}/>
            <div style={{ display: 'flex', gap: 10 }}>
              <div style={{ flex: 1 }}>
                <label style={label}>{t('onb.basalDose')}</label>
                <input style={input} type="number" inputMode="decimal" value={basalDose} onChange={e => setBasalDose(e.target.value)}/>
              </div>
              <div style={{ flex: 1 }}>
                <label style={label}>{t('onb.basalHour2')}</label>
                <input style={input} type="time" step="3600" value={basalHora} onChange={e => setBasalHora(e.target.value)}/>
              </div>
            </div>
            <div style={{ color: theme.inkFaint, fontSize: 12, lineHeight: 1.5, marginTop: 12 }}>{t('onb.therapyNote')}</div>
            {err && <div style={{ color: '#E8A79B', fontSize: 13, marginTop: 10 }}>{err}</div>}
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 22 }}>
              <button style={btn(false)} onClick={() => setStep(0)}>{t('onb.back')}</button>
              <button style={btn(true)} disabled={busy} onClick={saveBasics}>{busy ? t('common.saving') : t('onb.next')}</button>
            </div>
          </div>
        )}

        {step === 2 && (
          <div key="s2" className="rise-in">
            <div style={{ color: theme.ink, fontSize: 22, fontWeight: 300, marginBottom: 8 }}>{t('onb.sensor')}</div>
            {/* selector de proveedor CGM */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
              {Object.entries(P).map(([id, p]) => (
                <button key={id} onClick={() => { setProvider(id); setEmail(''); setPass(''); setErr(null) }} style={{
                  flex: 1, padding: '9px 4px', borderRadius: 12, cursor: 'pointer', fontFamily: SANS,
                  fontSize: 12.5, fontWeight: 600,
                  border: `0.5px solid ${provider === id ? p.c : theme.border}`,
                  background: provider === id ? `${p.c}22` : 'transparent',
                  color: provider === id ? theme.ink : theme.inkSoft }}>
                  {p.name}
                </button>
              ))}
            </div>
            <div style={{ color: theme.inkSoft, fontSize: 13.5, lineHeight: 1.55, marginBottom: 6 }}>
              {provider === 'nightscout' ? t('sensor.nsHint') : provider === 'dexcom' ? t('sensor.dexcomHint') : t('onb.sensorHint')}
            </div>
            <button onClick={() => setHelpOpen(true)} style={{ background: 'none', border: 'none', padding: 0,
              color: theme.accent, fontSize: 13, fontFamily: SANS, cursor: 'pointer', marginBottom: 16 }}>
              {t('onb.sensorHelp')}
            </button>
            <label style={label}>{P[provider].f1}</label>
            <input style={{ ...input, marginBottom: 14 }} type={P[provider].t1} value={email} onChange={e => setEmail(e.target.value)} autoComplete="off"/>
            <label style={label}>{P[provider].f2}</label>
            <input style={input} type="password" value={pass} onChange={e => setPass(e.target.value)} autoComplete="new-password"/>
            {err && <div style={{ color: '#E8A79B', fontSize: 13, marginTop: 10 }}>{err}</div>}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 22 }}>
              <button style={{ ...btn(false), border: 'none' }} onClick={finish}>{t('onb.skip')}</button>
              <button style={btn(true)} onClick={connectSensor}
                disabled={busy || !email.trim() || (provider !== 'nightscout' && !pass) || (provider === 'libre' && !email.includes('@'))}>
                {busy ? t('perfil.libre.checking') : t('perfil.libre.save')}
              </button>
            </div>
          </div>
        )}
        </>
        )}
      </div>
      {helpOpen && <AyudaSheet theme={theme} initial={provider} onClose={() => setHelpOpen(false)}/>}
    </div>
  ), document.body)
}
