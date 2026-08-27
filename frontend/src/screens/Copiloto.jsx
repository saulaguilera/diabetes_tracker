// Copiloto.jsx — chat que SOLO explica y acompaña (nunca recomienda ni predice).
// El candado real vive en el system prompt del backend (/api/copilot/chat).
import { useState, useRef, useEffect } from 'react'
import { apiPost, apiGet, apiStream } from '../api.js'
import { PAL, SANS } from '../theme.js'
import { useLang } from '../i18n.jsx'
import { Typewriter } from '../components/ui.jsx'
import CopilotAvatar from '../components/CopilotAvatar.jsx'

// foto → dataURL comprimido (mismo criterio que Registro: 1280px)
function fotoADataURL(file, max = 1280, quality = 0.8) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = reject
    reader.onload = () => {
      const img = new Image()
      img.onerror = reject
      img.onload = () => {
        const scale = Math.min(1, max / Math.max(img.width, img.height))
        const canvas = document.createElement('canvas')
        canvas.width = Math.round(img.width * scale)
        canvas.height = Math.round(img.height * scale)
        canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height)
        resolve(canvas.toDataURL('image/jpeg', quality))
      }
      img.src = reader.result
    }
    reader.readAsDataURL(file)
  })
}

const SUGG_KEYS = ['cop.s1', 'cop.s2', 'cop.s3', 'cop.s4', 'cop.s5']

// La conversación se guarda en localStorage y dura 24h: sobrevive a cambiar de
// pestaña, y al día siguiente arranca una nueva.
const CHAT_KEY = 'orbit_chat_v1'
const DAY_MS = 24 * 60 * 60 * 1000

function loadChat() {
  try {
    const saved = JSON.parse(localStorage.getItem(CHAT_KEY) || 'null')
    if (saved && Array.isArray(saved.messages) && saved.messages.length &&
        Date.now() - saved.startedAt < DAY_MS) {
      return saved
    }
  } catch {}
  return null
}

