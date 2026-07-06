// Registro.jsx — registrar comida / insulina / ejercicio.
// POST /api/copilot/log → escribe a las mismas tablas que la app actual.
import { useState, useRef, useEffect } from 'react'
import { apiPost, apiGet } from '../api.js'
import { PAL, SANS } from '../theme.js'
import { Card, Eyebrow, Stepper, Segmented, Chips, Field } from '../components/ui.jsx'
import Historial from '../components/Historial.jsx'

// Redimensiona la foto en el cliente antes de enviarla (evita subir MB).
// 1024px: suficiente detalle para identificar componentes sin subir demasiado.
function fileToDataURL(file, max = 1024, quality = 0.8) {
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
  { id: 'comida',    label: 'Comida',    color: PAL.metabolismo.key },
  { id: 'insulina',  label: 'Insulina',  color: PAL.insulina.key },
  { id: 'ejercicio', label: 'Ejercicio', color: PAL.glucosa.key },
  { id: 'contexto',  label: 'Contexto',  color: PAL.ritmo.key },
]

// etiquetas de contexto: cosas que mueven la glucosa fuera de comida/insulina/ejercicio
const CONTEXT_TAGS = [
  { id: 'estres',    label: 'Estrés',      emoji: '😰' },
  { id: 'enfermo',   label: 'Enfermedad',  emoji: '🤒' },
  { id: 'mal_sueno', label: 'Dormí mal',   emoji: '😴' },
  { id: 'viaje',     label: 'Viaje',       emoji: '✈️' },
  { id: 'alcohol',   label: 'Alcohol',     emoji: '🍷' },
  { id: 'otro',      label: 'Otro',        emoji: '📍' },
]

