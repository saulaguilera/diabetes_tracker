// Registro.jsx — registrar comida / insulina / ejercicio.
// POST /api/copilot/log → escribe a las mismas tablas que la app actual.
import { useState } from 'react'
import { apiPost } from '../api.js'
import { PAL, SANS } from '../theme.js'
import { Card, Eyebrow, Stepper, Segmented, Chips, Field } from '../components/ui.jsx'

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
