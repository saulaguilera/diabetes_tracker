// Perfil.jsx — datos del usuario, sensor, terapia (editable), médico y tema.
import { useState, useEffect } from 'react'
import { apiGet, apiPut } from '../api.js'
import { PAL, SANS } from '../theme.js'
import { Card, Eyebrow, Stepper, Field, useSheetClose, backdropAnim, sheetAnim } from '../components/ui.jsx'

function Centered({ theme, children }) {
  return <div style={{ minHeight: '50%', display: 'grid', placeItems: 'center', textAlign: 'center', padding: '0 32px', color: theme.inkSoft, fontSize: 14, fontFamily: SANS }}>{children}</div>
}

function Row({ theme, label, value, color }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', padding: '11px 0', borderTop: `0.5px solid ${theme.border}` }}>
      <span style={{ color: theme.inkSoft, fontSize: 14 }}>{label}</span>
      <span style={{ color: color || theme.ink, fontSize: 14, fontWeight: 500, fontVariantNumeric: 'tabular-nums' }}>{value}</span>
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

      {/* terapia */}
      <Card theme={theme}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
          <Eyebrow theme={theme} style={{ fontSize: 10 }}>Tu terapia</Eyebrow>
          <button onClick={() => setEditing(true)} style={{ background: 'none', border: 'none', color: theme.accent, fontSize: 12.5, fontFamily: SANS, cursor: 'pointer' }}>Editar</button>
        </div>
        <Row theme={theme} label="Objetivo" value={fmt(c.objetivo, ' mg/dL')}/>
        <Row theme={theme} label="Sensibilidad (ISF)" value={c.isf != null ? `${c.isf} mg/dL/U` : 'auto'}/>
        <Row theme={theme} label="Ratio (ICR)" value={c.icr != null ? `${c.icr} g/U` : 'auto'}/>
        <Row theme={theme} label="Basal" value={basalTxt}/>
      </Card>

      {/* equipo médico */}
      <Card theme={theme} glow={PAL.glucosa.soft}>
        <Eyebrow theme={theme} style={{ fontSize: 10, marginBottom: 10 }}>Tu equipo médico</Eyebrow>
        <div style={{ color: theme.ink, fontSize: 15, marginBottom: 4 }}>Conectá con tu médico</div>
        <div style={{ color: theme.inkSoft, fontSize: 13, lineHeight: 1.5, marginBottom: 14 }}>
          Compartí tus reportes y patrones con tu equipo de forma segura.
        </div>
        <button disabled style={{
          width: '100%', padding: '12px', borderRadius: 14, border: `0.5px solid ${theme.border}`,
          background: theme.surface, color: theme.inkFaint, fontSize: 14, fontFamily: SANS, cursor: 'default' }}>
          Próximamente
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

      {editing && <EditSheet theme={theme} name={data.name} config={c}
        onClose={() => setEditing(false)} onSaved={() => { setEditing(false); setReloadKey(k => k + 1) }}/>}
    </div>
  )
}

function EditSheet({ theme, name: name0, config, onClose, onSaved }) {
  const [name, setName] = useState(name0 || '')
  const [objetivo, setObjetivo] = useState(Math.round(config.objetivo ?? 100))
  const [isf, setIsf] = useState(Math.round(config.isf ?? 40))
  const [icr, setIcr] = useState(Math.round(config.icr ?? 10))
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [closing, requestClose] = useSheetClose(onClose)

  const save = async () => {
    setBusy(true); setErr(null)
    try { await apiPut('/profile', { name, objetivo, isf, icr }); onSaved() }
    catch (e) { setErr('No se pudo guardar.'); setBusy(false) }
  }

  return (
    <div onClick={requestClose} style={{ position: 'fixed', inset: 0, zIndex: 60, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'flex-end', justifyContent: 'center', animation: backdropAnim(closing) }}>
      <div onClick={e => e.stopPropagation()} style={{ width: '100%', maxWidth: 460, background: theme.dark ? '#0E1426' : '#fff',
        borderTopLeftRadius: 26, borderTopRightRadius: 26, padding: '22px 22px calc(28px + env(safe-area-inset-bottom))',
        animation: sheetAnim(closing), maxHeight: '88%', overflowY: 'auto' }}>
        <div style={{ width: 38, height: 4, borderRadius: 2, background: theme.border, margin: '0 auto 18px' }}/>
        <div style={{ color: theme.ink, fontSize: 18, fontWeight: 500, marginBottom: 18 }}>Editar perfil</div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <div style={{ color: theme.inkFaint, fontSize: 11, marginBottom: 7 }}>Nombre</div>
            <Field theme={theme} value={name} onChange={setName} placeholder="Tu nombre"/>
          </div>
          <FieldRow theme={theme} label="Objetivo (mg/dL)"><Stepper theme={theme} value={objetivo} setValue={setObjetivo} step={5} min={70} max={180} unit="" color={theme.accent}/></FieldRow>
          <FieldRow theme={theme} label="Sensibilidad ISF"><Stepper theme={theme} value={isf} setValue={setIsf} step={1} min={5} max={200} unit="" color={theme.ink}/></FieldRow>
          <FieldRow theme={theme} label="Ratio ICR"><Stepper theme={theme} value={icr} setValue={setIcr} step={1} min={2} max={40} unit="" color={theme.ink}/></FieldRow>
        </div>

        <div style={{ color: theme.inkFaint, fontSize: 11.5, marginTop: 14, lineHeight: 1.5 }}>
          La basal y el sensor se configuran en la app principal.
        </div>
        {err && <div style={{ color: '#D98A6A', fontSize: 13, marginTop: 10 }}>{err}</div>}

        <button onClick={save} disabled={busy} style={{ width: '100%', marginTop: 18, padding: '14px', borderRadius: 14, border: 'none',
          background: theme.accent, color: '#0A0C1E', fontSize: 15, fontWeight: 600, fontFamily: SANS, cursor: 'pointer', opacity: busy ? 0.6 : 1 }}>
          {busy ? 'Guardando…' : 'Guardar'}
        </button>
      </div>
    </div>
  )
}
