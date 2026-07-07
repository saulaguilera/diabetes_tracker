// EventSheet.jsx — hoja de detalle de un evento (comida/insulina/ejercicio).
// Comidas editables; todo eliminable. La usan Historial y Hoy (actividad reciente).
// Se renderiza con portal a document.body para que quede por encima de la nav
// (la transición de secciones usa transform → crea un stacking context que la
// atraparía debajo).
import { useState } from 'react'
import { createPortal } from 'react-dom'
import { apiPut, apiDelete } from '../api.js'
import { PAL, SANS } from '../theme.js'
import { useLang } from '../i18n.jsx'
import { Stepper, Field, useSheetClose, backdropAnim, sheetAnim } from './ui.jsx'

export const META = {
  comida:    { color: PAL.metabolismo.key, label: 'Comida' },
  insulina:  { color: PAL.insulina.key,    label: 'Insulina' },
  ejercicio: { color: PAL.glucosa.key,     label: 'Ejercicio' },
  contexto:  { color: PAL.ritmo.key,       label: 'Contexto' },
}

export function dayLabel(dateStr, t, lang = 'es') {
  if (!dateStr) return ''
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const d = new Date(dateStr + 'T00:00:00')
  const diff = Math.round((today - d) / 86400000)
  if (diff === 0) return t ? t('common.today') : 'Hoy'
  if (diff === 1) return t ? t('common.yesterday') : 'Ayer'
  return d.toLocaleDateString(lang, { day: 'numeric', month: 'short' })
}