export default function Copiloto({ theme }) {
  const { t } = useLang()
  const startedAtRef = useRef(0)
  // saludo cálido con nombre: "Hola, Saúl 💙 …" — el nombre se cachea para
  // que esté al instante; 3 variantes rotan en cada chat nuevo
  const greetingFor = (name) => {
    const n = 1 + Math.floor(Math.random() * 3)
    return t(`cop.greet${n}`).replace('{name}', name ? `, ${name}` : '')
  }
  const cachedName = () => {
    try { return localStorage.getItem('orbit_user_name') || '' } catch { return '' }
  }
  const [messages, setMessages] = useState(() => {
    const saved = loadChat()
    if (saved) { startedAtRef.current = saved.startedAt; return saved.messages }
    startedAtRef.current = Date.now()
    return [{ role: 'assistant', content: greetingFor(cachedName()) }]
  })
  // primera visita sin nombre cacheado: al llegar el perfil, si el chat sigue
  // siendo solo el saludo, se personaliza en el momento
  useEffect(() => {
    apiGet('/profile').then(p => {
      const nombre = ((p && p.name) || '').trim().split(' ')[0]
      if (!nombre) return
      const habiaCache = !!cachedName()   // ANTES de guardar, o nunca personaliza
      try { localStorage.setItem('orbit_user_name', nombre) } catch {}
      setMessages(m => (m.length === 1 && m[0].role === 'assistant' && !habiaCache)
        ? [{ role: 'assistant', content: greetingFor(nombre) }] : m)
    }).catch(() => {})
  }, [])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [kb, setKb] = useState(0)   // alto del teclado (visualViewport)
  const [foto, setFoto] = useState(null)      // dataURL adjunto, pendiente de enviar
  const fotoRef = useRef(null)
  const listRef = useRef(null)

  // persistir la conversación (no guardamos solo el saludo; sin el flag de animación)
  useEffect(() => {
    try {
      if (messages.some(m => m.streaming)) return   // parciales no se persisten
      if (messages.length > 1) {
        const clean = messages.map(({ justArrived, img, streaming, sid, ...rest }) => rest)
        localStorage.setItem(CHAT_KEY, JSON.stringify({ startedAt: startedAtRef.current, messages: clean }))
      }
    } catch {}
  }, [messages])

  const scrollToBottom = () => { const el = listRef.current; if (el) el.scrollTop = el.scrollHeight }

  // empezar una conversación nueva (manual o cuando venció el día)
  const resetChat = () => {
    try { localStorage.removeItem(CHAT_KEY) } catch {}
    startedAtRef.current = Date.now()
    setMessages([{ role: 'assistant', content: greetingFor(cachedName()) }])
  }

  // Sube el input con el teclado en vez de empujar toda la pantalla.
  useEffect(() => {
    const vv = window.visualViewport
    if (!vv) return
    const onVV = () => {
      const h = Math.max(0, window.innerHeight - vv.height - vv.offsetTop)
      setKb(h > 110 ? h : 0)
    }
    vv.addEventListener('resize', onVV)
    vv.addEventListener('scroll', onVV)
    return () => { vv.removeEventListener('resize', onVV); vv.removeEventListener('scroll', onVV) }
  }, [])

  // Scrollea SOLO la lista por dentro (no scrollIntoView, que puede mover la
  // página entera y hacer que "suba todo").
  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, sending, kb])

  const send = async (textArg) => {
    const text = (typeof textArg === 'string' ? textArg : input).trim()
    const img = foto
    if ((!text && !img) || sending) return
    const history = messages.map(m => ({ role: m.role, content: m.content }))
    setMessages(m => [...m, { role: 'user', content: text || '📷', img }])
    setInput(''); setFoto(null); setSending(true)
    const body = { message: text, history, ...(img ? { image: img } : {}) }

    // ── streaming: la respuesta aparece en vivo ──
    // El fallback al endpoint clásico SOLO corre si el stream murió antes del
    // primer evento: si ya hubo eventos, el servidor pudo haber ejecutado
    // herramientas de ESCRITURA (registrar comida/insulina) y reintentar
    // duplicaría registros en una app de salud. En ese caso: error honesto.
    const sid = Date.now() + Math.random()          // identidad de la burbuja
    let huboEventos = false
    const quitaBurbuja = (m) => m.filter(x => x.sid !== sid)
    // esta red no deja fluir streams → directo al clásico (jamás colgarse)
    if (sseOkRef.current === false) {
      try {
        const r = await apiPost('/chat', body)
        setMessages(m => [...m, { role: 'assistant', content: r.reply || '…',
          usedData: (r.used_data || []).length > 0, justArrived: true,
          followups: r.followups || [] }])
      } catch (e) {
        setMessages(m => [...m, { role: 'assistant', content: t('cop.error'), justArrived: true }])
      } finally { setSending(false) }
      return
    }
    const ctrl = new AbortController()
    let ultimoEvento = Date.now()
    // sin señales de vida por 60s → abortar y avisar (nada de spinner eterno)
    const vigilante = setInterval(() => {
      if (Date.now() - ultimoEvento > 60000) { try { ctrl.abort() } catch {} }
    }, 5000)
    try {
      const res = await apiStream('/chat/stream', body, ctrl.signal)
      const reader = res.body.getReader()
      const dec = new TextDecoder()
      let buf = '', acc = '', started = false, terminado = false
      const visiblePara = (texto) => {
        // holdback de la línea técnica de chips, incluso a medio llegar
        const corte = texto.indexOf('\n>>>')
        let vis = corte >= 0 ? texto.slice(0, corte) : texto
        return vis.replace(/\n?>{1,3}\s*$/, '')   // '>' o '>>' colgando al final
      }
      const pinta = (texto) => {
        const visible = visiblePara(texto)
        setMessages(m => started
          ? m.map(x => x.sid === sid ? { ...x, content: visible } : x)
          : [...m, { sid, role: 'assistant', content: visible, streaming: true }])
        started = true
      }
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const lineas = buf.split('\n\n')
        buf = lineas.pop()
        for (const l of lineas) {
          if (!l.startsWith('data: ')) continue
          const ev = JSON.parse(l.slice(6))
          huboEventos = true
          ultimoEvento = Date.now()
          if (ev.type === 'ping') continue
          if (ev.type === 'delta') { acc += ev.t; pinta(acc) }
          else if (ev.type === 'rollback') {
            // era una ronda de consultas: quitar la burbuja parcial, no dejarla vacía
            acc = ''
            if (started) { setMessages(quitaBurbuja); started = false }
          }
          else if (ev.type === 'status') setSlowThinking(ev.fase === 'consultando')
          else if (ev.type === 'error') throw new Error('stream error')
          else if (ev.type === 'done') {
            terminado = true
            const final = { role: 'assistant', content: ev.reply || '…',
              usedData: (ev.used_data || []).length > 0,
              followups: ev.followups || [] }
            setMessages(m => started
              ? m.map(x => x.sid === sid ? final : x)
              : [...m, final])
          }
        }
      }
      if (!terminado) throw new Error('stream incompleto')
      clearInterval(vigilante)
      setSending(false)
      return
    } catch (e) {
      clearInterval(vigilante)
      setMessages(quitaBurbuja)
      if (huboEventos) {
        // el servidor ya pudo actuar: no reintentar solo (evita duplicados)
        setMessages(m => [...m, { role: 'assistant', content: t('cop.error'), justArrived: true }])
        setSending(false)
        return
      }
    }

    // fallback: el stream nunca arrancó (red/proxy sin SSE) → endpoint clásico
    try {
      const r = await apiPost('/chat', body)
      setMessages(m => [...m, { role: 'assistant', content: r.reply || '…',
        usedData: (r.used_data || []).length > 0, justArrived: true,
        followups: r.followups || [] }])
    } catch (e) {
      setMessages(m => [...m, { role: 'assistant', content: t('cop.error'), justArrived: true }])
    } finally {
      setSending(false)
    }
  }

  // sondeo de streaming (una vez por sesión): si esta red bufferea SSE
  // (proxies de hotel/corporativos), el chat usa el endpoint clásico —
  // más lento de ver, pero jamás se cuelga ni duplica registros
  const sseOkRef = useRef(null)
  useEffect(() => {
    try {
      const cached = sessionStorage.getItem('orbit_sse')
      if (cached) { sseOkRef.current = cached === 'ok'; return }
    } catch {}
    ;(async () => {
      try {
        const ctrl = new AbortController()
        const timer = setTimeout(() => ctrl.abort(), 4000)
        const res = await apiStream('/stream-check', {}, ctrl.signal)
        const reader = res.body.getReader()
        const t0 = Date.now()
        const dec = new TextDecoder()
        let buf = ''
        while (Date.now() - t0 < 3500) {
          const { done, value } = await reader.read()
          if (done) break
          buf += dec.decode(value, { stream: true })
          if (buf.includes('"n": 1')) break     // el primer ping fluyó a tiempo
        }
        clearTimeout(timer)
        try { ctrl.abort() } catch {}
        sseOkRef.current = buf.includes('"n": 1')
      } catch { sseOkRef.current = false }
      try { sessionStorage.setItem('orbit_sse', sseOkRef.current ? 'ok' : 'no') } catch {}
    })()
  }, [])

  // ¿quedó un patrón esperando? → el copiloto lo cuenta él mismo al entrar
  useEffect(() => {
    apiPost('/chat/pending', {}).then(r => {
      if (r && r.pending && r.pending.cuerpo) {
        setMessages(m => [...m, { role: 'assistant', justArrived: true,
          content: `🧠 ${t('cop.foundIntro')} ${r.pending.cuerpo}`,
          followups: [t('cop.f1'), t('cop.f2')] }])
      }
    }).catch(() => {})
  }, [])

  // el análisis con consultas tarda más que un saludo → avisar qué está pasando
  const [slowThinking, setSlowThinking] = useState(false)
  useEffect(() => {
    if (!sending) { setSlowThinking(false); return }
    const id = setTimeout(() => setSlowThinking(true), 2500)
    return () => clearTimeout(id)
  }, [sending])

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', fontFamily: SANS }}>
      {/* nueva conversación — sutil, arriba a la derecha, cuando hay charla activa */}
      {messages.length > 1 && (
        <button onClick={resetChat} title={t('cop.newChat')} style={{
          position: 'absolute', top: 'calc(56px + env(safe-area-inset-top))', right: 16, zIndex: 5,
          display: 'flex', alignItems: 'center', gap: 5, padding: '6px 12px', borderRadius: 100, cursor: 'pointer',
          background: theme.surface, color: theme.inkSoft, border: `0.5px solid ${theme.border}`,
          fontFamily: SANS, fontSize: 12 }}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>
          </svg>
          {t('cop.newChat')}
        </button>
      )}
      {/* mensajes */}
      <div ref={listRef} style={{ flex: 1, overflowY: 'auto', overscrollBehavior: 'contain', padding: '8px 18px 8px', display: 'flex', flexDirection: 'column', gap: 14 }}>
        {messages.map((m, i) => (
          <Bubble key={i} theme={theme} role={m.role} text={m.content} usedData={m.usedData}
            img={m.img} animate={m.justArrived} onScroll={scrollToBottom}/>
        ))}
        {sending && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: theme.inkFaint, fontSize: 12.5, paddingLeft: 44 }}>
            <div className="ai-orbit" style={{ width: 14, height: 14, borderRadius: '50%', border: `2px solid ${theme.accent}44`, borderTopColor: theme.accent }}/>
            {slowThinking ? t('cop.analyzing') : t('cop.thinking')}
          </div>
        )}
      </div>

      {/* chips: seguimientos de la última respuesta, o sugerencias al empezar */}
      {!sending && (() => {
        const last = messages[messages.length - 1]
        const chips = (last && last.role === 'assistant' && last.followups && last.followups.length > 0)
          ? last.followups
          : (messages.length === 1 ? SUGG_KEYS.map(k => t(k)) : [])
        if (!chips.length) return null
        return (
          <div style={{ flexShrink: 0, display: 'flex', gap: 8, overflowX: 'auto', padding: '4px 16px 2px' }}>
            {chips.map((c, i) => (
              <button key={i} onClick={() => send(c)} style={{
                flexShrink: 0, padding: '9px 14px', borderRadius: 100, cursor: 'pointer', fontFamily: SANS, fontSize: 13,
                background: theme.surface, color: theme.inkSoft, border: `0.5px solid ${theme.border}`, whiteSpace: 'nowrap' }}>
                {c}
              </button>
            ))}
          </div>
        )
      })()}

      {/* foto adjunta, esperando el mensaje */}
      {foto && (
        <div style={{ flexShrink: 0, padding: '0 16px 6px', display: 'flex', alignItems: 'center', gap: 10 }}>
          <img src={foto} alt="" style={{ width: 52, height: 52, borderRadius: 10, objectFit: 'cover' }}/>
          <span style={{ fontSize: 12, color: theme.inkSoft, flex: 1 }}>{t('cop.photoReady')}</span>
          <button onClick={() => setFoto(null)} style={{ background: 'none', border: 'none',
            color: theme.inkFaint, fontSize: 18, cursor: 'pointer', padding: 4 }}>✕</button>
        </div>
      )}

      {/* barra de entrada (sobre la nav) */}
      <div style={{ flexShrink: 0, padding: '10px 16px', display: 'flex', alignItems: 'flex-end', gap: 10,
        marginBottom: kb > 0 ? kb + 8 : 'calc(92px + env(safe-area-inset-bottom))',
        transition: 'margin-bottom 0.2s ease' }}>
        <input ref={fotoRef} type="file" accept="image/*" capture="environment"
          style={{ display: 'none' }}
          onChange={async e => {
            const f = e.target.files && e.target.files[0]
            if (f) { try { setFoto(await fotoADataURL(f)) } catch {} }
            if (fotoRef.current) fotoRef.current.value = ''
          }}/>
        <button onClick={() => fotoRef.current && fotoRef.current.click()} disabled={sending} style={{
          width: 44, height: 44, borderRadius: '50%', flexShrink: 0, cursor: 'pointer',
          background: theme.surface, border: `0.5px solid ${theme.border}`,
          display: 'grid', placeItems: 'center' }}>
          <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke={foto ? theme.accent : theme.inkSoft}
            strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
            <circle cx="12" cy="13" r="4"/>
          </svg>
        </button>
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
          placeholder={t('cop.placeholder')}
          rows={1}
          style={{
            flex: 1, resize: 'none', maxHeight: 100, padding: '12px 14px', borderRadius: 18, fontSize: 15,
            fontFamily: SANS, lineHeight: 1.4, background: theme.surface, border: `0.5px solid ${theme.border}`,
            color: theme.ink, outline: 'none' }}/>
        <button onClick={send} disabled={sending || (!input.trim() && !foto)} style={{
          width: 44, height: 44, borderRadius: '50%', flexShrink: 0, border: 'none',
          background: (input.trim() || foto) ? theme.accent : theme.surface, color: '#0A0C1E',
          cursor: (input.trim() || foto) ? 'pointer' : 'default', display: 'grid', placeItems: 'center', transition: 'background 0.2s' }}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke={(input.trim() || foto) ? '#0A0C1E' : theme.inkFaint} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 19V5M5 12l7-7 7 7"/>
          </svg>
        </button>
      </div>
    </div>
  )
}

