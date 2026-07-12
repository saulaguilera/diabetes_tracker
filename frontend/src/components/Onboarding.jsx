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
import AyudaSheet from './AyudaSheet.jsx'

export default function Onboarding({ theme, onDone }) {
  const { t } = useLang()
  const [story, setStory] = useState(0)   // 0..2 historia · 3 → formulario
  const [step, setStep] = useState(0)
  const [name, setName] = useState('')
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
        name: name.trim(), objetivo,
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
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
      <div style={{ width: '100%', maxWidth: 420 }}>
        {story < 3 ? (
          /* ── historia: tres pantallas, pocas palabras ── */
          <div key={'st' + story} className="rise-in" onClick={() => setStory(v => v + 1)}
            style={{ textAlign: 'center', cursor: 'pointer', padding: '40px 8px' }}>
            <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 34 }}>
              <OrbitLogo size={52}/>
            </div>
            <div style={{ color: theme.ink, fontSize: 30, fontWeight: 300, lineHeight: 1.3,
              letterSpacing: '-0.02em', minHeight: 118 }}>
              {story === 0 && t('onb.story1')}
              {story === 1 && t('onb.story2')}
              {story === 2 && (
                <>
                  {t('onb.story3')}
                  <div style={{ fontSize: 17, color: theme.inkSoft, marginTop: 14 }}>{t('onb.story3b')} 💙</div>
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
              background: theme.accent, color: '#0A0C1E', fontFamily: SANS, fontSize: 15, fontWeight: 600 }}>
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
