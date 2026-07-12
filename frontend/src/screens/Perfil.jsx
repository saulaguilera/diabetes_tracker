// Perfil.jsx — datos del usuario, sensor, terapia (editable), médico y tema.
import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { apiGet, apiPut, apiDelete } from '../api.js'
import { PAL, SANS } from '../theme.js'
import { Card, Eyebrow, Stepper, Field, Loading, useSheetClose, backdropAnim, sheetAnim } from '../components/ui.jsx'
import { useLang, LANGS } from '../i18n.jsx'
import AyudaSheet from '../components/AyudaSheet.jsx'

function Centered({ theme, children }) {
  return <div style={{ minHeight: '50%', display: 'grid', placeItems: 'center', textAlign: 'center', padding: '0 32px', color: theme.inkSoft, fontSize: 14, fontFamily: SANS }}>{children}</div>
}

function Row({ theme, label, value, color, sub }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', padding: '11px 0', borderTop: `0.5px solid ${theme.border}` }}>
      <span style={{ color: theme.inkSoft, fontSize: 14 }}>{label}</span>
      <span style={{ textAlign: 'right' }}>
        <span style={{ color: color || theme.ink, fontSize: 14, fontWeight: 500, fontVariantNumeric: 'tabular-nums' }}>{value}</span>
        {sub && <div style={{ color: theme.inkFaint, fontSize: 11, marginTop: 2 }}>{sub}</div>}
      </span>
    </div>
  )
}

function FieldRow({ theme, label, children }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
      <span style={{ color: theme.inkSoft, fontSize: 14 }}>{label}</span>
      {children}
    </div>
  )
}

function Toggle({ on, onChange, color }) {
  return (
    <div onClick={onChange} style={{
      width: 46, height: 27, borderRadius: 100, cursor: 'pointer', flexShrink: 0,
      background: on ? color : 'rgba(120,120,140,0.35)', transition: 'background 0.25s', position: 'relative' }}>
      <div style={{ position: 'absolute', top: 3, left: on ? 22 : 3, width: 21, height: 21, borderRadius: '50%',
        background: '#fff', transition: 'left 0.25s', boxShadow: '0 1px 3px rgba(0,0,0,0.3)' }}/>
    </div>
  )
}

