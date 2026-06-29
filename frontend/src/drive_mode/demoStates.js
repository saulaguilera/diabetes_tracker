// demoStates.js — estados de demo de Drive Mode (forma idéntica al payload de
// /api/copilot/drive). Para previsualizar la UI sin backend.
export const DEMO_STATES = [
  { label: 'stable 112',     value: 112, unit: 'mg/dL', trend_arrow: '→', trend: 'flat',
    status: 'stable',      level: 'normal',      tint: 'positive', message: 'Stable',
    minutes_since_update: 3, updated_text: 'Updated 3 min ago', sensor: 'Libre 3', connected: true,  stale: false, brand: 'ORBIT' },
  { label: 'falling 82',     value: 82,  unit: 'mg/dL', trend_arrow: '↘', trend: 'falling_slowly',
    status: 'low',         level: 'caution',     tint: 'warning',  message: 'Check when safe',
    minutes_since_update: 2, updated_text: 'Updated 2 min ago', sensor: 'Libre 3', connected: true,  stale: false, brand: 'ORBIT' },
  { label: 'urgent low 68',  value: 68,  unit: 'mg/dL', trend_arrow: '↓', trend: 'falling_fast',
    status: 'urgent_low',  level: 'urgent',      tint: 'critical', message: 'Low glucose — stop when safe',
    minutes_since_update: 1, updated_text: 'Updated 1 min ago', sensor: 'Libre 3', connected: true,  stale: false, brand: 'ORBIT' },
  { label: 'high 210',       value: 210, unit: 'mg/dL', trend_arrow: '↗', trend: 'rising_slowly',
    status: 'high',        level: 'caution',     tint: 'warning',  message: 'Glucose high',
    minutes_since_update: 4, updated_text: 'Updated 4 min ago', sensor: 'Libre 3', connected: true,  stale: false, brand: 'ORBIT' },
  { label: 'stale',          value: 118, unit: 'mg/dL', trend_arrow: '—', trend: 'unknown',
    status: 'stale',       level: 'unavailable', tint: 'muted',    message: 'Data stale',
    minutes_since_update: 22, updated_text: 'Updated 22 min ago', sensor: 'Libre 3', connected: true, stale: true, brand: 'ORBIT' },
  { label: 'disconnected',   value: '--', unit: 'mg/dL', trend_arrow: '—', trend: 'unknown',
    status: 'disconnected', level: 'unavailable', tint: 'muted',   message: 'Sensor disconnected',
    minutes_since_update: null, updated_text: 'No data', sensor: 'Libre 3', connected: false, stale: true, brand: 'ORBIT' },
]
