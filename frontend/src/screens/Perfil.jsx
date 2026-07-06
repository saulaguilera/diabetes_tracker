// Perfil.jsx — datos del usuario, sensor, terapia (editable), médico y tema.
import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { apiGet, apiPut } from '../api.js'
import { PAL, SANS } from '../theme.js'
import { Card, Eyebrow, Stepper, Field, useSheetClose, backdropAnim, sheetAnim } from '../components/ui.jsx'

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
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [editing, setEditing] = useState(false)

  useEffect(() => {
    let alive = true
    setErr(null)
    apiGet('/profile')
      .then(d => { if (alive) setData(d) })
      .catch(e => { if (alive) setErr(e.message) })
    return () => { alive = false }
  }, [refreshKey, reloadKey])

  if (err) return <Centered theme={theme}>No se pudo cargar tu perfil ahora.</Centered>
  if (!data) return <Centered theme={theme}>Cargando…</Centered>

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
          <Eyebrow theme={theme}>Perfil</Eyebrow>
          {data.name && <div style={{ fontSize: 26, fontWeight: 300, color: theme.ink, marginTop: 8, letterSpacing: '-0.02em' }}>Hola, {data.name}</div>}
        </div>
        <button onClick={() => setEditing(true)} style={{ background: 'none', border: 'none', color: theme.accent, fontSize: 14, fontFamily: SANS, cursor: 'pointer', padding: '4px 0' }}>Editar</button>
      </div>

      {/* sensor */}
      <Card theme={theme}>
        <Eyebrow theme={theme} style={{ fontSize: 10, marginBottom: 4 }}>Tu sensor</Eyebrow>
        <Row theme={theme} label="Última lectura" value={s.last_reading != null ? `${s.last_reading} mg/dL · ${s.last_reading_ago}` : '—'}/>
        <Row theme={theme} label="Sincronización" value={s.last_sync_ago || '—'}/>
        <Row theme={theme} label="Fuente" value={s.source === 'cgm_libre' ? 'FreeStyle Libre' : (s.source || '—')}/>
      </Card>

      {/* terapia — configurado + observado en tus datos (referencia, no auto-aplica) */}
      <Card theme={theme}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
          <Eyebrow theme={theme} style={{ fontSize: 10 }}>Tu terapia</Eyebrow>
          <button onClick={() => setEditing(true)} style={{ background: 'none', border: 'none', color: theme.accent, fontSize: 12.5, fontFamily: SANS, cursor: 'pointer' }}>Editar</button>
        </div>
        <Row theme={theme} label="Objetivo" value={fmt(c.objetivo, ' mg/dL')}/>
        <Row theme={theme} label="Sensibilidad (ISF)"
          value={c.isf != null ? `${c.isf} mg/dL/U` : 'auto'}
          sub={obs.isf ? `en tus datos: ~${obs.isf.mu} (${obs.isf.n} obs.)` : null}/>
        <Row theme={theme} label="Ratio (ICR)"
          value={c.icr != null ? `${c.icr} g/U` : 'auto'}
          sub={obs.icr ? `en tus datos: ~${obs.icr.mu} (${obs.icr.n} obs.)` : null}/>
        <Row theme={theme} label="Basal" value={basalTxt}/>
        {(obs.isf || obs.icr) && (
          <div style={{ color: theme.inkFaint, fontSize: 11, lineHeight: 1.5, marginTop: 10 }}>
            "En tus datos" es lo que Orbit observó (aprendizaje bayesiano por franjas).
            Es referencia para conversar con tu equipo médico — no se aplica solo.
          </div>
        )}
      </Card>

      {/* equipo médico — reporte PDF descriptivo para llevar a la consulta */}
      <Card theme={theme} glow={PAL.glucosa.soft}>
        <Eyebrow theme={theme} style={{ fontSize: 10, marginBottom: 10 }}>Tu equipo médico</Eyebrow>
        <div style={{ color: theme.ink, fontSize: 15, marginBottom: 4 }}>Reporte para tu consulta</div>
        <div style={{ color: theme.inkSoft, fontSize: 13, lineHeight: 1.5, marginBottom: 14 }}>
          TIR, noches, hipos y coberturas observadas de los últimos 30 días — datos, no opiniones.
        </div>
        <button onClick={() => window.open('/api/copilot/report.pdf?days=30', '_blank')} style={{
          width: '100%', padding: '12px', borderRadius: 14, border: 'none',
          background: PAL.glucosa.key, color: '#0A0C1E', fontSize: 14, fontWeight: 600,
          fontFamily: SANS, cursor: 'pointer' }}>
          Descargar reporte (PDF)
        </button>
      </Card>

      {/* apariencia */}
      <Card theme={theme} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <Eyebrow theme={theme} style={{ fontSize: 10 }}>Apariencia</Eyebrow>
          <div style={{ color: theme.ink, fontSize: 15, marginTop: 6 }}>Tema oscuro</div>
        </div>
        <Toggle on={dark} onChange={onToggleTheme} color={theme.accent}/>
      </Card>

      <div style={{ color: theme.inkFaint, fontSize: 11, textAlign: 'center', lineHeight: 1.5 }}>
        Orbit · copiloto metabólico
      </div>

      {editing && <EditSheet theme={theme} name={data.name} config={c} obs={obs}
        onClose={() => setEditing(false)} onSaved={() => { setEditing(false); setReloadKey(k => k + 1) }}/>}
    </div>
  )
}

