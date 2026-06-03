// Historial.jsx — timeline de eventos agrupado por día, con filtros.
// Tocar un evento abre el detalle: comidas editables; todo eliminable.
import { useState, useEffect } from 'react'
import { apiGet, apiPut, apiDelete } from '../api.js'
import { PAL, SANS } from '../theme.js'
import { Stepper, Field } from './ui.jsx'

const META = {
  comida:    { color: PAL.metabolismo.key, label: 'Comida' },
  insulina:  { color: PAL.insulina.key,    label: 'Insulina' },
  ejercicio: { color: PAL.glucosa.key,     label: 'Ejercicio' },
}
const FILTERS = [
  { id: 'todos', label: 'Todos' },
  { id: 'comida', label: 'Comidas' },
  { id: 'insulina', label: 'Insulina' },
  { id: 'ejercicio', label: 'Ejercicio' },
]

function dayLabel(dateStr) {
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const d = new Date(dateStr + 'T00:00:00')
  const diff = Math.round((today - d) / 86400000)
  if (diff === 0) return 'Hoy'
  if (diff === 1) return 'Ayer'
  return d.toLocaleDateString('es', { day: 'numeric', month: 'short' })
}

export default function Historial({ theme }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [filter, setFilter] = useState('todos')
  const [sel, setSel] = useState(null)

  const load = () => {
    apiGet('/history').then(setData).catch(e => setErr(e.message))
  }
  useEffect(() => { load() }, [])

  if (err) return <Centered theme={theme}>No se pudo cargar el historial.</Centered>
  if (!data) return <Centered theme={theme}>Cargando…</Centered>

  const events = (data.events || []).filter(e => filter === 'todos' || e.cat === filter)
  const groups = []
  const idx = {}
  events.forEach(e => {
    if (!(e.date in idx)) { idx[e.date] = groups.length; groups.push({ date: e.date, items: [] }) }
    groups[idx[e.date]].items.push(e)
  })

  return (
    <div style={{ fontFamily: SANS }}>
      <div style={{ display: 'flex', gap: 8, overflowX: 'auto', paddingBottom: 14 }}>
        {FILTERS.map(f => {
          const on = filter === f.id
          return (
            <button key={f.id} onClick={() => setFilter(f.id)} style={{
              flexShrink: 0, padding: '8px 14px', borderRadius: 100, cursor: 'pointer', fontFamily: SANS, fontSize: 13,
              background: on ? theme.surfaceStrong : theme.surface, color: on ? theme.ink : theme.inkSoft,
              border: `0.5px solid ${on ? theme.borderStrong : theme.border}`, whiteSpace: 'nowrap' }}>
              {f.label}
            </button>
          )
        })}
      </div>

      {groups.length === 0 ? (
        <Centered theme={theme}>Sin registros en este período.</Centered>
      ) : groups.map(g => (
        <div key={g.date} style={{ marginBottom: 18 }}>
          <div style={{ fontSize: 11, letterSpacing: '0.14em', textTransform: 'uppercase', color: theme.inkFaint, marginBottom: 8, paddingLeft: 2 }}>
            {dayLabel(g.date)}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {g.items.map((e, i) => {
              const m = META[e.cat] || { color: theme.accent }
              return (
                <div key={i} onClick={() => setSel(e)} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '11px 14px',
                  borderRadius: 14, background: theme.surface, border: `0.5px solid ${theme.border}`, cursor: 'pointer' }}>
                  <span style={{ width: 8, height: 8, borderRadius: '50%', flexShrink: 0, background: m.color, boxShadow: `0 0 7px ${m.color}` }}/>
                  <span style={{ flex: 1, color: theme.ink, fontSize: 14.5 }}>{e.title}</span>
                  {e.badge && <span style={{ color: theme.inkSoft, fontSize: 13, fontVariantNumeric: 'tabular-nums' }}>{e.badge}</span>}
                  <span style={{ color: theme.inkFaint, fontSize: 12, fontVariantNumeric: 'tabular-nums', minWidth: 38, textAlign: 'right' }}>{e.time}</span>
                </div>
              )
            })}
          </div>
        </div>
      ))}

      {sel && <DetailSheet theme={theme} ev={sel} onClose={() => setSel(null)} onChanged={() => { setSel(null); load() }}/>}
    </div>
  )
}

