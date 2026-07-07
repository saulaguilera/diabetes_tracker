// Patrones.jsx — retrospectivo: TIR semanal, resumen y observaciones detectadas.
// Consume GET /api/copilot/patterns. Sin predicción.
import { useState, useEffect } from 'react'
import { apiGet } from '../api.js'
import { PAL, SANS } from '../theme.js'
import { useLang } from '../i18n.jsx'
import { Card, Eyebrow, Loading } from '../components/ui.jsx'

const NIVEL_COLOR = {
  danger: '#D98A6A', warning: '#E0B057', info: PAL.glucosa.key,
  favourable: '#5FC6A8', good: '#5FC6A8',
}
function tirColor(v) {
  if (v == null) return null
  return v >= 70 ? '#5FC6A8' : v >= 50 ? '#E0B057' : '#D98A6A'
}

function Centered({ theme, children }) {
  return <div style={{ minHeight: '60%', display: 'grid', placeItems: 'center', textAlign: 'center', padding: '0 32px', color: theme.inkSoft, fontSize: 14, fontFamily: SANS }}>{children}</div>
}

function WeeklyBars({ theme, weekly }) {
  const H = 92
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', gap: 8 }}>
      {weekly.values.map((v, i) => (
        <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 7 }}>
          <div style={{ width: '100%', maxWidth: 26, height: H, borderRadius: 8, overflow: 'hidden',
            background: theme.dark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.05)', display: 'flex', alignItems: 'flex-end' }}>
            {v != null && <div style={{ width: '100%', height: `${v}%`, background: tirColor(v), borderRadius: 8, transition: 'height 0.5s ease' }}/>}
          </div>
          <span style={{ fontSize: 11, color: theme.inkFaint }}>{weekly.labels[i]}</span>
          <span style={{ fontSize: 10, color: theme.inkFaint, fontVariantNumeric: 'tabular-nums' }}>{v != null ? `${v}%` : '—'}</span>
        </div>
      ))}
    </div>
  )
}

function Stat({ theme, label, value, unit, color }) {
  return (
    <div style={{ flex: 1 }}>
      <div style={{ color: theme.inkFaint, fontSize: 11 }}>{label}</div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 3, marginTop: 5 }}>
        <span style={{ fontSize: 22, fontWeight: 300, color: color || theme.ink, fontVariantNumeric: 'tabular-nums' }}>{value == null ? '—' : value}</span>
        {unit && value != null && <span style={{ fontSize: 12, color: theme.inkSoft }}>{unit}</span>}
      </div>
    </div>
  )
}

