// Registro.jsx — registrar comida / insulina / ejercicio.
// POST /api/copilot/log → escribe a las mismas tablas que la app actual.
import { useState, useRef } from 'react'
import { apiPost } from '../api.js'
import { PAL, SANS } from '../theme.js'
import { Card, Eyebrow, Stepper, Segmented, Chips, Field } from '../components/ui.jsx'

// Redimensiona la foto en el cliente antes de enviarla (evita subir MB).
function fileToDataURL(file, max = 768, quality = 0.7) {
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
]

export default function Registro({ theme, onDone }) {
  const [cat, setCat] = useState('comida')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState(null)

  // comida
  const [name, setName] = useState('')
  const [carbs, setCarbs] = useState(30)
  const [protein, setProtein] = useState(0)
  const [fat, setFat] = useState(0)
  // insulina
  const [units, setUnits] = useState(4)
  const [insType, setInsType] = useState('Rápida')
  // ejercicio
  const [actType, setActType] = useState('Caminar')
  const [dur, setDur] = useState(30)
  const [intensity, setIntensity] = useState('Ligera')
  // foto / estimación
  const fileRef = useRef(null)
  const [photo, setPhoto] = useState(null)
  const [scanning, setScanning] = useState(false)
  const [scanned, setScanned] = useState(false)

  const onPickPhoto = async (e) => {
    const file = e.target.files && e.target.files[0]
    if (!file) return
    setScanning(true); setScanned(false); setErr(null)
    try {
      const dataUrl = await fileToDataURL(file)
      setPhoto(dataUrl)
      const r = await apiPost('/estimate', { image: dataUrl })
      if (r.name) setName(r.name)
      setCarbs(r.carbs || 0); setProtein(r.protein || 0); setFat(r.fat || 0)
      setScanned(true)
    } catch (e2) {
      setErr('No pude estimar la foto. Cargá los datos a mano.')
    } finally {
      setScanning(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const color = CATS.find(c => c.id === cat).color

  const payload = () => {
    if (cat === 'comida') return { cat, name: name || 'Comida', carbs, protein, fat }
    if (cat === 'insulina') return { cat, units, type: insType === 'Basal' ? 'basal' : 'bolus' }
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

      <Segmented theme={theme} options={CATS} value={cat} onChange={setCat} color={color}/>

      {cat === 'comida' && (
        <Card theme={theme} style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          {/* escanear con cámara → estima macros (editables) */}
          <input ref={fileRef} type="file" accept="image/*" capture="environment" onChange={onPickPhoto} style={{ display: 'none' }}/>
          {!photo ? (
            <button onClick={() => fileRef.current && fileRef.current.click()} disabled={scanning} style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10, padding: '14px', borderRadius: 16,
              cursor: scanning ? 'default' : 'pointer', fontFamily: SANS, fontSize: 14,
              background: theme.surface, border: `1px dashed ${theme.borderStrong}`, color: theme.inkSoft }}>
              {scanning ? (
                <><span className="ai-orbit" style={{ width: 16, height: 16, borderRadius: '50%', border: `2px solid ${color}44`, borderTopColor: color }}/> Estimando…</>
              ) : (
                <><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg> Escanear comida con cámara</>
              )}
            </button>
          ) : (
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <img src={photo} alt="" style={{ width: 56, height: 56, borderRadius: 12, objectFit: 'cover', flexShrink: 0 }}/>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 12.5, color: theme.inkSoft }}>{scanning ? 'Estimando…' : 'Estimado por IA · revisá los valores'}</div>
                <button onClick={() => fileRef.current && fileRef.current.click()} style={{ marginTop: 4, background: 'none', border: 'none', color, fontSize: 12.5, cursor: 'pointer', fontFamily: SANS, padding: 0 }}>Cambiar foto</button>
              </div>
            </div>
          )}
          <Field theme={theme} value={name} onChange={setName} placeholder="¿Qué comiste?"/>
          <Row theme={theme} label="Carbohidratos"><Stepper theme={theme} value={carbs} setValue={setCarbs} step={5} max={300} unit="g" color={color}/></Row>
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

      {err && <div style={{ color: '#D98A6A', fontSize: 13, textAlign: 'center' }}>{err}</div>}

      <button onClick={submit} disabled={saving} style={{
        marginTop: 4, padding: '15px', borderRadius: 16, border: 'none', cursor: saving ? 'default' : 'pointer',
        background: color, color: '#0A0C1E', fontSize: 15, fontWeight: 600, fontFamily: SANS,
        opacity: saving ? 0.6 : 1, transition: 'opacity 0.2s' }}>
        {saving ? 'Guardando…' : 'Registrar'}
      </button>
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
