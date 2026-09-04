// Copiloto.jsx — chat que SOLO explica y acompaña (nunca recomienda ni predice).
// El candado real vive en el system prompt del backend (/api/copilot/chat).
import { useState, useRef, useEffect, useCallback, memo } from 'react'
import { apiPost, apiGet, apiStream } from '../api.js'
import { PAL, SANS } from '../theme.js'
import { useLang } from '../i18n.jsx'
import { Typewriter } from '../components/ui.jsx'
import CopilotAvatar from '../components/CopilotAvatar.jsx'
import GlucoseWave from '../components/GlucoseWave.jsx'

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
let chatGen = 0        // generación del chat: resetChat la sube y cualquier
                       // closure viejo (aun de otra instancia) queda inválido
let ctrlVivo = null    // stream en vuelo, para abortarlo desde resetChat
const marcaEnvio = (v) => { try { window.__orbitChatEnviando = v } catch {} }

// La respuesta puede llegar cuando el usuario ya se fue a otra pestaña (el
// componente se DESMONTA al cambiar): escribirla directo a localStorage y
// avisar — si el chat sigue montado se refresca; si no, lo verá al volver.
function persistirRespuesta(finalMsg, startedAt, avisar = true) {
  try {
    const saved = JSON.parse(localStorage.getItem(CHAT_KEY) || 'null')
    // simetría con loadChat: un blob vencido (24h) NO se hereda — la
    // respuesta tardía abre conversación nueva con startedAt fresco
    const fresco = saved && Array.isArray(saved.messages) &&
      Date.now() - saved.startedAt < DAY_MS
    const base = fresco ? saved.messages : []
    const limpios = base.filter(m => !m.streaming)
    // idempotente: si el último guardado ya es este mismo mensaje, no duplicar
    const ult = limpios[limpios.length - 1]
    const fg = (m) => (m && m.grafica && m.grafica.fecha) || ''
    if (ult && ult.role === finalMsg.role && ult.content === finalMsg.content
        && fg(ult) === fg(finalMsg)) return
    // blob vencido o inexistente: ancla FRESCA (el startedAt del closure puede
    // traer el timestamp viejo >24h y el blob nacería ya purgable)
    guardarChat(fresco ? saved.startedAt : Date.now(), [...limpios, finalMsg])
    // el aviso es SOLO para respuestas: avisar al guardar el mensaje del
    // usuario disparaba la recogida a mitad del envío y duplicaba burbujas
    if (avisar) window.dispatchEvent(new Event('orbit-chat-update'))
  } catch {}
}

// chats viejos pueden traer duplicados de la carrera corregida: sanear
// (dos respuestas de texto idéntico sobre DÍAS distintos no se colapsan)
function dedupeConsecutivos(msgs) {
  const fg = (m) => (m.grafica && m.grafica.fecha) || ''
  return msgs.filter((m, i) => !(i > 0 && msgs[i - 1].role === m.role
    && msgs[i - 1].content === m.content && fg(msgs[i - 1]) === fg(m)))
}

// las gráficas pesan (~5KB c/u): conservarlas solo en los 3 mensajes más
// recientes que traigan una; los viejos degradan a solo-texto
function podarCharts(msgs) {
  let quedan = 3
  const out = []
  for (let i = msgs.length - 1; i >= 0; i--) {
    const m = msgs[i]
    if (m.grafica && quedan > 0) { quedan -= 1; out.push(m) }
    else if (m.grafica) { const { grafica, ...rest } = m; out.push(rest) }
    else out.push(m)
  }
  return out.reverse()
}

// única puerta de escritura del chat: poda gráficas y, si el storage está
// lleno, reintenta sin ninguna — el texto siempre gana la persistencia
function guardarChat(startedAt, msgs) {
  const blob = (ms) => JSON.stringify({ startedAt, messages: ms })
  try { localStorage.setItem(CHAT_KEY, blob(podarCharts(msgs))) }
  catch {
    try { localStorage.setItem(CHAT_KEY, blob(msgs.map(({ grafica, ...r }) => r))) } catch {}
  }
}
const DAY_MS = 24 * 60 * 60 * 1000