function DetailSheet({ theme, ev, onClose, onChanged }) {
  const isMeal = ev.cat === 'comida'
  const d = ev.data || {}
  const m = META[ev.cat] || { color: theme.accent, label: ev.cat }
  const [name, setName] = useState(d.name || '')
  const [carbs, setCarbs] = useState(Math.round(d.carbs || 0))
  const [protein, setProtein] = useState(Math.round(d.protein || 0))
  const [fat, setFat] = useState(Math.round(d.fat || 0))
  const [busy, setBusy] = useState(false)

  const save = async () => {
    setBusy(true)
    try { await apiPut(`/meal/${ev.id}`, { name, carbs, protein, fat }); onChanged() }
    catch (e) { setBusy(false) }
  }
  const remove = async () => {
    setBusy(true)
    try { await apiDelete(`/entry/${ev.cat}/${ev.id}`); onChanged() }
    catch (e) { setBusy(false) }
  }

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 60, background: 'rgba(0,0,0,0.5)',
      display: 'flex', alignItems: 'flex-end', justifyContent: 'center' }}>
      <div onClick={e => e.stopPropagation()} style={{ width: '100%', maxWidth: 460, background: theme.dark ? '#0E1426' : '#fff',
        borderTopLeftRadius: 26, borderTopRightRadius: 26, padding: '22px 22px calc(28px + env(safe-area-inset-bottom))',
        animation: 'slideUp 0.32s cubic-bezier(.2,.8,.2,1)', maxHeight: '85%', overflowY: 'auto' }}>
        <div style={{ width: 38, height: 4, borderRadius: 2, background: theme.border, margin: '0 auto 18px' }}/>

        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 18 }}>
          <span style={{ width: 9, height: 9, borderRadius: '50%', background: m.color, boxShadow: `0 0 8px ${m.color}` }}/>
          <span style={{ flex: 1, color: theme.ink, fontSize: 18, fontWeight: 500 }}>{isMeal ? 'Editar comida' : ev.title}</span>
          <span style={{ color: theme.inkFaint, fontSize: 13 }}>{dayLabel(ev.date)} · {ev.time}</span>
        </div>

        {isMeal ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <Field theme={theme} value={name} onChange={setName} placeholder="¿Qué comiste?"/>
            <Row theme={theme} label="Carbohidratos"><Stepper theme={theme} value={carbs} setValue={setCarbs} step={5} max={300} unit="g" color={m.color}/></Row>
            <Row theme={theme} label="Proteína"><Stepper theme={theme} value={protein} setValue={setProtein} step={5} max={200} unit="g" color={theme.ink}/></Row>
            <Row theme={theme} label="Grasa"><Stepper theme={theme} value={fat} setValue={setFat} step={5} max={200} unit="g" color={theme.ink}/></Row>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            {ev.cat === 'insulina' && <>
              <Info theme={theme} k="Unidades" v={`${d.units}U`}/>
              <Info theme={theme} k="Tipo" v={d.label || d.type}/>
            </>}
            {ev.cat === 'ejercicio' && <>
              <Info theme={theme} k="Actividad" v={d.activity_type}/>
              <Info theme={theme} k="Duración" v={d.duration_min ? `${d.duration_min} min` : '—'}/>
              {d.intensity && <Info theme={theme} k="Intensidad" v={d.intensity}/>}
            </>}
          </div>
        )}

        <div style={{ display: 'flex', gap: 10, marginTop: 22 }}>
          <button onClick={remove} disabled={busy} style={{ padding: '13px 16px', borderRadius: 14, border: `0.5px solid ${theme.border}`,
            background: 'transparent', color: '#D98A6A', fontSize: 14, fontFamily: SANS, cursor: 'pointer' }}>Eliminar</button>
          {isMeal && (
            <button onClick={save} disabled={busy} style={{ flex: 1, padding: '13px', borderRadius: 14, border: 'none',
              background: m.color, color: '#0A0C1E', fontSize: 15, fontWeight: 600, fontFamily: SANS, cursor: 'pointer', opacity: busy ? 0.6 : 1 }}>
              {busy ? 'Guardando…' : 'Guardar'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

function Row({ theme, label, children }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
      <span style={{ color: theme.inkSoft, fontSize: 14 }}>{label}</span>
      {children}
    </div>
  )
}
function Info({ theme, k, v }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '11px 0', borderTop: `0.5px solid ${theme.border}` }}>
      <span style={{ color: theme.inkSoft, fontSize: 14 }}>{k}</span>
      <span style={{ color: theme.ink, fontSize: 14, fontWeight: 500 }}>{v}</span>
    </div>
  )
}
function Centered({ theme, children }) {
  return <div style={{ padding: '40px 20px', textAlign: 'center', color: theme.inkSoft, fontSize: 14, fontFamily: SANS }}>{children}</div>
}
