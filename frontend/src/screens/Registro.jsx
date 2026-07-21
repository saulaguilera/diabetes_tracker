// Registro.jsx — registrar comida / insulina / ejercicio.
// POST /api/copilot/log → escribe a las mismas tablas que la app actual.
import { useState, useRef, useEffect } from 'react'
import { apiPost, apiGet } from '../api.js'
import { PAL, SANS } from '../theme.js'
import { useLang } from '../i18n.jsx'
import { Card, Eyebrow, Stepper, Segmented, Chips, Field } from '../components/ui.jsx'
import Historial from '../components/Historial.jsx'

// Redimensiona la foto en el cliente antes de enviarla (evita subir MB).
// 1280px: el modelo de visión aprovecha hasta ~1568px por lado; con 1024 se
// perdían detalles finos (granos, salsas) que cambian la estimación de porción.
function fileToDataURL(file, max = 1280, quality = 0.8) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = reject
    reader.onload = () => {
      const img = new Image()
      img.onerror = reject
      img.onload = () => {
        const scale = Math.min(1, max / Math.max(img.width, img.height))
        const w = Math.round(img.width * scale), h = Math.round(img.height * scale)
        const canvas = document.createElement('canvas')
        canvas.width = w; canvas.height = h
        canvas.getContext('2d').drawImage(img, 0, 0, w, h)
        resolve(canvas.toDataURL('image/jpeg', quality))
      }
      img.src = reader.result
    }
    reader.readAsDataURL(file)
  })
}

const CATS = [
  { id: 'comida',    key: 'reg.cat.comida',    color: PAL.metabolismo.key },
  { id: 'insulina',  key: 'reg.cat.insulina',  color: PAL.insulina.key },
  { id: 'ejercicio', key: 'reg.cat.ejercicio', color: PAL.glucosa.key },
  { id: 'contexto',  key: 'reg.cat.contexto',  color: PAL.ritmo.key },
]

// etiquetas de contexto: cosas que mueven la glucosa fuera de comida/insulina/ejercicio
const CONTEXT_TAGS = [
  { id: 'estres',    key: 'reg.tag.estres',    emoji: '😰' },
  { id: 'enfermo',   key: 'reg.tag.enfermo',   emoji: '🤒' },
  { id: 'mal_sueno', key: 'reg.tag.mal_sueno', emoji: '😴' },
  { id: 'viaje',     key: 'reg.tag.viaje',     emoji: '✈️' },
  { id: 'alcohol',   key: 'reg.tag.alcohol',   emoji: '🍷' },
  { id: 'otro',      key: 'reg.tag.otro',      emoji: '📍' },
]
// opciones de ejercicio/intensidad (id estable para backend + clave i18n)
const EX_TYPES = [['Caminar','reg.ex.walk'],['Correr','reg.ex.run'],['Bici','reg.ex.bike'],['Fuerza','reg.ex.strength'],['Otro','reg.ex.other']]
// id = valor canónico que guarda el backend (baja/media/alta), igual que el resto
// del sistema (dashboard clásico, quicklog, datos históricos). La etiqueta es i18n.
const INTENSITIES = [['baja','reg.int.light'],['media','reg.int.moderate'],['alta','reg.int.intense']]

