// Historial.jsx — timeline de eventos (comida / insulina / ejercicio) agrupado
// por día, con filtros. Consume GET /api/copilot/history.
import { useState, useEffect } from 'react'
import { apiGet } from '../api.js'
import { PAL, SANS } from '../theme.js'

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

  useEffect(() => {
    let alive = true
    apiGet('/history')
      .then(d => { if (alive) setData(d) })
      .catch(e => { if (alive) setErr(e.message) })
    return () => { alive = false }
  }, [])

  if (err) return <Centered theme={theme}>No se pudo cargar el historial.</Centered>
  if (!data) return <Centered theme={theme}>Cargando…</Centered>

  const events = (data.events || []).filter(e => filter === 'todos' || e.cat === filter)
  // agrupar por día
  const groups = []
  const idx = {}
  events.forEach(e => {
    if (!(e.date in idx)) { idx[e.date] = groups.length; groups.push({ date: e.date, items: [] }) }
    groups[idx[e.date]].items.push(e)
  })

  return (
    <div style={{ fontFamily: SANS }}>
      {/* filtros */}
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
                <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '11px 14px',
                  borderRadius: 14, background: theme.surface, border: `0.5px solid ${theme.border}` }}>
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
    </div>
  )
}

function Centered({ theme, children }) {
  return <div style={{ padding: '40px 20px', textAlign: 'center', color: theme.inkSoft, fontSize: 14, fontFamily: SANS }}>{children}</div>
}