function EditSheet({ theme, name: name0, config, obs = {}, onClose, onSaved }) {
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
    catch (e) { setErr('No se pudo guardar.'); setBusy(false) }
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
          {m === 'auto' ? 'Automático' : 'Manual'}
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
          <div style={{ color: theme.ink, fontSize: 18, fontWeight: 500, marginBottom: 14 }}>Editar perfil</div>
        </div>

        <div style={{ flex: 1, overflowY: 'auto', padding: '4px 22px 8px', WebkitOverflowScrolling: 'touch' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <div style={{ color: theme.inkFaint, fontSize: 11, marginBottom: 7 }}>Nombre</div>
            <Field theme={theme} value={name} onChange={setName} placeholder="Tu nombre"/>
          </div>
          <FieldRow theme={theme} label="Objetivo (mg/dL)"><Stepper theme={theme} value={objetivo} setValue={setObjetivo} step={5} min={70} max={180} unit="" color={theme.accent}/></FieldRow>

          {/* ISF: automático (aprendido de tus datos) o manual (acordado con tu médico) */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <FieldRow theme={theme} label="Sensibilidad ISF"><ModeSwitch mode={isfMode} setMode={setIsfMode}/></FieldRow>
            {isfMode === 'manual' ? (
              <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <Stepper theme={theme} value={isf} setValue={setIsf} step={1} min={5} max={200} unit="" color={theme.ink}/>
              </div>
            ) : (
              <div style={{ color: theme.inkFaint, fontSize: 12, textAlign: 'right' }}>
                {obs.isf ? `usa lo aprendido de tus datos (~${obs.isf.mu} mg/dL/U, ${obs.isf.n} obs.)` : 'usa lo aprendido de tus datos'}
              </div>
            )}
          </div>

          {/* ICR: idem */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <FieldRow theme={theme} label="Ratio ICR"><ModeSwitch mode={icrMode} setMode={setIcrMode}/></FieldRow>
            {icrMode === 'manual' ? (
              <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <Stepper theme={theme} value={icr} setValue={setIcr} step={1} min={2} max={40} unit="" color={theme.ink}/>
              </div>
            ) : (
              <div style={{ color: theme.inkFaint, fontSize: 12, textAlign: 'right' }}>
                {obs.icr ? `usa lo aprendido de tus datos (~${obs.icr.mu} g/U, ${obs.icr.n} obs.)` : 'usa lo aprendido de tus datos'}
              </div>
            )}
          </div>

          <div style={{ borderTop: `0.5px solid ${theme.border}`, paddingTop: 14, display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div style={{ color: theme.inkFaint, fontSize: 11, letterSpacing: '0.12em', textTransform: 'uppercase' }}>Basal</div>
            <FieldRow theme={theme} label="Dosis diaria"><Stepper theme={theme} value={basalDose} setValue={setBasalDose} step={1} min={0} max={80} unit="U" color={theme.ink}/></FieldRow>
            <FieldRow theme={theme} label="Hora habitual"><Stepper theme={theme} value={basalHora} setValue={setBasalHora} step={1} min={0} max={23} unit="h" color={theme.ink}/></FieldRow>
            <div>
              <div style={{ color: theme.inkFaint, fontSize: 11, marginBottom: 7 }}>Tipo (toujeo, glargina, degludec…)</div>
              <Field theme={theme} value={basalTipo} onChange={setBasalTipo} placeholder="toujeo"/>
            </div>
          </div>
        </div>

        <div style={{ color: theme.inkFaint, fontSize: 11.5, marginTop: 14, lineHeight: 1.5 }}>
          La basal alimenta el modelo, el contexto del copiloto y el recordatorio diario.
        </div>
        {err && <div style={{ color: '#D98A6A', fontSize: 13, marginTop: 10 }}>{err}</div>}
        </div>

        {/* Guardar SIEMPRE visible: footer fijo fuera del área scrolleable */}
        <div style={{ flexShrink: 0, padding: '12px 22px calc(16px + env(safe-area-inset-bottom))',
          borderTop: `0.5px solid ${theme.border}`,
          background: theme.dark ? '#0E1426' : '#fff' }}>
          <button onClick={save} disabled={busy} style={{ width: '100%', padding: '14px', borderRadius: 14, border: 'none',
            background: theme.accent, color: '#0A0C1E', fontSize: 15, fontWeight: 600, fontFamily: SANS, cursor: 'pointer', opacity: busy ? 0.6 : 1 }}>
            {busy ? 'Guardando…' : 'Guardar'}
          </button>
        </div>
      </div>
    </div>
  ), document.body)
}
