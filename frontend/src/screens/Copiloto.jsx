// Copiloto.jsx — chat que SOLO explica y acompaña (nunca recomienda ni predice).
// El candado real vive en el system prompt del backend (/api/copilot/chat).
import { useState, useRef, useEffect } from 'react'
import { apiPost } from '../api.js'
import { PAL, SANS } from '../theme.js'
import NebulaGuide from '../components/NebulaGuide.jsx'

const GREETING = 'Hola 👋 Soy tu copiloto. Puedo explicarte tus datos y acompañarte. Para dosis o decisiones médicas, siempre tu equipo de salud. ¿Qué querés saber?'

// Preguntas sugeridas (estilo del diseño) — SOLO explicativas/de acompañamiento.
// Se evitan a propósito las que piden juicio o recomendación ("¿está bien?",
// "ideas para mi comida"), por el candado del copiloto.
const SUGGESTIONS = [
  '¿Por qué subí anoche?',
  '¿Cómo estuvo mi semana?',
  '¿Cómo viene mi día?',
  'Explicame mi tiempo en rango',
]

export default function Copiloto({ theme }) {
  const [messages, setMessages] = useState([{ role: 'assistant', content: GREETING }])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const endRef = useRef(null)

  useEffect(() => {
    if (messages.length > 1 || sending) endRef.current && endRef.current.scrollIntoView({ behavior: 'smooth' })
  }, [messages, sending])

  const send = async (textArg) => {
    const text = (typeof textArg === 'string' ? textArg : input).trim()
    if (!text || sending) return
    const history = messages.map(m => ({ role: m.role, content: m.content }))
    setMessages(m => [...m, { role: 'user', content: text }])
    setInput(''); setSending(true)
    try {
      const r = await apiPost('/chat', { message: text, history })
      setMessages(m => [...m, { role: 'assistant', content: r.reply || '…' }])
    } catch (e) {
      setMessages(m => [...m, { role: 'assistant', content: 'No pude responder ahora. Probá de nuevo.' }])
    } finally {
      setSending(false)
    }
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', fontFamily: SANS }}>
      {/* mensajes */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '8px 18px 8px', display: 'flex', flexDirection: 'column', gap: 14 }}>
        {messages.map((m, i) => (
          <Bubble key={i} theme={theme} role={m.role} text={m.content}/>
        ))}
        {sending && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: theme.inkFaint, fontSize: 12.5, paddingLeft: 44 }}>
            <div className="ai-orbit" style={{ width: 14, height: 14, borderRadius: '50%', border: `2px solid ${theme.accent}44`, borderTopColor: theme.accent }}/>
            pensando…
          </div>
        )}
        <div ref={endRef}/>
      </div>

      {/* preguntas sugeridas (al empezar) */}
      {messages.length === 1 && !sending && (
        <div style={{ flexShrink: 0, display: 'flex', gap: 8, overflowX: 'auto', padding: '4px 16px 2px' }}>
          {SUGGESTIONS.map((s, i) => (
            <button key={i} onClick={() => send(s)} style={{
              flexShrink: 0, padding: '9px 14px', borderRadius: 100, cursor: 'pointer', fontFamily: SANS, fontSize: 13,
              background: theme.surface, color: theme.inkSoft, border: `0.5px solid ${theme.border}`, whiteSpace: 'nowrap' }}>
              {s}
            </button>
          ))}
        </div>
      )}

      {/* barra de entrada (sobre la nav) */}
      <div style={{ flexShrink: 0, padding: '10px 16px', marginBottom: 'calc(92px + env(safe-area-inset-bottom))', display: 'flex', alignItems: 'flex-end', gap: 10 }}>
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
          placeholder="Preguntá sobre tus datos…"
          rows={1}
          style={{
            flex: 1, resize: 'none', maxHeight: 100, padding: '12px 14px', borderRadius: 18, fontSize: 15,
            fontFamily: SANS, lineHeight: 1.4, background: theme.surface, border: `0.5px solid ${theme.border}`,
            color: theme.ink, outline: 'none' }}/>
        <button onClick={send} disabled={sending || !input.trim()} style={{
          width: 44, height: 44, borderRadius: '50%', flexShrink: 0, border: 'none',
          background: input.trim() ? theme.accent : theme.surface, color: '#0A0C1E',
          cursor: input.trim() ? 'pointer' : 'default', display: 'grid', placeItems: 'center', transition: 'background 0.2s' }}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke={input.trim() ? '#0A0C1E' : theme.inkFaint} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 19V5M5 12l7-7 7 7"/>
          </svg>
        </button>
      </div>
    </div>
  )
}

function Bubble({ theme, role, text }) {
  const isUser = role === 'user'
  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexDirection: isUser ? 'row-reverse' : 'row' }}>
      {!isUser && (
        <div style={{ flexShrink: 0, width: 34, height: 34, marginBottom: 2 }}>
          <NebulaGuide kind="insulina" size={34} breathe={false}/>
        </div>
      )}
      <div style={{
        maxWidth: '78%', padding: '11px 14px', borderRadius: 18, fontSize: 14.5, lineHeight: 1.5,
        background: isUser ? theme.accent : theme.surface,
        color: isUser ? '#0A0C1E' : theme.ink,
        borderBottomRightRadius: isUser ? 6 : 18, borderBottomLeftRadius: isUser ? 18 : 6,
        border: isUser ? 'none' : `0.5px solid ${theme.border}`, whiteSpace: 'pre-wrap' }}>
        {text}
      </div>
    </div>
  )
}