export default function Registro({ theme, onDone }) {
  const { t } = useLang()
  const [mode, setMode] = useState('registrar')
  const [cat, setCat] = useState('comida')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState(null)

  // comida — carbos arrancan en 0: un default arbitrario (30g) que se guarda
  // sin ajustar es peor que vacío (ensucia la memoria de comidas y el research)
  const [name, setName] = useState('')
  const [carbs, setCarbs] = useState(0)
  const [protein, setProtein] = useState(0)
  const [fat, setFat] = useState(0)
  const [fiber, setFiber] = useState(0)   // fibra estimada (se descuenta de carbos)
  // insulina
  const [units, setUnits] = useState(4)
  const [insType, setInsType] = useState('Rápida')
  // ejercicio
  const [actType, setActType] = useState('Caminar')
  const [dur, setDur] = useState(30)
  const [intensity, setIntensity] = useState('media')
  // contexto
  const [ctxTag, setCtxTag] = useState('estres')
  const [ctxNotes, setCtxNotes] = useState('')
  // foto / estimación
  const fileRef = useRef(null)
  const [photo, setPhoto] = useState(null)
  const [scanning, setScanning] = useState(false)
  const [scanned, setScanned] = useState(false)
  // autocompletar desde la base nutricional interna
  const [sugg, setSugg] = useState([])
  const skipSuggRef = useRef(false)   // true cuando el nombre lo puso la foto/sugerencia

  // autocompletar con debounce mientras se escribe el nombre
  useEffect(() => {
    if (skipSuggRef.current) { skipSuggRef.current = false; setSugg([]); return }
    const q = name.trim()
    if (q.length < 3) { setSugg([]); return }
    const t = setTimeout(() => {
      apiGet(`/food/search?q=${encodeURIComponent(q)}`)
        .then(d => setSugg(d.results || []))
        .catch(() => setSugg([]))
    }, 350)
    return () => clearTimeout(t)
  }, [name])

  const pickSugg = (s) => {
    skipSuggRef.current = true
    setName(s.label.charAt(0).toUpperCase() + s.label.slice(1))
    setCarbs(s.carbs); setProtein(s.protein); setFat(s.fat)
    setFiber(s.fiber || 0); setScanned(true); setSugg([])
  }

  const [breakdown, setBreakdown] = useState([])   // componentes estimados
  const [confidence, setConfidence] = useState(null)
  const [score, setScore] = useState(null)         // 1-10 amigabilidad T1D
  const [scoreReason, setScoreReason] = useState('')
  const lastPhotoRef = useRef(null)                // para re-estimar con pista

  const estimate = async (dataUrl, hint) => {
    setScanning(true); setScanned(false); setErr(null)
    try {
      const r = await apiPost('/estimate', { image: dataUrl, hint: hint || '' })
      if (r.name) { skipSuggRef.current = true; setName(r.name) }
      const f = r.fiber || 0
      setFiber(f)
      // Carbos NETOS = totales − fibra (la fibra casi no sube la glucosa).
      setCarbs(Math.max(0, Math.round((r.carbs || 0) - f)))
      setProtein(r.protein || 0); setFat(r.fat || 0)
      setBreakdown(r.breakdown || [])
      setConfidence(r.confidence || null)
      setScore(r.score || null); setScoreReason(r.score_reason || '')
      setScanned(true)
    } catch (e2) {
      setErr('No pude estimar la foto. Cargá los datos a mano.')
    } finally {
      setScanning(false)
    }
  }

  const onPickPhoto = async (e) => {
    const file = e.target.files && e.target.files[0]
    if (!file) return
    try {
      const dataUrl = await fileToDataURL(file)
      setPhoto(dataUrl); lastPhotoRef.current = dataUrl
      // si el usuario ya escribió qué es, usarlo como pista para identificar
      await estimate(dataUrl, name)
    } catch (e2) {
      setErr('No pude leer la foto.')
    } finally {
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const color = CATS.find(c => c.id === cat).color

  const payload = () => {
    if (cat === 'comida') {
      const p = { cat, name: name || 'Comida', carbs, protein, fat, fiber }
      if (score) { p.score = score }
      // ingredientes del desglose de la foto → se guardan como componentes
      if (scanned && breakdown.length > 0) p.components = breakdown
      return p
    }
    if (cat === 'insulina') return { cat, units, type: insType === 'Basal' ? 'basal' : 'bolus' }
    if (cat === 'contexto') return { cat, tag: ctxTag, notes: ctxNotes }
    return { cat, activity_type: actType, duration_min: dur, intensity }
  }

  const submit = async () => {
    setSaving(true); setErr(null)
    try {
      await apiPost('/log', payload())
      onDone && onDone()
    } catch (e) {
      setErr(t('reg.saveError'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={{ padding: '4px 22px 120px', fontFamily: SANS, display: 'flex', flexDirection: 'column', gap: 20 }}>
      <Eyebrow theme={theme}>{t('reg.title')}</Eyebrow>

      <Segmented theme={theme} options={[{ id: 'registrar', label: t('reg.tabLog') }, { id: 'historial', label: t('reg.tabHistory') }]} value={mode} onChange={setMode} color={theme.accent}/>

      {mode === 'historial' ? <Historial theme={theme}/> : (
      <>
      <Segmented theme={theme} options={CATS.map(c => ({ id: c.id, label: t(c.key) }))} value={cat} onChange={setCat} color={color}/>

      {cat === 'comida' && (
        <Card theme={theme} style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          {/* escanear con cámara → protagonista del flujo (grande, arriba) */}
          <input ref={fileRef} type="file" accept="image/*" capture="environment" onChange={onPickPhoto} style={{ display: 'none' }}/>
          {!photo ? (
            scanning ? <Analyzing theme={theme} color={color}/> : (
            <button onClick={() => fileRef.current && fileRef.current.click()} style={{
              display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 10,
              padding: '26px 14px', borderRadius: 18,
              cursor: 'pointer', fontFamily: SANS, fontSize: 15,
              background: theme.surface, border: `1.5px dashed ${theme.borderStrong}`, color: theme.inkSoft }}>
              <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>
              <span>{t('reg.scan')}</span>
              <span style={{ fontSize: 12, color: theme.inkFaint }}>{t('reg.scanHint')}</span>
            </button>
            )
          ) : (
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <img src={photo} alt="" style={{ width: 56, height: 56, borderRadius: 12, objectFit: 'cover', flexShrink: 0 }}/>
                <div style={{ flex: 1 }}>
                  {!scanning && <div style={{ fontSize: 12.5, color: theme.inkSoft }}>{t('reg.estimatedBy')}</div>}
                  <div style={{ display: 'flex', gap: 14, marginTop: 4 }}>
                    <button onClick={() => fileRef.current && fileRef.current.click()} style={{ background: 'none', border: 'none', color, fontSize: 12.5, cursor: 'pointer', fontFamily: SANS, padding: 0 }}>{t('reg.changePhoto')}</button>
                    {/* corregiste el nombre → re-estima usándolo como pista */}
                    {!scanning && lastPhotoRef.current && (
                      <button onClick={() => estimate(lastPhotoRef.current, name)} style={{ background: 'none', border: 'none', color: theme.inkSoft, fontSize: 12.5, cursor: 'pointer', fontFamily: SANS, padding: 0 }}>{t('reg.reestimate')}</button>
                    )}
                  </div>
                </div>
              </div>
              {scanning && <div style={{ marginTop: 10 }}><Analyzing theme={theme} color={color}/></div>}
              {/* desglose por componente — transparencia de dónde salió cada gramo */}
              {scanned && breakdown.length > 0 && (
                <div className="rise-in" style={{ marginTop: 10, padding: '10px 12px', borderRadius: 12,
                  background: theme.dark ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.03)',
                  border: `0.5px solid ${theme.border}` }}>
                  {breakdown.map((b, i) => (
                    <div key={i} style={{ display: 'flex', gap: 8, fontSize: 12.5, padding: '3px 0', color: theme.inkSoft }}>
                      <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{b.name}</span>
                      <span style={{ color: theme.inkFaint }}>{b.grams}g</span>
                      <span style={{ color, fontWeight: 600, width: 52, textAlign: 'right' }}>{b.carbs}g CH</span>
                      <span title={b.source === 'base' ? 'macros desde la base nutricional' : 'macros estimados por IA'}
                        style={{ color: b.source === 'base' ? '#5FC6A8' : theme.inkFaint, fontSize: 11 }}>
                        {b.source === 'base' ? '◈ base' : '◇ IA'}
                      </span>
                    </div>
                  ))}
                  {/* puntuación T1D del plato: número + porqué en una frase */}
                  {score != null && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 8,
                      paddingTop: 8, borderTop: `0.5px solid ${theme.border}` }}>
                      <span style={{ flexShrink: 0, minWidth: 44, textAlign: 'center', padding: '3px 8px',
                        borderRadius: 100, fontSize: 12.5, fontWeight: 700,
                        color: scoreColor(score), background: `${scoreColor(score)}1E`,
                        border: `0.5px solid ${scoreColor(score)}55` }}>
                        {score}/10
                      </span>
                      <span style={{ fontSize: 12, color: theme.inkSoft, lineHeight: 1.4 }}>
                        {scoreReason || t('reg.scoreLabel')}
                      </span>
                    </div>
                  )}
                  {confidence === 'baja' && (
                    <div style={{ marginTop: 6, fontSize: 12, color: '#E0B057' }}>
                      {t('reg.lowConfidence')}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
          <div style={{ position: 'relative' }}>
            <Field theme={theme} value={name} onChange={setName} placeholder={t('reg.whatAte')}/>
            {/* sugerencias desde la base nutricional (CH netos por porción) */}
            {sugg.length > 0 && (
              <div className="rise-in" style={{ position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 40, marginTop: 6,
                background: theme.dark ? '#101830' : '#fff', borderRadius: 14,
                border: `0.5px solid ${theme.borderStrong}`, overflow: 'hidden',
                boxShadow: '0 12px 32px rgba(0,0,0,0.35)' }}>
                {sugg.map((s, i) => (
                  <div key={i} onClick={() => pickSugg(s)} style={{ display: 'flex', alignItems: 'center',
                    gap: 10, padding: '11px 14px', cursor: 'pointer',
                    borderTop: i ? `0.5px solid ${theme.border}` : 'none' }}>
                    <span style={{ flex: 1, color: theme.ink, fontSize: 14, whiteSpace: 'nowrap',
                      overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {s.label.charAt(0).toUpperCase() + s.label.slice(1)}
                    </span>
                    <span style={{ color, fontSize: 13, fontWeight: 600, flexShrink: 0 }}>{s.carbs}g CH</span>
                    {s.grams != null && <span style={{ color: theme.inkFaint, fontSize: 11.5, flexShrink: 0 }}>/{Math.round(s.grams)}g</span>}
                  </div>
                ))}
              </div>
            )}
          </div>
          <Row theme={theme} label={t('reg.carbs')}><Stepper theme={theme} value={carbs} setValue={setCarbs} step={5} max={300} unit="g" color={color}/></Row>
          <Row theme={theme} label={t('reg.protein')}><Stepper theme={theme} value={protein} setValue={setProtein} step={5} max={200} unit="g" color={theme.ink}/></Row>
          <Row theme={theme} label={t('reg.fat')}><Stepper theme={theme} value={fat} setValue={setFat} step={5} max={200} unit="g" color={theme.ink}/></Row>
          {/* fibra: informativa — los carbos de arriba ya son netos (fibra descontada) */}
          <Row theme={theme} label={<span>{t('reg.fiber')} <span style={{ color: theme.inkFaint, fontSize: 11.5 }}>· {t('reg.fiberHint')}</span></span>}>
            <Stepper theme={theme} value={fiber} setValue={setFiber} step={1} max={60} unit="g" color="#5FC6A8"/>
          </Row>
        </Card>
      )}

      {cat === 'insulina' && (
        <Card theme={theme} style={{ display: 'flex', flexDirection: 'column', gap: 22, alignItems: 'center' }}>
          <Stepper theme={theme} value={units} setValue={setUnits} step={0.5} max={50} unit="U" color={color} big/>
          <Chips theme={theme} options={[{ id: 'Rápida', label: t('reg.rapid') }, { id: 'Basal', label: t('reg.basal') }]} value={insType} onChange={setInsType} color={color}/>
        </Card>
      )}

      {cat === 'ejercicio' && (
        <Card theme={theme} style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <Chips theme={theme} options={EX_TYPES.map(([id, k]) => ({ id, label: t(k) }))} value={actType} onChange={setActType} color={color}/>
          <Row theme={theme} label={t('reg.duration')}><Stepper theme={theme} value={dur} setValue={setDur} step={5} max={300} unit="min" color={color}/></Row>
          {/* etiqueta arriba de las chips: sin ella no se entiende qué significan */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <span style={{ color: theme.inkSoft, fontSize: 14 }}>{t('ev.intensity')}</span>
            <Chips theme={theme} options={INTENSITIES.map(([id, k]) => ({ id, label: t(k) }))} value={intensity} onChange={setIntensity} color={color}/>
          </div>
        </Card>
      )}

      {cat === 'contexto' && (
        <Card theme={theme} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div style={{ color: theme.inkSoft, fontSize: 13, lineHeight: 1.5 }}>
            {t('reg.ctxPrompt')}
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {CONTEXT_TAGS.map(ct => (
              <button key={ct.id} onClick={() => setCtxTag(ct.id)} style={{
                padding: '10px 14px', borderRadius: 100, cursor: 'pointer', fontFamily: SANS, fontSize: 13.5,
                border: `0.5px solid ${ctxTag === ct.id ? color : theme.border}`,
                background: ctxTag === ct.id ? `${color}22` : 'transparent',
                color: ctxTag === ct.id ? theme.ink : theme.inkSoft, fontWeight: ctxTag === ct.id ? 600 : 400 }}>
                {ct.emoji} {t(ct.key)}
              </button>
            ))}
          </div>
          <Field theme={theme} value={ctxNotes} onChange={setCtxNotes} placeholder={t('reg.ctxNote')}/>
        </Card>
      )}

      {err && <div style={{ color: '#D98A6A', fontSize: 13, textAlign: 'center' }}>{err}</div>}

      <button onClick={submit} disabled={saving} style={{
        marginTop: 4, padding: '15px', borderRadius: 16, border: 'none', cursor: saving ? 'default' : 'pointer',
        background: color, color: '#0A0C1E', fontSize: 15, fontWeight: 600, fontFamily: SANS,
        opacity: saving ? 0.6 : 1, transition: 'opacity 0.2s' }}>
        {saving ? t('common.saving') : t('reg.register')}
      </button>
      </>
      )}
    </div>
  )
}

// Estado de análisis visible: spinner + mensajes que rotan mientras la IA
// trabaja (identificando → porciones → macros). Nadie se queda mirando
// una pantalla muda preguntándose si la foto llegó.
function Analyzing({ theme, color }) {
  const { t } = useLang()
  const msgs = [t('reg.analyzing1'), t('reg.analyzing2'), t('reg.analyzing3')]
  const [i, setI] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setI(v => (v + 1) % msgs.length), 1800)
    return () => clearInterval(id)
  }, [])
  return (
    <div className="rise-in" style={{ display: 'flex', alignItems: 'center', gap: 10,
      padding: '12px 14px', borderRadius: 12, background: `${color}14`,
      border: `0.5px solid ${color}44` }}>
      <span className="ai-orbit" style={{ width: 18, height: 18, borderRadius: '50%',
        border: `2.5px solid ${color}44`, borderTopColor: color, flexShrink: 0 }}/>
      <span style={{ fontSize: 13.5, fontWeight: 600, color: theme.ink }}>{msgs[i]}</span>
    </div>
  )
}

// color de la puntuación 1-10: verde ≥7, ámbar 4-6, coral ≤3
const scoreColor = (s) => s >= 7 ? '#5FC6A8' : s >= 4 ? '#E0B057' : '#D98A6A'

function Row({ theme, label, children }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
      <span style={{ color: theme.inkSoft, fontSize: 14 }}>{label}</span>
      {children}
    </div>
  )
}