export default function EventSheet({ theme, ev, onClose, onChanged }) {
  const { t, lang } = useLang()
  const isMeal = ev.cat === 'comida'
  const d = ev.data || {}
  const m = META[ev.cat] || { color: theme.accent, label: ev.cat }
  const [name, setName] = useState(d.name || '')
  const [carbs, setCarbs] = useState(Math.round(d.carbs || 0))
  const [protein, setProtein] = useState(Math.round(d.protein || 0))
  const [fat, setFat] = useState(Math.round(d.fat || 0))
  // fecha/hora editables: muchas veces la comida se registra tarde y el
  // horario real importa para entender la respuesta glucémica
  const [date, setDate] = useState(ev.date || '')
  const [time, setTime] = useState(ev.time || '')
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState(false)   // paso "¿seguro?" antes de borrar

  const noun = isMeal ? t('ev.noun.meal')
    : ev.cat === 'insulina' ? t('ev.noun.insulin')
    : ev.cat === 'contexto' ? t('ev.noun.context')
    : t('ev.noun.exercise')

  const dateTimeChanged = (date && date !== ev.date) || (time && time !== ev.time)

  const save = async () => {
    setBusy(true)
    try {
      if (isMeal) {
        const body = { name, carbs, protein, fat }
        if (date && date !== ev.date) body.date = date
        if (time && time !== ev.time) body.time = time
        await apiPut(`/meal/${ev.id}`, body)
      } else {
        // insulina / ejercicio / contexto: solo fecha/hora editables
        await apiPut(`/entry/${ev.cat}/${ev.id}`, { date, time })
      }
      onChanged()
    } catch (e) { setBusy(false) }
  }
  const remove = async () => {
    setBusy(true)
    try { await apiDelete(`/entry/${ev.cat}/${ev.id}`); onChanged() }
    catch (e) { setBusy(false) }
  }

  const [closing, requestClose] = useSheetClose(onClose)

  return createPortal((
    <div onClick={requestClose} style={{ position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(0,0,0,0.5)',
      display: 'flex', alignItems: 'flex-end', justifyContent: 'center', animation: backdropAnim(closing) }}>
      <div onClick={e => e.stopPropagation()} style={{ width: '100%', maxWidth: 460, background: theme.dark ? '#0E1426' : '#fff',
        borderTopLeftRadius: 26, borderTopRightRadius: 26, padding: '22px 22px calc(28px + env(safe-area-inset-bottom))',
        animation: sheetAnim(closing), maxHeight: '85%', overflowY: 'auto' }}>
        <div style={{ width: 38, height: 4, borderRadius: 2, background: theme.border, margin: '0 auto 18px' }}/>

        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 18 }}>
          <span style={{ width: 9, height: 9, borderRadius: '50%', background: m.color, boxShadow: `0 0 8px ${m.color}` }}/>
          <span style={{ flex: 1, color: theme.ink, fontSize: 18, fontWeight: 500 }}>{isMeal ? t('ev.editMeal') : ev.title}</span>
          <span style={{ color: theme.inkFaint, fontSize: 13 }}>{[dayLabel(ev.date, t, lang), ev.time].filter(Boolean).join(' · ')}</span>
        </div>

        {isMeal ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <Field theme={theme} value={name} onChange={setName} placeholder={t('reg.whatAte')}/>
            {/* ingredientes (si la comida se registró con desglose de foto) */}
            {d.components && d.components.length > 0 && (
              <div style={{ padding: '10px 12px', borderRadius: 12,
                background: theme.dark ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.03)',
                border: `0.5px solid ${theme.border}` }}>
                <div style={{ color: theme.inkFaint, fontSize: 10.5, letterSpacing: '0.12em',
                  textTransform: 'uppercase', marginBottom: 5 }}>{t('ev.ingredients')}</div>
                {d.components.map((cp, i) => (
                  <div key={i} style={{ display: 'flex', gap: 8, fontSize: 12.5, padding: '2px 0', color: theme.inkSoft }}>
                    <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{cp.name}</span>
                    {cp.grams != null && <span style={{ color: theme.inkFaint }}>{Math.round(cp.grams)}g</span>}
                    <span style={{ color: m.color, fontWeight: 600 }}>{cp.carbs}g CH</span>
                  </div>
                ))}
              </div>
            )}
            <Row theme={theme} label={t('reg.carbs')}><Stepper theme={theme} value={carbs} setValue={setCarbs} step={5} max={300} unit="g" color={m.color}/></Row>
            <Row theme={theme} label={t('reg.protein')}><Stepper theme={theme} value={protein} setValue={setProtein} step={5} max={200} unit="g" color={theme.ink}/></Row>
            <Row theme={theme} label={t('reg.fat')}><Stepper theme={theme} value={fat} setValue={setFat} step={5} max={200} unit="g" color={theme.ink}/></Row>
            {/* ¿registraste tarde? corregí cuándo comiste en realidad */}
            <Row theme={theme} label={t('ev.day')}>
              <input type="date" value={date} max={new Date().toISOString().slice(0, 10)}
                onChange={e => setDate(e.target.value)} style={timeInput(theme)}/>
            </Row>
            <Row theme={theme} label={t('ev.time')}>
              <input type="time" value={time} onChange={e => setTime(e.target.value)} style={timeInput(theme)}/>
            </Row>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            {ev.cat === 'insulina' && <>
              <Info theme={theme} k={t('ev.units')} v={`${d.units}U`}/>
              <Info theme={theme} k={t('ev.type')} v={d.label || d.type}/>
            </>}
            {ev.cat === 'ejercicio' && <>
              <Info theme={theme} k={t('ev.activity')} v={d.activity_type}/>
              <Info theme={theme} k={t('ev.durationLabel')} v={d.duration_min ? `${d.duration_min} min` : '—'}/>
              {d.intensity && <Info theme={theme} k={t('ev.intensity')} v={d.intensity}/>}
            </>}
            {ev.cat === 'contexto' && <>
              <Info theme={theme} k={t('ev.context')} v={ev.title}/>
              {d.notes && <Info theme={theme} k={t('ev.note')} v={d.notes}/>}
            </>}
            {/* fecha/hora editables — también se registra tarde el ejercicio, etc. */}
            <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 12 }}>
              <Row theme={theme} label={t('ev.day')}>
                <input type="date" value={date} max={new Date().toISOString().slice(0, 10)}
                  onChange={e => setDate(e.target.value)} style={timeInput(theme)}/>
              </Row>
              <Row theme={theme} label={t('ev.time')}>
                <input type="time" value={time} onChange={e => setTime(e.target.value)} style={timeInput(theme)}/>
              </Row>
            </div>
          </div>
        )}

        {confirm ? (
          <div style={{ marginTop: 22 }}>
            <div style={{ color: theme.inkSoft, fontSize: 14, lineHeight: 1.45, marginBottom: 14 }}>
              {t('ev.confirmDel', { noun })}
            </div>
            <div style={{ display: 'flex', gap: 10 }}>
              <button onClick={() => setConfirm(false)} disabled={busy} style={{ flex: 1, padding: '13px', borderRadius: 14,
                border: `0.5px solid ${theme.border}`, background: 'transparent', color: theme.inkSoft, fontSize: 15,
                fontFamily: SANS, cursor: 'pointer' }}>{t('common.cancel')}</button>
              <button onClick={remove} disabled={busy} style={{ flex: 1, padding: '13px', borderRadius: 14, border: 'none',
                background: '#D9665A', color: '#fff', fontSize: 15, fontWeight: 600, fontFamily: SANS, cursor: 'pointer', opacity: busy ? 0.6 : 1 }}>
                {busy ? t('common.deleting') : t('ev.confirmYes')}
              </button>
            </div>
          </div>
        ) : (
          <div style={{ display: 'flex', gap: 10, marginTop: 22 }}>
            <button onClick={() => setConfirm(true)} disabled={busy} style={{ padding: '13px 16px', borderRadius: 14, border: `0.5px solid ${theme.border}`,
              background: 'transparent', color: '#D98A6A', fontSize: 14, fontFamily: SANS, cursor: 'pointer' }}>{t('common.delete')}</button>
            {/* comida: siempre editable; otros: Guardar solo si cambió fecha/hora */}
            {(isMeal || dateTimeChanged) && (
              <button onClick={save} disabled={busy} style={{ flex: 1, padding: '13px', borderRadius: 14, border: 'none',
                background: m.color, color: '#0A0C1E', fontSize: 15, fontWeight: 600, fontFamily: SANS, cursor: 'pointer', opacity: busy ? 0.6 : 1 }}>
                {busy ? t('common.saving') : t('common.save')}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  ), document.body)
}

// estilo compartido de los inputs nativos de fecha/hora (dark-friendly)
function timeInput(theme) {
  return {
    padding: '9px 12px', borderRadius: 12, border: `0.5px solid ${theme.border}`,
    background: theme.dark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.04)',
    color: theme.ink, fontSize: 15, fontFamily: SANS, colorScheme: theme.dark ? 'dark' : 'light',
    WebkitAppearance: 'none', appearance: 'none', outline: 'none',
  }
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