export default function Perfil({ theme, refreshKey = 0, dark = true, onToggleTheme }) {
  const { t, lang, setLang, unit, setUnit, gUnit, gVal } = useLang()
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [editing, setEditing] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)

  useEffect(() => {
    let alive = true
    setErr(null)
    apiGet('/profile')
      .then(d => { if (alive) setData(d) })
      .catch(e => { if (alive) setErr(e.message) })
    return () => { alive = false }
  }, [refreshKey, reloadKey])

  if (err) return <Centered theme={theme}>{t('perfil.loadError')}</Centered>
  if (!data) return <Centered theme={theme}><Loading theme={theme} label={t('common.loading')}/></Centered>

  const s = data.sensor || {}
  const c = data.config || {}
  const obs = data.observed || {}
  const fmt = (v, unit) => (v == null ? '—' : `${v}${unit || ''}`)
  const basalHora = c.basal_hora != null ? Math.round(parseFloat(c.basal_hora)) : null
  const basalTxt = c.basal_dose != null
    ? `${c.basal_dose}U${c.basal_tipo ? ' ' + c.basal_tipo : ''}${basalHora != null ? ` · ${basalHora}:00` : ''}`
    : '—'

  return (
    <div style={{ padding: '4px 22px 120px', fontFamily: SANS, display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between' }}>
        <div>
          <Eyebrow theme={theme}>{t('perfil.title')}</Eyebrow>
          {data.name && <div style={{ fontSize: 26, fontWeight: 300, color: theme.ink, marginTop: 8, letterSpacing: '-0.02em' }}>{t('perfil.hi', { name: data.name })}</div>}
        </div>
        <button onClick={() => setEditing(true)} style={{ background: 'none', border: 'none', color: theme.accent, fontSize: 14, fontFamily: SANS, cursor: 'pointer', padding: '4px 0' }}>{t('common.edit')}</button>
      </div>

      {/* sensor */}
      <Card theme={theme}>
        <Eyebrow theme={theme} style={{ fontSize: 10, marginBottom: 4 }}>{t('perfil.sensor')}</Eyebrow>
        <Row theme={theme} label={t('perfil.lastReading')} value={s.last_reading != null ? `${gVal(s.last_reading)} ${gUnit} · ${s.last_reading_ago}` : '—'}/>
        <Row theme={theme} label={t('perfil.sync')} value={s.last_sync_ago || '—'}/>
        <Row theme={theme} label={t('perfil.source')} value={s.source === 'cgm_libre' ? 'FreeStyle Libre' : (s.source || '—')}/>
        <LibreConnect theme={theme}/>
      </Card>

      {/* terapia — configurado + observado en tus datos (referencia, no auto-aplica) */}
      <Card theme={theme}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
          <Eyebrow theme={theme} style={{ fontSize: 10 }}>{t('perfil.therapy')}</Eyebrow>
          <button onClick={() => setEditing(true)} style={{ background: 'none', border: 'none', color: theme.accent, fontSize: 12.5, fontFamily: SANS, cursor: 'pointer' }}>{t('common.edit')}</button>
        </div>
        <Row theme={theme} label={t('perfil.target')} value={c.objetivo != null ? `${gVal(c.objetivo)} ${gUnit}` : '—'}/>
        <Row theme={theme} label={t('perfil.isf')}
          value={c.isf != null ? `${gVal(c.isf)} ${gUnit}/U` : 'auto'}
          sub={obs.isf ? t('perfil.inYourData', { v: gVal(obs.isf.mu), n: obs.isf.n }) : t('perfil.noDataYet')}/>
        <Row theme={theme} label={t('perfil.icr')}
          value={c.icr != null ? `${c.icr} g/U` : 'auto'}
          sub={obs.icr ? t('perfil.inYourData', { v: obs.icr.mu, n: obs.icr.n }) : t('perfil.noDataYet')}/>
        <Row theme={theme} label={t('perfil.basal')} value={basalTxt}/>
        {(obs.isf || obs.icr) && (
          <div style={{ color: theme.inkFaint, fontSize: 11, lineHeight: 1.5, marginTop: 10 }}>
            {t('perfil.observedNote')}
          </div>
        )}
      </Card>

      {/* equipo médico — reporte PDF descriptivo para llevar a la consulta */}
      <Card theme={theme} glow={PAL.glucosa.soft}>
        <Eyebrow theme={theme} style={{ fontSize: 10, marginBottom: 10 }}>{t('perfil.team')}</Eyebrow>
        <div style={{ color: theme.ink, fontSize: 15, marginBottom: 4 }}>{t('perfil.reportTitle')}</div>
        <div style={{ color: theme.inkSoft, fontSize: 13, lineHeight: 1.5, marginBottom: 14 }}>
          {t('perfil.reportDesc')}
        </div>
        <button onClick={() => window.open('/api/copilot/report.pdf?days=30', '_blank')} style={{
          width: '100%', padding: '12px', borderRadius: 14, border: 'none',
          background: PAL.glucosa.key, color: '#0A0C1E', fontSize: 14, fontWeight: 600,
          fontFamily: SANS, cursor: 'pointer' }}>
          {t('perfil.downloadPdf')}
        </button>
      </Card>

      {/* idioma */}
      <Card theme={theme}>
        <Eyebrow theme={theme} style={{ fontSize: 10, marginBottom: 12 }}>{t('perfil.language')}</Eyebrow>
        <div style={{ display: 'flex', gap: 8 }}>
          {LANGS.map(l => (
            <button key={l.id} onClick={() => setLang(l.id)} style={{
              flex: 1, padding: '11px', borderRadius: 12, cursor: 'pointer', fontFamily: SANS, fontSize: 14,
              border: `0.5px solid ${lang === l.id ? theme.accent : theme.border}`,
              background: lang === l.id ? `${theme.accent}22` : 'transparent',
              color: lang === l.id ? theme.ink : theme.inkSoft, fontWeight: lang === l.id ? 600 : 400 }}>
              {l.label}
            </button>
          ))}
        </div>
      </Card>

      {/* unidad de glucosa */}
      <Card theme={theme}>
        <Eyebrow theme={theme} style={{ fontSize: 10, marginBottom: 12 }}>{t('perfil.glucoseUnit')}</Eyebrow>
        <div style={{ display: 'flex', gap: 8 }}>
          {[{ id: 'mgdl', label: 'mg/dL' }, { id: 'mmol', label: 'mmol/L' }].map(u => (
            <button key={u.id} onClick={() => setUnit(u.id)} style={{
              flex: 1, padding: '11px', borderRadius: 12, cursor: 'pointer', fontFamily: SANS, fontSize: 14,
              border: `0.5px solid ${unit === u.id ? theme.accent : theme.border}`,
              background: unit === u.id ? `${theme.accent}22` : 'transparent',
              color: unit === u.id ? theme.ink : theme.inkSoft, fontWeight: unit === u.id ? 600 : 400 }}>
              {u.label}
            </button>
          ))}
        </div>
      </Card>

      {/* apariencia */}
      <Card theme={theme} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <Eyebrow theme={theme} style={{ fontSize: 10 }}>{t('perfil.appearance')}</Eyebrow>
          <div style={{ color: theme.ink, fontSize: 15, marginTop: 6 }}>{t('perfil.darkTheme')}</div>
        </div>
        <Toggle on={dark} onChange={onToggleTheme} color={theme.accent}/>
      </Card>

      {/* centro de ayuda — guía de uso y conexión de sensores */}
      <Card theme={theme} onClick={() => setHelpOpen(true)} style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 12 }}>
        <span style={{ fontSize: 22 }}>🛟</span>
        <div style={{ flex: 1 }}>
          <div style={{ color: theme.ink, fontSize: 15 }}>{t('perfil.helpTitle')}</div>
          <div style={{ color: theme.inkSoft, fontSize: 12.5, lineHeight: 1.45, marginTop: 2 }}>{t('perfil.helpDesc')}</div>
        </div>
        <span style={{ color: theme.inkFaint, fontSize: 16 }}>›</span>
      </Card>

      {/* cerrar sesión — navegación completa a /logout (limpia la cookie) */}
      <button onClick={() => { window.location.href = '/logout' }} style={{
        width: '100%', padding: '13px', borderRadius: 14, cursor: 'pointer',
        background: 'transparent', border: `0.5px solid ${theme.border}`,
        color: theme.inkSoft, fontFamily: SANS, fontSize: 14 }}>
        {t('perfil.logout')}
      </button>

      <div style={{ color: theme.inkFaint, fontSize: 11, textAlign: 'center', lineHeight: 1.5 }}>
        {t('perfil.footer')}
      </div>

      {editing && <EditSheet theme={theme} name={data.name} config={c} obs={obs}
        onClose={() => setEditing(false)} onSaved={() => { setEditing(false); setReloadKey(k => k + 1) }}/>}
      {helpOpen && <AyudaSheet theme={theme} onClose={() => setHelpOpen(false)}/>}
    </div>
  )
}