export default function Patrones({ theme, refreshKey = 0 }) {
  const { t } = useLang()
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    let alive = true
    setErr(null)
    apiGet('/patterns')
      .then(d => { if (alive) setData(d) })
      .catch(e => { if (alive) setErr(e.message) })
    return () => { alive = false }
  }, [refreshKey])

  if (err) return <Centered theme={theme}>{t('pat.loadError')}</Centered>
  if (!data) return <Centered theme={theme}><Loading theme={theme} label={t('common.loading')}/></Centered>

  const r = data.resumen || {}
  const div = <div style={{ width: '0.5px', background: theme.border }}/>

  return (
    <div style={{ padding: '4px 22px 120px', fontFamily: SANS, display: 'flex', flexDirection: 'column', gap: 20 }}>
      <Eyebrow theme={theme}>{t('pat.title')}</Eyebrow>

      {/* TIR semanal — clickeable para expandir detalle */}
      <Card theme={theme} style={{ padding: '20px 20px 16px', cursor: 'pointer' }} onClick={() => setExpanded(e => !e)}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 16 }}>
          <span style={{ color: theme.inkSoft, fontSize: 14 }}>{t('pat.tir')}</span>
          <span style={{ color: theme.inkFaint, fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
            {t('pat.days7')}
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={theme.inkFaint} strokeWidth="2.2"
              strokeLinecap="round" strokeLinejoin="round" style={{ transform: expanded ? 'rotate(180deg)' : 'none', transition: 'transform 0.25s' }}>
              <path d="M6 9l6 6 6-6"/>
            </svg>
          </span>
        </div>
        <WeeklyBars theme={theme} weekly={data.weekly}/>

        {expanded && (() => {
          const bajo = r.hipo_pct || 0, alto = r.hiper_pct || 0
          const rango = r.tir != null ? r.tir : Math.max(0, 100 - bajo - alto)
          const dist = [
            { label: t('pat.distHigh'), val: alto, color: '#E0B057' },
            { label: t('pat.distRange'), val: rango, color: '#5FC6A8' },
            { label: t('pat.distLow'), val: bajo, color: '#D98A6A' },
          ]
          return (
            <div style={{ marginTop: 18, paddingTop: 16, borderTop: `0.5px solid ${theme.border}` }} onClick={e => e.stopPropagation()}>
              <div style={{ fontSize: 11, letterSpacing: '0.12em', textTransform: 'uppercase', color: theme.inkFaint, marginBottom: 12 }}>
                {t('pat.distribution', { n: r.days || 14 })}
              </div>
              {/* barra apilada */}
              <div style={{ display: 'flex', height: 12, borderRadius: 6, overflow: 'hidden', background: theme.surface, marginBottom: 14 }}>
                {[['#D98A6A', bajo], ['#5FC6A8', rango], ['#E0B057', alto]].map(([c, w], i) => w > 0 && (
                  <div key={i} style={{ width: `${w}%`, background: c }}/>
                ))}
              </div>
              {dist.map((d, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '7px 0' }}>
                  <span style={{ width: 8, height: 8, borderRadius: '50%', background: d.color }}/>
                  <span style={{ flex: 1, color: theme.inkSoft, fontSize: 13.5 }}>{d.label}</span>
                  <span style={{ color: theme.ink, fontSize: 14, fontWeight: 500, fontVariantNumeric: 'tabular-nums' }}>{d.val}%</span>
                </div>
              ))}
              {/* GMI estimada (≈ HbA1c) */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginTop: 14, paddingTop: 14, borderTop: `0.5px solid ${theme.border}` }}>
                <div>
                  <div style={{ color: theme.inkSoft, fontSize: 13.5 }}>{t('pat.gmi')}</div>
                  <div style={{ color: theme.inkFaint, fontSize: 11, marginTop: 2 }}>{t('pat.gmiSub')}</div>
                </div>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 2 }}>
                  <span style={{ fontSize: 26, fontWeight: 300, color: theme.ink, fontVariantNumeric: 'tabular-nums' }}>{r.gmi ?? '—'}</span>
                  {r.gmi != null && <span style={{ fontSize: 14, color: theme.inkSoft }}>%</span>}
                </div>
              </div>
              <div style={{ display: 'flex', gap: 14, marginTop: 12 }}>
                <span style={{ color: theme.inkSoft, fontSize: 13 }}>{t('pat.average')} <b style={{ color: theme.ink, fontWeight: 500 }}>{r.avg ?? '—'}</b> mg/dL</span>
                <span style={{ color: theme.inkSoft, fontSize: 13 }}>{t('pat.variability')} <b style={{ color: theme.ink, fontWeight: 500 }}>{r.cv ?? '—'}%</b></span>
              </div>
            </div>
          )
        })()}
      </Card>

      {/* resumen */}
      <Card theme={theme} style={{ padding: '16px 20px' }}>
        <Eyebrow theme={theme} style={{ fontSize: 10 }}>{t('pat.summary', { n: r.days || 14 })}</Eyebrow>
        <div style={{ display: 'flex', gap: 12, marginTop: 14 }}>
          <Stat theme={theme} label={t('pat.average')} value={r.avg} unit="mg/dL"/>
          {div}
          <Stat theme={theme} label={t('pat.variability')} value={r.cv} unit="%" color={r.cv != null && r.cv > 36 ? '#E0B057' : theme.ink}/>
          {div}
          <Stat theme={theme} label={t('pat.inRange')} value={r.tir} unit="%" color={tirColor(r.tir)}/>
        </div>
      </Card>

      {/* observaciones detectadas */}
      <div>
        <Eyebrow theme={theme} style={{ marginBottom: 12 }}>{t('pat.observations')}</Eyebrow>
        {(!data.patterns || data.patterns.length === 0) ? (
          <Card theme={theme} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#5FC6A8', boxShadow: '0 0 8px #5FC6A8' }}/>
            <span style={{ color: theme.inkSoft, fontSize: 14 }}>{t('pat.noPatterns')}</span>
          </Card>
        ) : data.patterns.map((p, i) => {
          const c = NIVEL_COLOR[p.nivel] || PAL.glucosa.key
          return (
            <Card key={i} theme={theme} style={{ marginBottom: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%', flexShrink: 0, background: c, boxShadow: `0 0 8px ${c}` }}/>
                <span style={{ color: theme.ink, fontSize: 15, fontWeight: 500 }}>{p.titulo}</span>
              </div>
              {p.detalle && <div style={{ color: theme.inkSoft, fontSize: 13, lineHeight: 1.5 }}>{p.detalle}</div>}
              {p.sugerencia && (
                <div style={{ color: theme.inkFaint, fontSize: 12.5, lineHeight: 1.5, marginTop: 10, paddingTop: 10, borderTop: `0.5px solid ${theme.border}` }}>
                  {p.sugerencia}
                </div>
              )}
            </Card>
          )
        })}
        <div style={{ color: theme.inkFaint, fontSize: 11, lineHeight: 1.5, marginTop: 4, textAlign: 'center' }}>
          {t('pat.note')}
        </div>
      </div>
    </div>
  )
}
