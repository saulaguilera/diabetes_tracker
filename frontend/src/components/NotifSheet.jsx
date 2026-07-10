// NotifSheet.jsx — campanita del header: notificaciones in-app de Orbit
// («🧠 Orbit encontró algo» cuando el detector encuentra un patrón nuevo).
// Al abrir se listan y se marcan leídas. Portal a body (como BriefSheet).
import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { apiGet, apiPost } from '../api.js'
import { SANS } from '../theme.js'
import { useLang } from '../i18n.jsx'
import { useSheetClose, backdropAnim, sheetAnim, Loading } from './ui.jsx'

function fmtTime(iso, lang) {
  try {
    const d = new Date(iso)
    const hoy = new Date()
    const esHoy = d.toDateString() === hoy.toDateString()
    if (esHoy) return d.toLocaleTimeString(lang, { hour: '2-digit', minute: '2-digit' })
    return d.toLocaleDateString(lang, { day: '2-digit', month: '2-digit' })
  } catch { return '' }
}

export default function NotifSheet({ theme, onClose }) {
  const { t, lang } = useLang()
  const [data, setData] = useState(null)
  const [err, setErr] = useState(false)
  const [closing, requestClose] = useSheetClose(onClose)

  useEffect(() => {
    let alive = true
    apiGet('/notifications')
      .then(d => {
        if (!alive) return
        setData(d)
        // marcarlas leídas apenas se ven (la campanita se apaga)
        if (d.unread > 0) apiPost('/notifications/read', {}).catch(() => {})
      })
      .catch(() => { if (alive) setErr(true) })
    return () => { alive = false }
  }, [])

  const items = (data && data.notifications) || []

  return createPortal((
    <div onClick={requestClose} style={{ position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(0,0,0,0.5)',
      display: 'flex', alignItems: 'flex-end', justifyContent: 'center', animation: backdropAnim(closing) }}>
      <div onClick={e => e.stopPropagation()} style={{ width: '100%', maxWidth: 460, background: theme.dark ? '#0E1426' : '#fff',
        borderTopLeftRadius: 26, borderTopRightRadius: 26, padding: '22px 22px calc(28px + env(safe-area-inset-bottom))',
        animation: sheetAnim(closing), maxHeight: '82%', overflowY: 'auto', fontFamily: SANS }}>
        <div style={{ width: 38, height: 4, borderRadius: 2, background: theme.border, margin: '0 auto 18px' }}/>

        <div style={{ color: theme.ink, fontSize: 20, fontWeight: 500, marginBottom: 14 }}>{t('notif.title')}</div>

        {!data && !err && <div style={{ padding: '26px 0' }}><Loading theme={theme} label={t('notif.loading')}/></div>}
        {err && <div style={{ padding: '22px 0', color: theme.inkSoft, fontSize: 14 }}>{t('notif.loadError')}</div>}

        {data && items.length === 0 && (
          <div style={{ color: theme.inkSoft, fontSize: 14, lineHeight: 1.55, padding: '6px 2px 14px' }}>
            {t('notif.empty')}
          </div>
        )}

        {items.map(n => (
          <div key={n.id} style={{ display: 'flex', gap: 12, alignItems: 'flex-start',
            padding: '13px 0', borderTop: `0.5px solid ${theme.border}` }}>
            {/* punto de no-leída */}
            <div style={{ flexShrink: 0, width: 8, height: 8, borderRadius: 4, marginTop: 6,
              background: n.read ? 'transparent' : theme.accent }}/>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ color: theme.ink, fontSize: 14.5, fontWeight: 600 }}>{n.title}</div>
              {n.body && <div style={{ color: theme.inkSoft, fontSize: 13.5, lineHeight: 1.5, marginTop: 3 }}>{n.body}</div>}
            </div>
            <div style={{ flexShrink: 0, color: theme.inkFaint, fontSize: 11.5, marginTop: 2 }}>{fmtTime(n.time, lang)}</div>
          </div>
        ))}
      </div>
    </div>
  ), document.body)
}
