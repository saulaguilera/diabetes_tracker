// Onboarding.jsx — bienvenida de un usuario nuevo (3 pasos, overlay a pantalla
// completa): 1) nombre · 2) objetivo + basal · 3) conectar sensor LibreLinkUp.
// Aparece cuando /profile dice onboarded:false; al terminar marca onboarded.
// Saltear el sensor es válido (puede conectarlo después en Perfil).
import { useState } from 'react'
import { createPortal } from 'react-dom'
import { apiPut } from '../api.js'
import { SANS } from '../theme.js'
import { useLang } from '../i18n.jsx'
import OrbitLogo from './OrbitLogo.jsx'

export default function Onboarding({ theme, onDone }) {
  const { t } = useLang()
  const [step, setStep] = useState(0)
  const [name, setName] = useState('')
  const [objetivo, setObjetivo] = useState('100')
  const [basalTipo, setBasalTipo] = useState('')
  const [basalDose, setBasalDose] = useState('')
  const [basalHora, setBasalHora] = useState('22')
  const [email, setEmail] = useState('')
  const [pass, setPass] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

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
        basal_tipo: basalTipo.trim(), basal_dose: basalDose || null, basal_hora: basalHora,
      })
      setStep(2)
    } catch { setErr(t('onb.saveError')) } finally { setBusy(false) }
  }

  const connectSensor = async () => {
    if (busy) return
    setBusy(true); setErr(null)
    try {
      await apiPut('/libre', { email: email.trim(), password: pass })
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
          <div>
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
          <div>
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
                <label style={label}>{t('onb.basalHour')}</label>
                <input style={input} type="number" inputMode="numeric" min="0" max="23" value={basalHora} onChange={e => setBasalHora(e.target.value)}/>
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
          <div>
            <div style={{ color: theme.ink, fontSize: 22, fontWeight: 300, marginBottom: 8 }}>{t('onb.sensor')}</div>
            {/* badge LibreLinkUp (hasta que haya más sensores compatibles) */}
            <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '7px 12px',
              borderRadius: 100, marginBottom: 14, background: 'rgba(255,199,44,0.12)',
              border: '0.5px solid rgba(255,199,44,0.45)' }}>
              <span style={{ width: 18, height: 18, borderRadius: '50%', background: '#FFC72C',
                display: 'grid', placeItems: 'center', color: '#1A1A1A', fontSize: 10, fontWeight: 800 }}>L</span>
              <span style={{ color: theme.ink, fontSize: 13, fontWeight: 600 }}>FreeStyle LibreLinkUp</span>
            </div>
            <div style={{ color: theme.inkSoft, fontSize: 13.5, lineHeight: 1.55, marginBottom: 18 }}>{t('onb.sensorHint')}</div>
            <label style={label}>{t('perfil.libre.email')}</label>
            <input style={{ ...input, marginBottom: 14 }} type="email" value={email} onChange={e => setEmail(e.target.value)} autoComplete="off"/>
            <label style={label}>{t('perfil.libre.password')}</label>
            <input style={input} type="password" value={pass} onChange={e => setPass(e.target.value)} autoComplete="new-password"/>
            {err && <div style={{ color: '#E8A79B', fontSize: 13, marginTop: 10 }}>{err}</div>}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 22 }}>
              <button style={{ ...btn(false), border: 'none' }} onClick={finish}>{t('onb.skip')}</button>
              <button style={btn(true)} disabled={busy || !email.includes('@') || !pass} onClick={connectSensor}>
                {busy ? t('perfil.libre.checking') : t('perfil.libre.save')}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  ), document.body)
}
