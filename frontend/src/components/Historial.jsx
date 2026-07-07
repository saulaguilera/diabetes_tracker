// Historial.jsx — timeline de eventos agrupado por día, con filtros.
// Tocar un evento abre EventSheet (comidas editables; todo eliminable).
import { useState, useEffect } from 'react'
import { apiGet } from '../api.js'
import { SANS } from '../theme.js'
import { useLang } from '../i18n.jsx'
import EventSheet, { META, dayLabel } from './EventSheet.jsx'

const FILTERS = [
  { id: 'todos', key: 'hist.all' },
  { id: 'comida', key: 'hist.meals' },
  { id: 'insulina', key: 'hist.insulin' },
  { id: 'ejercicio', key: 'hist.exercise' },
  { id: 'contexto', key: 'hist.context' },
]

export default function Historial({ theme }) {
  const { t, lang } = useLang()
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [filter, setFilter] = useState('todos')
  const [sel, setSel] = useState(null)

  const load = () => {
    apiGet('/history').then(setData).catch(e => setErr(e.message))
  }
  useEffect(() => { load() }, [])

  if (err) return <Centered theme={theme}>{t('hist.loadError')}</Centered>
  if (!data) return <Centered theme={theme}>{t('common.loading')}</Centered>

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
              {t(f.key)}
            </button>
          )
        })}
      </div>

      {groups.length === 0 ? (
        <Centered theme={theme}>{t('hist.empty')}</Centered>
      ) : groups.map(g => (
        <div key={g.date} style={{ marginBottom: 18 }}>
          <div style={{ fontSize: 11, letterSpacing: '0.14em', textTransform: 'uppercase', color: theme.inkFaint, marginBottom: 8, paddingLeft: 2 }}>
            {dayLabel(g.date, t, lang)}
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

      {sel && <EventSheet theme={theme} ev={sel} onClose={() => setSel(null)} onChanged={() => { setSel(null); load() }}/>}
    </div>
  )
}

function Centered({ theme, children }) {
  return <div style={{ padding: '40px 20px', textAlign: 'center', color: theme.inkSoft, fontSize: 14, fontFamily: SANS }}>{children}</div>
}