function Bubble({ theme, role, text, usedData, img, animate, onScroll }) {
  const { t } = useLang()
  const isUser = role === 'user'
  return (
    <div className="msg-in" style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexDirection: isUser ? 'row-reverse' : 'row' }}>
      {!isUser && (
        <div style={{ flexShrink: 0, width: 34, height: 34, marginBottom: 2 }}>
          <CopilotAvatar size={34}/>
        </div>
      )}
      <div style={{ maxWidth: '78%' }}>
        <div style={{
          padding: '11px 14px', borderRadius: 18, fontSize: 14.5, lineHeight: 1.5,
          background: isUser ? theme.accent : theme.surface,
          color: isUser ? '#0A0C1E' : theme.ink,
          borderBottomRightRadius: isUser ? 6 : 18, borderBottomLeftRadius: isUser ? 18 : 6,
          border: isUser ? 'none' : `0.5px solid ${theme.border}`, whiteSpace: 'pre-wrap' }}>
          {img && <img src={img} alt="" style={{ maxWidth: '100%', borderRadius: 12,
            marginBottom: text && text !== '📷' ? 8 : 0, display: 'block' }}/>}
          {/* el copiloto "escribe" su respuesta recién llegada; lo demás va directo */}
          {animate && !isUser
            ? <Typewriter text={text} onProgress={onScroll} onDone={onScroll} total={2000}/>
            : text}
        </div>
        {/* transparencia: esta respuesta salió de consultar tus datos reales */}
        {usedData && !isUser && (
          <div style={{ color: theme.inkFaint, fontSize: 10.5, marginTop: 4, paddingLeft: 6,
            letterSpacing: '0.06em' }}>{t('cop.basedOnData')}</div>
        )}
      </div>
    </div>
  )
}