function loadChat() {
  try {
    const saved = JSON.parse(localStorage.getItem(CHAT_KEY) || 'null')
    if (saved && Array.isArray(saved.messages) && saved.messages.length) {
      if (Date.now() - saved.startedAt < DAY_MS) {
        return { ...saved, messages: dedupeConsecutivos(saved.messages) }
      }
      localStorage.removeItem(CHAT_KEY)   // vencido: purgar, no heredar
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
        // jamás sobrescribir una respuesta que otro closure persistió y este
        // estado no tiene todavía — recoger() la traerá en el próximo tick
        const saved = JSON.parse(localStorage.getItem(CHAT_KEY) || 'null')
        if (saved && Array.isArray(saved.messages) && saved.messages.length) {
          const ultG = saved.messages[saved.messages.length - 1]
          if (ultG && ultG.role === 'assistant' &&
              !clean.some(m => m.role === 'assistant' && m.content === ultG.content)) {
            return
          }
        }
        guardarChat(startedAtRef.current, clean)
      }
    } catch {}
  }, [messages])

  const scrollToBottom = useCallback(() => { const el = listRef.current; if (el) el.scrollTop = el.scrollHeight }, [])

  // empezar una conversación nueva (manual o cuando venció el día)
  const resetChat = () => {
    chatGen += 1                              // closures en vuelo: inválidos
    try { ctrlVivo?.abort() } catch {}
    ctrlVivo = null
    marcaEnvio(false)
    try { localStorage.removeItem(CHAT_KEY) } catch {}
    startedAtRef.current = Date.now()
    setMessages([{ role: 'assistant', content: greetingFor(cachedName()) }])
    setSending(false)
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
    const gen = chatGen
    const vivo = () => chatGen === gen        // ¿resetChat nos invalidó?
    marcaEnvio(true)
    const history = messages.map(m => ({ role: m.role, content: m.content }))
    const userMsg = { role: 'user', content: text || '📷', img }
    persistirRespuesta({ role: 'user', content: text || '📷' }, startedAtRef.current, false)
    setMessages(m => [...m, userMsg])
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
    // sin veredicto del sondeo aún, o red que bufferea → clásico (jamás colgarse)
    if (sseOkRef.current !== true) {
      try {
        const r = await apiPost('/chat', body)
        if (!vivo()) return
        const final = { role: 'assistant', content: r.reply || '…',
          usedData: (r.used_data || []).length > 0, justArrived: true,
          followups: r.followups || [],
          ...(r.grafica ? { grafica: r.grafica } : {}) }
        persistirRespuesta(final, startedAtRef.current)
        setMessages(m => [...m, final])
      } catch (e) {
        if (!vivo()) return
        const final = { role: 'assistant', content: t('cop.error'), justArrived: true }
        persistirRespuesta(final, startedAtRef.current)
        setMessages(m => [...m, final])
      } finally { setSending(false); marcaEnvio(false) }
      return
    }
    const ctrl = new AbortController()
    ctrlVivo = ctrl
    const tEnvio = Date.now()
    let ultimoEvento = Date.now()
    // iOS congela la página en background: al volver, el silencio acumulado
    // NO es la red — darle al stream una ventana fresca en vez de castigarlo
    const alVolver = () => { if (!document.hidden) ultimoEvento = Date.now() }
    document.addEventListener('visibilitychange', alVolver)
    // el status sale del server al instante: 15s sin NINGÚN evento = la red
    // retiene el stream → abortar, marcar la sesión como no-stream y avisar.
    // Con eventos ya fluyendo, 60s de silencio → abortar (nada de spinner eterno)
    const vigilante = setInterval(() => {
      if (document.hidden) { ultimoEvento = Date.now(); return }
      const limite = huboEventos ? 60000 : 15000
      if (Date.now() - ultimoEvento > limite) {
        if (!huboEventos) {
          sseOkRef.current = false
          try { sessionStorage.setItem('orbit_sse', 'no') } catch {}
        }
        try { ctrl.abort() } catch {}
      }
    }, 3000)
    const pararVigilante = () => {
      clearInterval(vigilante)
      document.removeEventListener('visibilitychange', alVolver)
      if (ctrlVivo === ctrl) ctrlVivo = null
    }
    try {
      const res = await apiStream('/chat/stream', body, ctrl.signal)
      const reader = res.body.getReader()
      const dec = new TextDecoder()
      let buf = '', acc = '', started = false, terminado = false
      var terminadoRef = { ok: false }   // visible en el catch (var: hoisted)
      const visiblePara = (texto) => {
        // holdback de la línea técnica de chips, incluso a medio llegar
        const corte = texto.indexOf('\n>>>')
        let vis = corte >= 0 ? texto.slice(0, corte) : texto
        return vis.replace(/\n?>{1,3}\s*$/, '')   // '>' o '>>' colgando al final
      }
      const pinta = (texto) => {
        if (!vivo()) return
        setMessages(m => started
          ? m.map(x => x.sid === sid ? { ...x, content: texto } : x)
          : [...m, { sid, role: 'assistant', content: texto, streaming: true }])
        started = true
      }

      // ── revelado fluido (tipo ChatGPT): el texto de red se acumula en
      // `objetivo` y un loop rAF lo revela con catch-up proporcional — calmo
      // al día con el stream, acelera con el backlog, jamás se rezaga. Con
      // prefers-reduced-motion apagado por completo (comportamiento clásico).
      let reduce = false
      try { reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches } catch {}
      var rafId = null                 // var: visibles en el catch (hoisted)
      var finalPendiente = null        // mensaje final esperando el drenado
      var finalizado = false
      let objetivo = '', mostrado = 0, lastTick = 0, lastPaint = 0
      var backstopId = null
      const pararSuavizado = () => { if (rafId) { cancelAnimationFrame(rafId); rafId = null } }
      const finalize = () => {
        if (finalizado) return
        finalizado = true
        if (backstopId) { clearTimeout(backstopId); backstopId = null }
        pararSuavizado()
        if (vivo() && finalPendiente) {
          const f = finalPendiente
          setMessages(m => started ? m.map(x => x.sid === sid ? f : x) : [...m, f])
        }
        setSending(false)
        marcaEnvio(false)
      }
      // el backstop se RE-ARMA con cada avance pintado: solo dispara si el
      // drenado lleva 4s sin progresar (rAF congelado en background, etc.) —
      // jamás trunca un drenado sano de una respuesta larga
      const armaBackstop = () => {
        if (backstopId) clearTimeout(backstopId)
        backstopId = setTimeout(finalize, 4000)
      }
      const tick = (ts) => {
        rafId = null
        if (!vivo() || finalizado) { if (!vivo() && finalPendiente) finalize(); return }
        const target = visiblePara(objetivo)
        if (mostrado > target.length) mostrado = target.length
        const dt = lastTick ? Math.min(0.1, (ts - lastTick) / 1000) : 0.016
        lastTick = ts
        if (mostrado < target.length) {
          const pend = target.length - mostrado
          const cps = Math.min(finalPendiente ? 2400 : 700,
                               40 + pend * (finalPendiente ? 9 : 3))
          mostrado = Math.min(target.length, mostrado + Math.max(1, cps * dt))
          let corte = Math.floor(mostrado)
          // no partir un emoji: borde en high surrogate → avanzar 1
          const cc = target.charCodeAt(corte - 1)
          if (cc >= 0xD800 && cc <= 0xDBFF) corte = Math.min(target.length, corte + 1)
          if (ts - lastPaint >= 55 || corte >= target.length) {
            lastPaint = ts
            pinta(target.slice(0, corte))
            if (finalPendiente) armaBackstop()   // hay progreso: renovar plazo
          }
        }
        if (mostrado >= target.length) {
          if (finalPendiente) { finalize(); return }
          return   // al día: dormir hasta el próximo delta
        }
        rafId = requestAnimationFrame(tick)
      }
      const arranca = () => {
        if (!rafId && !finalizado) { lastTick = 0; rafId = requestAnimationFrame(tick) }
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
          if (ev.type === 'delta') {
            acc += ev.t
            if (reduce) pinta(visiblePara(acc))
            else { objetivo = acc; arranca() }
          }
          else if (ev.type === 'rollback') {
            // era una ronda de consultas: quitar la burbuja parcial, no dejarla vacía
            acc = ''; objetivo = ''; mostrado = 0
            pararSuavizado()
            if (started) { setMessages(quitaBurbuja); started = false }
          }
          else if (ev.type === 'status') setSlowThinking(ev.fase === 'consultando')
          else if (ev.type === 'error') throw new Error('stream error')
          else if (ev.type === 'done') {
            terminado = true
            terminadoRef.ok = true
            if (vivo()) {
              const final = { role: 'assistant', content: ev.reply || '…',
                usedData: (ev.used_data || []).length > 0,
                followups: ev.followups || [],
                ...(ev.grafica ? { grafica: ev.grafica } : {}) }
              // lo irreversible PRIMERO: la verdad en storage jamás espera
              // a una animación (desmonte a mitad de drenado = cero pérdida)
              persistirRespuesta(final, startedAtRef.current)
              // el server entrega el reply .strip()eado: comparar sin el
              // whitespace inicial que los deltas sí pudieron traer
              const revelado = visiblePara(objetivo)
                .slice(0, Math.floor(mostrado)).trimStart()
              const drenable = !reduce && started && !document.hidden &&
                (ev.reply || '').startsWith(revelado)
              if (!drenable) {
                // swap directo (reduced-motion, sin texto streameado, pestaña
                // oculta, o el reply final divergió de lo revelado — jamás
                // "reescribir" texto ante los ojos de la persona)
                pararSuavizado()
                setMessages(m => started
                  ? m.map(x => x.sid === sid ? final : x)
                  : [...m, { ...final, justArrived: true }])
              } else {
                // la gráfica entra YA a la burbuja en vuelo (rise-in) mientras
                // el texto restante se termina de revelar; el swap final —con
                // chips— aterriza cuando la última palabra se posa
                if (ev.grafica) {
                  setMessages(m => m.map(x =>
                    x.sid === sid ? { ...x, grafica: ev.grafica } : x))
                }
                objetivo = ev.reply
                mostrado = revelado.length   // re-anclar en el texto ya limpio
                finalPendiente = final
                arranca()
                armaBackstop()
              }
            }
            break
          }
        }
        if (terminado) break
      }
      if (!terminado) throw new Error('stream incompleto')
      pararVigilante()
      try { ctrl.abort() } catch {}   // soltar la conexión ya respondida
      if (!finalPendiente) {          // sin drenado pendiente: liberar ya
        setSending(false)
        marcaEnvio(false)
      }
      return
    } catch (e) {
      pararVigilante()
      if ((typeof terminadoRef !== 'undefined' && terminadoRef.ok) || !vivo()) {
        // la respuesta buena ya se entregó, o resetChat nos invalidó:
        // nada de burbujas de error póstumas
        if (typeof finalPendiente !== 'undefined' && finalPendiente && !finalizado) {
          return   // el drenado sigue vivo: finalize liberará sending
        }
        try { if (rafId) cancelAnimationFrame(rafId) } catch {}
        setSending(false)
        marcaEnvio(false)
        if (typeof terminadoRef === 'undefined' || !terminadoRef.ok) setMessages(quitaBurbuja)
        return
      }
      try { if (rafId) cancelAnimationFrame(rafId) } catch {}
      setMessages(quitaBurbuja)
      // auto-reenvío SOLO si falló al toque y sin eventos (conexión muerta):
      // pasados unos segundos, el servidor pudo haber procesado (incluso
      // registrado) aunque nada nos llegara — reenviar duplicaría.
      const falloInmediato = !huboEventos && (Date.now() - tEnvio < 8000)
      if (!falloInmediato) {
        const final = { role: 'assistant', content: t('cop.error'), justArrived: true }
        persistirRespuesta(final, startedAtRef.current)
        setMessages(m => [...m, final])
        setSending(false)
        marcaEnvio(false)
        return
      }
    }

    // fallback: el stream nunca arrancó (red/proxy sin SSE) → endpoint clásico
    try {
      const r = await apiPost('/chat', body)
      if (!vivo()) return
      const final = { role: 'assistant', content: r.reply || '…',
        usedData: (r.used_data || []).length > 0, justArrived: true,
        followups: r.followups || [],
        ...(r.grafica ? { grafica: r.grafica } : {}) }
      persistirRespuesta(final, startedAtRef.current)
      setMessages(m => [...m, final])
    } catch (e) {
      if (!vivo()) return
      const final = { role: 'assistant', content: t('cop.error'), justArrived: true }
      persistirRespuesta(final, startedAtRef.current)
      setMessages(m => [...m, final])
    } finally {
      setSending(false)
      marcaEnvio(false)
    }
  }

  // remontaje (cambio de pestaña) con un send de la instancia anterior aún en
  // vuelo: heredar su spinner para que el usuario no reenvíe (duplicaría
  // registros); al terminar aquel closure, soltar y recoger su respuesta
  useEffect(() => {
    if (!window.__orbitChatEnviando) return
    setSending(true)
    const t = setInterval(() => {
      if (!window.__orbitChatEnviando) { clearInterval(t); setSending(false) }
    }, 500)
    return () => clearInterval(t)
  }, [])

  // al volver a la pestaña: recoger respuestas que llegaron mientras no estaba
  useEffect(() => {
    const recoger = () => {
      if (sending) return
      const saved = loadChat()
      if (!saved || !saved.messages.length) return
      const ultG = saved.messages[saved.messages.length - 1]
      const ultE = messages[messages.length - 1]
      const distinto = !ultE || ultG.role !== ultE.role || ultG.content !== ultE.content
      if (saved.messages.length > messages.length ||
          (saved.messages.length === messages.length && distinto)) {
        startedAtRef.current = saved.startedAt
        setMessages(saved.messages.map((m, i) =>
          i === saved.messages.length - 1 ? { ...m, justArrived: true } : m))
      }
    }
    window.addEventListener('orbit-chat-update', recoger)
    if (!sending) recoger()   // avisos one-shot perdidos (desmonte/en-vuelo)
    return () => window.removeEventListener('orbit-chat-update', recoger)
  }, [messages, sending])

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
        const timer = setTimeout(() => ctrl.abort(), 6000)
        const t0 = Date.now()
        const res = await apiStream('/stream-check', {}, ctrl.signal)
        const reader = res.body.getReader()
        const dec = new TextDecoder()
        let buf = '', t1 = null
        while (Date.now() - t0 < 5500) {
          const { done, value } = await reader.read()
          if (done) break
          buf += dec.decode(value, { stream: true })
          if (buf.includes('"n": 1')) { t1 = Date.now() - t0; break }
        }
        clearTimeout(timer)
        try { ctrl.abort() } catch {}
        // el server retiene la conexión 2.5s tras el ping1: si ping1 llegó
        // ANTES de eso, la red streamea; si llegó al cierre, es buffer
        sseOkRef.current = t1 !== null && t1 < 2000
      } catch { sseOkRef.current = false }
      try { sessionStorage.setItem('orbit_sse', sseOkRef.current ? 'ok' : 'no') } catch {}
    })()
  }, [])

  // ¿quedó un patrón esperando? → el copiloto lo cuenta él mismo al entrar
  useEffect(() => {
    apiPost('/chat/pending', {}).then(r => {
      if (r && r.pending && r.pending.cuerpo) {
        const msg = { role: 'assistant', justArrived: true,
          content: `🧠 ${t('cop.foundIntro')} ${r.pending.cuerpo}`,
          followups: [t('cop.f1'), t('cop.f2')] }
        // el backend lo consumió: persistir YA (si el componente muere antes
        // del render, el patrón sobrevive en storage y recoger lo muestra)
        persistirRespuesta(msg, startedAtRef.current, false)
        setMessages(m => [...m, msg])
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
            img={m.img} grafica={m.grafica} animate={m.justArrived} onScroll={scrollToBottom}/>
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

// "Martes 2 sep" desde la fecha ISO, en el idioma de la UI
function etiquetaFecha(iso, lang) {
  try {
    const [y, mo, d] = String(iso).split('-').map(Number)
    const d0 = new Date(y, mo - 1, d)
    if (!y || !mo || !d || isNaN(d0.getTime())) return iso || ''
    const txt = d0.toLocaleDateString(
      lang === 'en' ? 'en-US' : 'es-ES', { weekday: 'long', day: 'numeric', month: 'short' })
    return txt.charAt(0).toUpperCase() + txt.slice(1)
  } catch { return iso || '' }
}

const Bubble = memo(function Bubble({ theme, role, text, usedData, img, grafica, animate, onScroll }) {
  const { t, lang, gUnit, gVal } = useLang()
  const isUser = role === 'user'
  // gráfica del día: solo con serie real — sin datos, ni hueco ni cabecera
  const conGrafica = !isUser && grafica && Array.isArray(grafica.series)
    && grafica.series.length >= 2
  // en tema claro los tonos brillantes no contrastan: variantes oscuras
  const tirColor = conGrafica && Number.isFinite(grafica.tir_pct)
    ? (grafica.tir_pct >= 70 ? (theme.dark ? '#34D399' : '#059669')
      : grafica.tir_pct >= 50 ? (theme.dark ? '#FBBF24' : '#B45309')
      : (theme.dark ? '#F87171' : '#DC2626'))
    : null
  return (
    <div className="msg-in" style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexDirection: isUser ? 'row-reverse' : 'row' }}>
      {!isUser && (
        <div style={{ flexShrink: 0, width: 34, height: 34, marginBottom: 2 }}>
          <CopilotAvatar size={34}/>
        </div>
      )}
      <div style={{ maxWidth: conGrafica ? '88%' : '78%', width: conGrafica ? '88%' : undefined }}>
        <div style={{
          padding: '11px 14px', borderRadius: 18, fontSize: 14.5, lineHeight: 1.5,
          background: isUser ? theme.accent : theme.surface,
          color: isUser ? '#0A0C1E' : theme.ink,
          borderBottomRightRadius: isUser ? 6 : 18, borderBottomLeftRadius: isUser ? 18 : 6,
          border: isUser ? 'none' : `0.5px solid ${theme.border}`, whiteSpace: 'pre-wrap' }}>
          {conGrafica && (
            <div className="rise-in" style={{ marginBottom: text ? 10 : 2, marginTop: 2 }}>
              <div style={{ display: 'flex', alignItems: 'baseline',
                justifyContent: 'space-between', gap: 8, marginBottom: 8 }}>
                <span style={{ fontSize: 10.5, letterSpacing: '0.08em',
                  textTransform: 'uppercase', color: theme.inkFaint }}>
                  {etiquetaFecha(grafica.fecha, lang)}
                </span>
                {tirColor && (
                  <span style={{ fontSize: 11, fontWeight: 600, color: tirColor,
                    whiteSpace: 'nowrap' }}>
                    {grafica.tir_pct}% {t('cop.tirPill')}
                  </span>
                )}
              </div>
              <GlucoseWave series={grafica.series} markers={grafica.markers || []}
                theme={theme} low={grafica.low || 70} high={grafica.high || 180}
                h={120} live={false} unitLabel={gUnit} fmtVal={gVal}/>
            </div>
          )}
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
})