function EditSheet({ theme, name: name0, config, obs = {}, onClose, onSaved }) {
  const { t } = useLang()
  const [name, setName] = useState(name0 || '')
  const [objetivo, setObjetivo] = useState(Math.round(config.objetivo ?? 100))
  // ISF/ICR: 'auto' = sin override manual (la app usa lo aprendido de tus
  // datos); 'manual' = valor fijo elegido con el equipo médico.
  const [isfMode, setIsfMode] = useState(config.isf == null ? 'auto' : 'manual')
  const [icrMode, setIcrMode] = useState(config.icr == null ? 'auto' : 'manual')
  const [isf, setIsf] = useState(Math.round(config.isf ?? (obs.isf ? obs.isf.mu : 40)))
  const [icr, setIcr] = useState(Math.round(config.icr ?? (obs.icr ? obs.icr.mu : 10)))
  // basal: dosis diaria, tipo y hora habitual (alimenta modelo + recordatorio)
  const [basalDose, setBasalDose] = useState(Math.round(config.basal_dose ?? 15))
  const [basalTipo, setBasalTipo] = useState(config.basal_tipo || '')
  const [basalHora, setBasalHora] = useState(
    config.basal_hora != null && config.basal_hora !== '' ? Math.round(parseFloat(config.basal_hora)) : 10)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [closing, requestClose] = useSheetClose(onClose)

  const save = async () => {
    setBusy(true); setErr(null)
    const body = { name, objetivo,
      basal_dose: basalDose, basal_tipo: basalTipo, basal_hora: basalHora }
    if (isfMode === 'auto') body.isf_auto = true; else body.isf = isf
    if (icrMode === 'auto') body.icr_auto = true; else body.icr = icr
    try { await apiPut('/profile', body); onSaved() }
    catch (e) { setErr(t('perfil.saveError')); setBusy(false) }
  }

  // segmento Auto/Manual para ISF e ICR
  const ModeSwitch = ({ mode, setMode }) => (
    <div style={{ display: 'flex', gap: 6 }}>
      {['auto', 'manual'].map(m => (
        <button key={m} onClick={() => setMode(m)} style={{
          padding: '6px 12px', borderRadius: 100, fontSize: 12, fontFamily: SANS, cursor: 'pointer',
          border: `0.5px solid ${mode === m ? theme.accent : theme.border}`,
          background: mode === m ? `${theme.accent}22` : 'transparent',
          color: mode === m ? theme.accent : theme.inkSoft, fontWeight: mode === m ? 600 : 400 }}>
          {m === 'auto' ? t('perfil.auto') : t('perfil.manual')}
        </button>
      ))}
    </div>
  )

  return createPortal((
    <div onClick={requestClose} style={{ position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'flex-end', justifyContent: 'center', animation: backdropAnim(closing) }}>
      {/* panel = columna: header fijo + contenido scrolleable + Guardar fijo */}
      <div onClick={e => e.stopPropagation()} style={{ width: '100%', maxWidth: 460, background: theme.dark ? '#0E1426' : '#fff',
        borderTopLeftRadius: 26, borderTopRightRadius: 26,
        animation: sheetAnim(closing), maxHeight: '88%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ padding: '22px 22px 0', flexShrink: 0 }}>
          <div style={{ width: 38, height: 4, borderRadius: 2, background: theme.border, margin: '0 auto 18px' }}/>
          <div style={{ color: theme.ink, fontSize: 18, fontWeight: 500, marginBottom: 14 }}>{t('perfil.editTitle')}</div>
        </div>

        <div style={{ flex: 1, overflowY: 'auto', padding: '4px 22px 8px', WebkitOverflowScrolling: 'touch' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <div style={{ color: theme.inkFaint, fontSize: 11, marginBottom: 7 }}>{t('perfil.name')}</div>
            <Field theme={theme} value={name} onChange={setName} placeholder={t('perfil.namePh')}/>
          </div>
          <FieldRow theme={theme} label={t('perfil.targetField')}><Stepper theme={theme} value={objetivo} setValue={setObjetivo} step={5} min={70} max={180} unit="" color={theme.accent}/></FieldRow>

          {/* ISF: automático (aprendido de tus datos) o manual (acordado con tu médico) */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <FieldRow theme={theme} label={t('perfil.isf')}><ModeSwitch mode={isfMode} setMode={setIsfMode}/></FieldRow>
            {isfMode === 'manual' ? (
              <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <Stepper theme={theme} value={isf} setValue={setIsf} step={1} min={5} max={200} unit="" color={theme.ink}/>
              </div>
            ) : (
              <div style={{ color: theme.inkFaint, fontSize: 12, textAlign: 'right' }}>
                {obs.isf ? t('perfil.usesLearnedIsf', { v: obs.isf.mu, n: obs.isf.n }) : t('perfil.usesLearned')}
              </div>
            )}
          </div>

          {/* ICR: idem */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <FieldRow theme={theme} label={t('perfil.icr')}><ModeSwitch mode={icrMode} setMode={setIcrMode}/></FieldRow>
            {icrMode === 'manual' ? (
              <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <Stepper theme={theme} value={icr} setValue={setIcr} step={1} min={2} max={40} unit="" color={theme.ink}/>
              </div>
            ) : (
              <div style={{ color: theme.inkFaint, fontSize: 12, textAlign: 'right' }}>
                {obs.icr ? t('perfil.usesLearnedIcr', { v: obs.icr.mu, n: obs.icr.n }) : t('perfil.usesLearned')}
              </div>
            )}
          </div>

          <div style={{ borderTop: `0.5px solid ${theme.border}`, paddingTop: 14, display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div style={{ color: theme.inkFaint, fontSize: 11, letterSpacing: '0.12em', textTransform: 'uppercase' }}>{t('perfil.basal')}</div>
            <FieldRow theme={theme} label={t('perfil.basalDose')}><Stepper theme={theme} value={basalDose} setValue={setBasalDose} step={1} min={0} max={80} unit="U" color={theme.ink}/></FieldRow>
            <FieldRow theme={theme} label={t('perfil.basalHour')}><Stepper theme={theme} value={basalHora} setValue={setBasalHora} step={1} min={0} max={23} unit="h" color={theme.ink}/></FieldRow>
            <div>
              <div style={{ color: theme.inkFaint, fontSize: 11, marginBottom: 7 }}>{t('perfil.basalType')}</div>
              <Field theme={theme} value={basalTipo} onChange={setBasalTipo} placeholder="toujeo"/>
            </div>
          </div>
        </div>

        <div style={{ color: theme.inkFaint, fontSize: 11.5, marginTop: 14, lineHeight: 1.5 }}>
          {t('perfil.basalNote')}
        </div>
        {err && <div style={{ color: '#D98A6A', fontSize: 13, marginTop: 10 }}>{err}</div>}
        </div>

        {/* Guardar SIEMPRE visible: footer fijo fuera del área scrolleable */}
        <div style={{ flexShrink: 0, padding: '12px 22px calc(16px + env(safe-area-inset-bottom))',
          borderTop: `0.5px solid ${theme.border}`,
          background: theme.dark ? '#0E1426' : '#fff' }}>
          <button onClick={save} disabled={busy} style={{ width: '100%', padding: '14px', borderRadius: 14, border: 'none',
            background: theme.accent, color: '#0A0C1E', fontSize: 15, fontWeight: 600, fontFamily: SANS, cursor: 'pointer', opacity: busy ? 0.6 : 1 }}>
            {busy ? t('common.saving') : t('common.save')}
          </button>
        </div>
      </div>
    </div>
  ), document.body)
}

// ── Conexión del sensor: cuenta LibreLinkUp del usuario ──────────────────────
// GET/PUT/DELETE /api/copilot/libre. La contraseña viaja UNA vez (se guarda
// cifrada en el servidor) y jamás vuelve.
function LibreConnect({ theme }) {
  const { t } = useLang()
  const [st, setSt] = useState(null)        // {connected, email, provider}
  const [open, setOpen] = useState(false)
  const [provider, setProvider] = useState('libre')
  const [email, setEmail] = useState('')
  const [pass, setPass] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  // etiquetas por proveedor (nightscout usa URL + token)
  const P = {
    libre:      { name: 'FreeStyle Libre', c: '#FFC72C', f1: t('perfil.libre.email'), f2: t('perfil.libre.password'), t1: 'email' },
    dexcom:     { name: 'Dexcom',          c: '#58A618', f1: t('sensor.dexcomUser'), f2: t('perfil.libre.password'), t1: 'text' },
    nightscout: { name: 'Nightscout',      c: '#8B5CF6', f1: t('sensor.nsUrl'), f2: t('sensor.nsToken'), t1: 'url' },
  }

  useEffect(() => {
    apiGet('/libre').then(setSt).catch(() => {})
  }, [])

  const conectar = async () => {
    if (busy) return
    setBusy(true); setMsg(null)
    try {
      const r = await apiPut('/libre', { provider, email: email.trim(), password: pass })
      setSt(r); setOpen(false); setEmail(''); setPass('')
      setMsg(t('perfil.libre.ok'))
    } catch (e) {
      setMsg(t('perfil.libre.badCreds'))
    } finally { setBusy(false) }
  }

  const desconectar = async () => {
    if (busy) return
    setBusy(true); setMsg(null)
    try { const r = await apiDelete('/libre'); setSt(r) } catch (e) {}
    setBusy(false)
  }

  const inputStyle = {
    width: '100%', padding: '10px 12px', borderRadius: 12, fontSize: 14, fontFamily: SANS,
    background: theme.bg, border: `0.5px solid ${theme.border}`, color: theme.ink, outline: 'none',
  }
  const btn = (primary) => ({
    padding: '9px 16px', borderRadius: 100, cursor: 'pointer', fontFamily: SANS, fontSize: 13,
    border: primary ? 'none' : `0.5px solid ${theme.border}`,
    background: primary ? theme.accent : 'transparent',
    color: primary ? '#0A0C1E' : theme.inkSoft,
  })

  return (
    <div style={{ marginTop: 12, paddingTop: 12, borderTop: `0.5px solid ${theme.border}` }}>
      {st && st.connected ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ flex: 1, color: theme.inkSoft, fontSize: 13 }}>
            {(P[st.provider] || P.libre).name} · <span style={{ color: theme.ink }}>{st.email}</span>
          </span>
          <button onClick={desconectar} style={btn(false)}>{t('perfil.libre.disconnect')}</button>
        </div>
      ) : !open ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ flex: 1, color: theme.inkFaint, fontSize: 12.5, lineHeight: 1.45 }}>
            {t('perfil.libre.pitch')}
          </span>
          <button onClick={() => setOpen(true)} style={btn(true)}>{t('perfil.libre.connect')}</button>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'flex', gap: 8 }}>
            {Object.entries(P).map(([id, p]) => (
              <button key={id} onClick={() => setProvider(id)} style={{
                flex: 1, padding: '8px 4px', borderRadius: 12, cursor: 'pointer', fontFamily: SANS,
                fontSize: 12, fontWeight: 600,
                border: `0.5px solid ${provider === id ? p.c : theme.border}`,
                background: provider === id ? `${p.c}22` : 'transparent',
                color: provider === id ? theme.ink : theme.inkSoft }}>
                {p.name}
              </button>
            ))}
          </div>
          <div style={{ color: theme.inkFaint, fontSize: 12, lineHeight: 1.45 }}>
            {provider === 'nightscout' ? t('sensor.nsHint') : provider === 'dexcom' ? t('sensor.dexcomHint') : t('perfil.libre.hint')}
          </div>
          <input style={inputStyle} type={P[provider].t1} placeholder={P[provider].f1}
            value={email} onChange={e => setEmail(e.target.value)} autoComplete="off"/>
          <input style={inputStyle} type="password" placeholder={P[provider].f2}
            value={pass} onChange={e => setPass(e.target.value)} autoComplete="new-password"/>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button onClick={() => { setOpen(false); setMsg(null) }} style={btn(false)}>{t('common.cancel')}</button>
            <button onClick={conectar} disabled={busy || !email || (provider !== 'nightscout' && !pass) || (provider === 'libre' && !email.includes('@'))} style={{ ...btn(true), opacity: busy ? 0.6 : 1 }}>
              {busy ? t('perfil.libre.checking') : t('perfil.libre.save')}
            </button>
          </div>
        </div>
      )}
      {msg && <div style={{ color: theme.inkFaint, fontSize: 12, marginTop: 8 }}>{msg}</div>}
    </div>
  )
}