export default function Registro({ theme, onDone }) {
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
  const [intensity, setIntensity] = useState('Ligera')
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
      const p = { cat, name: name || 'Comida', carbs, protein, fat }
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
      setErr('No se pudo guardar. Intentá de nuevo.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={{ padding: '4px 22px 120px', fontFamily: SANS, display: 'flex', flexDirection: 'column', gap: 20 }}>
      <Eyebrow theme={theme}>Registro</Eyebrow>

      <Segmented theme={theme} options={[{ id: 'registrar', label: 'Registrar' }, { id: 'historial', label: 'Historial' }]} value={mode} onChange={setMode} color={theme.accent}/>

      {mode === 'historial' ? <Historial theme={theme}/> : (
      <>
      <Segmented theme={theme} options={CATS} value={cat} onChange={setCat} color={color}/>

      {cat === 'comida' && (
        <Card theme={theme} style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          {/* escanear con cámara → protagonista del flujo (grande, arriba) */}
          <input ref={fileRef} type="file" accept="image/*" capture="environment" onChange={onPickPhoto} style={{ display: 'none' }}/>
          {!photo ? (
            <button onClick={() => fileRef.current && fileRef.current.click()} disabled={scanning} style={{
              display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 10,
              padding: '26px 14px', borderRadius: 18,
              cursor: scanning ? 'default' : 'pointer', fontFamily: SANS, fontSize: 15,
              background: theme.surface, border: `1.5px dashed ${theme.borderStrong}`, color: theme.inkSoft }}>
              {scanning ? (
                <><span className="ai-orbit" style={{ width: 22, height: 22, borderRadius: '50%', border: `2.5px solid ${color}44`, borderTopColor: color }}/> Estimando…</>
              ) : (
                <>
                  <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>
                  <span>Escanear comida con cámara</span>
                  <span style={{ fontSize: 12, color: theme.inkFaint }}>identifica ingredientes y estima carbos</span>
                </>
              )}
            </button>
          ) : (
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <img src={photo} alt="" style={{ width: 56, height: 56, borderRadius: 12, objectFit: 'cover', flexShrink: 0 }}/>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 12.5, color: theme.inkSoft }}>
                    {scanning ? 'Analizando componentes…' : 'Estimado por IA · revisá los valores'}
                  </div>
                  <div style={{ display: 'flex', gap: 14, marginTop: 4 }}>
                    <button onClick={() => fileRef.current && fileRef.current.click()} style={{ background: 'none', border: 'none', color, fontSize: 12.5, cursor: 'pointer', fontFamily: SANS, padding: 0 }}>Cambiar foto</button>
                    {/* corregiste el nombre → re-estima usándolo como pista */}
                    {!scanning && lastPhotoRef.current && (
                      <button onClick={() => estimate(lastPhotoRef.current, name)} style={{ background: 'none', border: 'none', color: theme.inkSoft, fontSize: 12.5, cursor: 'pointer', fontFamily: SANS, padding: 0 }}>↻ Re-estimar con el nombre</button>
                    )}
                  </div>
                </div>
              </div>
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
                  {confidence === 'baja' && (
                    <div style={{ marginTop: 6, fontSize: 12, color: '#E0B057' }}>
                      ⚠︎ Confianza baja — revisá bien antes de guardar (o re-estimá con el nombre correcto).
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
          <div style={{ position: 'relative' }}>
            <Field theme={theme} value={name} onChange={setName} placeholder="¿Qué comiste? (ej: 200ml leche)"/>
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
          <Row theme={theme} label="Carbohidratos"><Stepper theme={theme} value={carbs} setValue={setCarbs} step={5} max={300} unit="g" color={color}/></Row>
          {scanned && fiber > 0 && (
            <div style={{ fontSize: 12, color: theme.inkFaint, lineHeight: 1.5, marginTop: -8 }}>
              Carbos <b style={{ color: theme.inkSoft }}>netos</b>: estimamos {carbs + fiber}g de carbos − {fiber}g de fibra = {carbs}g (la fibra casi no sube la glucosa).
            </div>
          )}
          <Row theme={theme} label="Proteína"><Stepper theme={theme} value={protein} setValue={setProtein} step={5} max={200} unit="g" color={theme.ink}/></Row>
          <Row theme={theme} label="Grasa"><Stepper theme={theme} value={fat} setValue={setFat} step={5} max={200} unit="g" color={theme.ink}/></Row>
        </Card>
      )}

      {cat === 'insulina' && (
        <Card theme={theme} style={{ display: 'flex', flexDirection: 'column', gap: 22, alignItems: 'center' }}>
          <Stepper theme={theme} value={units} setValue={setUnits} step={0.5} max={50} unit="U" color={color} big/>
          <Chips theme={theme} options={['Rápida', 'Basal']} value={insType} onChange={setInsType} color={color}/>
        </Card>
      )}

      {cat === 'ejercicio' && (
        <Card theme={theme} style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <Chips theme={theme} options={['Caminar', 'Correr', 'Bici', 'Fuerza', 'Otro']} value={actType} onChange={setActType} color={color}/>
          <Row theme={theme} label="Duración"><Stepper theme={theme} value={dur} setValue={setDur} step={5} max={300} unit="min" color={color}/></Row>
          <Chips theme={theme} options={['Ligera', 'Moderada', 'Intensa']} value={intensity} onChange={setIntensity} color={color}/>
        </Card>
      )}

      {cat === 'contexto' && (
        <Card theme={theme} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div style={{ color: theme.inkSoft, fontSize: 13, lineHeight: 1.5 }}>
            ¿Algo que hoy pueda estar moviendo tu glucosa? Marcalo y el copiloto lo tiene en cuenta.
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {CONTEXT_TAGS.map(t => (
              <button key={t.id} onClick={() => setCtxTag(t.id)} style={{
                padding: '10px 14px', borderRadius: 100, cursor: 'pointer', fontFamily: SANS, fontSize: 13.5,
                border: `0.5px solid ${ctxTag === t.id ? color : theme.border}`,
                background: ctxTag === t.id ? `${color}22` : 'transparent',
                color: ctxTag === t.id ? theme.ink : theme.inkSoft, fontWeight: ctxTag === t.id ? 600 : 400 }}>
                {t.emoji} {t.label}
              </button>
            ))}
          </div>
          <Field theme={theme} value={ctxNotes} onChange={setCtxNotes} placeholder="Nota opcional (ej: resfrío, reunión difícil…)"/>
        </Card>
      )}

      {err && <div style={{ color: '#D98A6A', fontSize: 13, textAlign: 'center' }}>{err}</div>}

      <button onClick={submit} disabled={saving} style={{
        marginTop: 4, padding: '15px', borderRadius: 16, border: 'none', cursor: saving ? 'default' : 'pointer',
        background: color, color: '#0A0C1E', fontSize: 15, fontWeight: 600, fontFamily: SANS,
        opacity: saving ? 0.6 : 1, transition: 'opacity 0.2s' }}>
        {saving ? 'Guardando…' : 'Registrar'}
      </button>
      </>
      )}
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
