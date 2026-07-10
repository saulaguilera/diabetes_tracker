// i18n.js — internacionalización de Orbit.
// Español neutro (latino) + English. Agregar un idioma = un bloque más en DICT.
// El idioma se persiste en localStorage y se avisa al backend (para que el
// copiloto responda en el mismo idioma).
import { createContext, useContext, useState, useCallback } from 'react'

export const LANGS = [
  { id: 'es', label: 'Español' },
  { id: 'en', label: 'English' },
]

const DICT = {
  es: {
    // navegación
    'nav.hoy': 'Hoy', 'nav.patrones': 'Patrones', 'nav.registro': 'Registro',
    'nav.copiloto': 'Copiloto', 'nav.perfil': 'Perfil',
    'app.driveAria': 'Modo conducción',
    // comunes
    'common.loading': 'Cargando…', 'common.save': 'Guardar', 'common.saving': 'Guardando…',
    'common.cancel': 'Cancelar', 'common.delete': 'Eliminar', 'common.deleting': 'Eliminando…',
    'common.edit': 'Editar', 'common.today': 'Hoy', 'common.yesterday': 'Ayer',
    'common.loadError': 'No se pudieron cargar tus datos ahora.',
    'common.mgdl': 'mg/dL',
    'greet.morning': 'Buenos días', 'greet.afternoon': 'Buenas tardes', 'greet.evening': 'Buenas noches',
    // Hoy
    'hoy.now': 'Ahora', 'hoy.noReadings': 'Sin lecturas recientes.',
    'hoy.status.low': 'Bajo', 'hoy.status.high': 'Alto', 'hoy.status.range': 'En rango',
    'hoy.agoMin': 'hace {n}m',
    'hoy.contextNow': 'Contexto ahora', 'hoy.activeInsulin': 'Insulina activa',
    'hoy.activeCarbs': 'Carbos activos', 'hoy.trend': 'Tendencia',
    'hoy.trend.up': 'Subiendo', 'hoy.trend.down': 'Bajando', 'hoy.trend.flat': 'Estable',
    'hoy.basal': 'Basal', 'hoy.basalNotToday': 'hoy sin registrar',
    'hoy.tir': 'Tiempo en rango', 'hoy.tirToday': 'Hoy · 24 h',
    'hoy.recent': 'Actividad reciente',
    'hoy.brief': 'Brief diario', 'hoy.briefTeaser': '{greet} · {tir}% en rango hoy',
    'hoy.briefDefault': 'Tu resumen del día',
    'hoy.basalReminder': 'Tu basal de hoy todavía no está registrada',
    'hoy.basalReminderWhen': 'Solés aplicarla a las {h}:00',
    'hoy.log': 'Registrar', 'hoy.dismiss': 'Descartar por hoy',
    // Registro
    'reg.title': 'Registro', 'reg.tabLog': 'Registrar', 'reg.tabHistory': 'Historial',
    'reg.cat.comida': 'Comida', 'reg.cat.insulina': 'Insulina',
    'reg.cat.ejercicio': 'Ejercicio', 'reg.cat.contexto': 'Contexto',
    'reg.scan': 'Escanear comida con cámara', 'reg.scanHint': 'identifica ingredientes y estima carbos',
    'reg.estimating': 'Estimando…', 'reg.analyzing': 'Analizando componentes…',
    'reg.estimatedBy': 'Estimado por IA · revisá los valores',
    'reg.changePhoto': 'Cambiar foto', 'reg.reestimate': '↻ Re-estimar con el nombre',
    'reg.lowConfidence': '⚠︎ Confianza baja — revisá bien antes de guardar (o re-estimá con el nombre correcto).',
    'reg.whatAte': '¿Qué comiste? (ej: 200ml leche)',
    'reg.carbs': 'Carbohidratos', 'reg.protein': 'Proteína', 'reg.fat': 'Grasa',
    'reg.rapid': 'Rápida', 'reg.basal': 'Basal',
    'reg.duration': 'Duración',
    'reg.ex.walk': 'Caminar', 'reg.ex.run': 'Correr', 'reg.ex.bike': 'Bici',
    'reg.ex.strength': 'Fuerza', 'reg.ex.other': 'Otro',
    'reg.int.light': 'Baja', 'reg.int.moderate': 'Media', 'reg.int.intense': 'Alta',
    'reg.ctxPrompt': '¿Algo que hoy pueda estar moviendo tu glucosa? Marcalo y el copiloto lo tiene en cuenta.',
    'reg.ctxNote': 'Nota opcional (ej: resfrío, reunión difícil…)',
    'reg.tag.estres': 'Estrés', 'reg.tag.enfermo': 'Enfermedad', 'reg.tag.mal_sueno': 'Dormí mal',
    'reg.tag.viaje': 'Viaje', 'reg.tag.alcohol': 'Alcohol', 'reg.tag.otro': 'Otro',
    'reg.saveError': 'No se pudo guardar. Intentá de nuevo.',
    'reg.register': 'Registrar',
    // EventSheet
    'ev.editMeal': 'Editar comida', 'ev.ingredients': 'Ingredientes',
    'ev.day': 'Día', 'ev.time': 'Hora',
    'ev.units': 'Unidades', 'ev.type': 'Tipo', 'ev.activity': 'Actividad',
    'ev.durationLabel': 'Duración', 'ev.intensity': 'Intensidad',
    'ev.context': 'Contexto', 'ev.note': 'Nota',
    'ev.confirmDel': '¿Seguro que querés eliminar {noun}? Esta acción no se puede deshacer.',
    'ev.confirmYes': 'Sí, eliminar',
    'ev.noun.meal': 'este alimento', 'ev.noun.insulin': 'este registro de insulina',
    'ev.noun.exercise': 'esta actividad', 'ev.noun.context': 'esta marca de contexto',
    // Notificaciones
    'notif.title': 'Notificaciones',
    'notif.loading': 'Buscando novedades…',
    'notif.loadError': 'No pude cargar las notificaciones ahora.',
    'notif.empty': 'Sin novedades por ahora. Cuando Orbit encuentre un patrón nuevo en tus datos, te lo dejo acá. 💙',
    // Brief
    'brief.preparing': 'Preparando tu resumen…', 'brief.loadError': 'No se pudo cargar el resumen ahora.',
    'brief.empty': 'Todavía no registraste nada hoy. Cuando agregues lecturas o eventos, los vas a ver acá.',
    'brief.yourMeals': 'Tus comidas de hoy', 'brief.disclaimer': 'Orbit solo describe y acompaña tus datos. No reemplaza a tu equipo médico.',
    // Copiloto
    'cop.greeting': 'Hola 👋 Soy tu copiloto. Puedo explicarte tus datos y acompañarte. Para dosis o decisiones médicas, siempre tu equipo de salud. ¿Qué querés saber?',
    'cop.thinking': 'pensando…', 'cop.analyzing': 'analizando tus datos…',
    'cop.placeholder': 'Preguntá sobre tus datos…', 'cop.basedOnData': '✦ basado en tus datos',
    'cop.newChat': 'Nueva',
    'cop.error': 'No pude responder ahora. Probá de nuevo.',
    'cop.s1': '¿Qué me estuvo afectando?', 'cop.s2': '¿Qué pasa con mi glucosa después del ejercicio?',
    'cop.s3': '¿Cómo me fue cubriendo los carbohidratos?', 'cop.s4': '¿Cómo son mis noches?',
    'cop.s5': '¿Cómo estuvo mi semana?',
    // Perfil
    'perfil.title': 'Perfil', 'perfil.hi': 'Hola, {name}',
    'perfil.sensor': 'Tu sensor', 'perfil.lastReading': 'Última lectura',
    'perfil.sync': 'Sincronización', 'perfil.source': 'Fuente',
    'perfil.therapy': 'Tu terapia', 'perfil.target': 'Objetivo',
    'perfil.isf': 'Sensibilidad (ISF)', 'perfil.icr': 'Ratio (ICR)', 'perfil.basal': 'Basal',
    'perfil.inYourData': 'en tus datos: ~{v} ({n} obs.)',
    'perfil.observedNote': '"En tus datos" es lo que Orbit observó (aprendizaje bayesiano por franjas). Es referencia para conversar con tu equipo médico — no se aplica solo.',
    'perfil.team': 'Tu equipo médico', 'perfil.reportTitle': 'Reporte para tu consulta',
    'perfil.reportDesc': 'TIR, noches, hipos y coberturas observadas de los últimos 30 días — datos, no opiniones.',
    'perfil.downloadPdf': 'Descargar reporte (PDF)',
    'perfil.appearance': 'Apariencia', 'perfil.darkTheme': 'Tema oscuro',
    'perfil.language': 'Idioma', 'perfil.glucoseUnit': 'Unidad de glucosa',
    'perfil.footer': 'Orbit · copiloto metabólico',
    'perfil.loadError': 'No se pudo cargar tu perfil ahora.',
    'perfil.editTitle': 'Editar perfil', 'perfil.name': 'Nombre', 'perfil.namePh': 'Tu nombre',
    'perfil.targetField': 'Objetivo (mg/dL)', 'perfil.auto': 'Automático', 'perfil.manual': 'Manual',
    'perfil.usesLearned': 'usa lo aprendido de tus datos',
    'perfil.usesLearnedIsf': 'usa lo aprendido de tus datos (~{v} mg/dL/U, {n} obs.)',
    'perfil.usesLearnedIcr': 'usa lo aprendido de tus datos (~{v} g/U, {n} obs.)',
    'perfil.basalDose': 'Dosis diaria', 'perfil.basalHour': 'Hora habitual',
    'perfil.basalType': 'Tipo (toujeo, glargina, degludec…)',
    'perfil.basalNote': 'La basal alimenta el modelo, el contexto del copiloto y el recordatorio diario.',
    'perfil.saveError': 'No se pudo guardar.',
    // Patrones
    'pat.title': 'Patrones', 'pat.loadError': 'No se pudieron cargar tus patrones ahora.',
    'pat.tir': 'Tiempo en rango', 'pat.days7': '7 días', 'pat.daysN': '{n} días',
    'pat.distribution': 'Distribución · {n} días',
    'pat.distHigh': 'Alto · > {hi}', 'pat.distRange': 'En rango · {lo}–{hi}', 'pat.distLow': 'Bajo · < {lo}',
    'pat.gmi': 'GMI estimada', 'pat.gmiSub': '≈ HbA1c · de tu promedio',
    'pat.average': 'Promedio', 'pat.variability': 'Variabilidad', 'pat.inRange': 'En rango',
    'pat.summary': 'Resumen · {n} días', 'pat.observations': 'Observaciones',
    'pat.noPatterns': 'Sin patrones destacados — buena señal.',
    'pat.note': 'Observaciones de tus datos. Conversá los ajustes con tu equipo médico.',
    // Historial
    'hist.all': 'Todos', 'hist.meals': 'Comidas', 'hist.insulin': 'Insulina',
    'hist.exercise': 'Ejercicio', 'hist.context': 'Contexto',
    'hist.empty': 'Sin registros en este período.', 'hist.loadError': 'No se pudo cargar el historial.',
    'drive.loadError': 'No se pudo cargar el estado.',
  },
  en: {
    'nav.hoy': 'Today', 'nav.patrones': 'Patterns', 'nav.registro': 'Log',
    'nav.copiloto': 'Copilot', 'nav.perfil': 'Profile',
    'app.driveAria': 'Drive mode',
    'common.loading': 'Loading…', 'common.save': 'Save', 'common.saving': 'Saving…',
    'common.cancel': 'Cancel', 'common.delete': 'Delete', 'common.deleting': 'Deleting…',
    'common.edit': 'Edit', 'common.today': 'Today', 'common.yesterday': 'Yesterday',
    'common.loadError': "Couldn't load your data right now.",
    'common.mgdl': 'mg/dL',
    'greet.morning': 'Good morning', 'greet.afternoon': 'Good afternoon', 'greet.evening': 'Good evening',
    'hoy.now': 'Now', 'hoy.noReadings': 'No recent readings.',
    'hoy.status.low': 'Low', 'hoy.status.high': 'High', 'hoy.status.range': 'In range',
    'hoy.agoMin': '{n}m ago',
    'hoy.contextNow': 'Context now', 'hoy.activeInsulin': 'Active insulin',
    'hoy.activeCarbs': 'Active carbs', 'hoy.trend': 'Trend',
    'hoy.trend.up': 'Rising', 'hoy.trend.down': 'Falling', 'hoy.trend.flat': 'Steady',
    'hoy.basal': 'Basal', 'hoy.basalNotToday': 'not logged today',
    'hoy.tir': 'Time in range', 'hoy.tirToday': 'Today · 24 h',
    'hoy.recent': 'Recent activity',
    'hoy.brief': 'Daily brief', 'hoy.briefTeaser': '{greet} · {tir}% in range today',
    'hoy.briefDefault': 'Your day summary',
    'hoy.basalReminder': "Your basal isn't logged yet today",
    'hoy.basalReminderWhen': 'You usually take it at {h}:00',
    'hoy.log': 'Log', 'hoy.dismiss': 'Dismiss for today',
    'reg.title': 'Log', 'reg.tabLog': 'Log', 'reg.tabHistory': 'History',
    'reg.cat.comida': 'Food', 'reg.cat.insulina': 'Insulin',
    'reg.cat.ejercicio': 'Exercise', 'reg.cat.contexto': 'Context',
    'reg.scan': 'Scan meal with camera', 'reg.scanHint': 'identifies ingredients and estimates carbs',
    'reg.estimating': 'Estimating…', 'reg.analyzing': 'Analyzing components…',
    'reg.estimatedBy': 'AI estimate · review the values',
    'reg.changePhoto': 'Change photo', 'reg.reestimate': '↻ Re-estimate with the name',
    'reg.lowConfidence': '⚠︎ Low confidence — double-check before saving (or re-estimate with the right name).',
    'reg.whatAte': 'What did you eat? (e.g. 200ml milk)',
    'reg.carbs': 'Carbs', 'reg.protein': 'Protein', 'reg.fat': 'Fat',
    'reg.rapid': 'Rapid', 'reg.basal': 'Basal',
    'reg.duration': 'Duration',
    'reg.ex.walk': 'Walk', 'reg.ex.run': 'Run', 'reg.ex.bike': 'Bike',
    'reg.ex.strength': 'Strength', 'reg.ex.other': 'Other',
    'reg.int.light': 'Low', 'reg.int.moderate': 'Medium', 'reg.int.intense': 'High',
    'reg.ctxPrompt': 'Anything that might be moving your glucose today? Tag it and the copilot takes it into account.',
    'reg.ctxNote': 'Optional note (e.g. cold, tough meeting…)',
    'reg.tag.estres': 'Stress', 'reg.tag.enfermo': 'Illness', 'reg.tag.mal_sueno': 'Slept poorly',
    'reg.tag.viaje': 'Travel', 'reg.tag.alcohol': 'Alcohol', 'reg.tag.otro': 'Other',
    'reg.saveError': "Couldn't save. Try again.",
    'reg.register': 'Log',
    'ev.editMeal': 'Edit meal', 'ev.ingredients': 'Ingredients',
    'ev.day': 'Day', 'ev.time': 'Time',
    'ev.units': 'Units', 'ev.type': 'Type', 'ev.activity': 'Activity',
    'ev.durationLabel': 'Duration', 'ev.intensity': 'Intensity',
    'ev.context': 'Context', 'ev.note': 'Note',
    'ev.confirmDel': 'Delete {noun}? This cannot be undone.',
    'ev.confirmYes': 'Yes, delete',
    'ev.noun.meal': 'this food', 'ev.noun.insulin': 'this insulin entry',
    'ev.noun.exercise': 'this activity', 'ev.noun.context': 'this context tag',
    'notif.title': 'Notifications',
    'notif.loading': 'Checking for updates…',
    'notif.loadError': "Couldn't load notifications right now.",
    'notif.empty': "Nothing new yet. When Orbit finds a new pattern in your data, it lands here. 💙",
    'brief.preparing': 'Preparing your summary…', 'brief.loadError': "Couldn't load the summary right now.",
    'brief.empty': "You haven't logged anything today yet. Once you add readings or events, they'll show up here.",
    'brief.yourMeals': "Today's meals", 'brief.disclaimer': 'Orbit only describes and supports your data. It does not replace your care team.',
    'cop.greeting': "Hi 👋 I'm your copilot. I can explain your data and support you. For doses or medical decisions, always your care team. What would you like to know?",
    'cop.thinking': 'thinking…', 'cop.analyzing': 'analyzing your data…',
    'cop.placeholder': 'Ask about your data…', 'cop.basedOnData': '✦ based on your data',
    'cop.newChat': 'New',
    'cop.error': "I couldn't answer right now. Try again.",
    'cop.s1': "What's been affecting me?", 'cop.s2': 'What happens to my glucose after exercise?',
    'cop.s3': 'How did I do covering carbs?', 'cop.s4': 'What are my nights like?',
    'cop.s5': 'How was my week?',
    'perfil.title': 'Profile', 'perfil.hi': 'Hi, {name}',
    'perfil.sensor': 'Your sensor', 'perfil.lastReading': 'Last reading',
    'perfil.sync': 'Sync', 'perfil.source': 'Source',
    'perfil.therapy': 'Your therapy', 'perfil.target': 'Target',
    'perfil.isf': 'Sensitivity (ISF)', 'perfil.icr': 'Ratio (ICR)', 'perfil.basal': 'Basal',
    'perfil.inYourData': 'in your data: ~{v} ({n} obs.)',
    'perfil.observedNote': '"In your data" is what Orbit observed (Bayesian learning by time-of-day). It\'s a reference to discuss with your care team — it is not applied automatically.',
    'perfil.team': 'Your care team', 'perfil.reportTitle': 'Report for your appointment',
    'perfil.reportDesc': 'TIR, nights, lows and observed coverages from the last 30 days — data, not opinions.',
    'perfil.downloadPdf': 'Download report (PDF)',
    'perfil.appearance': 'Appearance', 'perfil.darkTheme': 'Dark theme',
    'perfil.language': 'Language', 'perfil.glucoseUnit': 'Glucose unit',
    'perfil.footer': 'Orbit · metabolic copilot',
    'perfil.loadError': "Couldn't load your profile right now.",
    'perfil.editTitle': 'Edit profile', 'perfil.name': 'Name', 'perfil.namePh': 'Your name',
    'perfil.targetField': 'Target (mg/dL)', 'perfil.auto': 'Automatic', 'perfil.manual': 'Manual',
    'perfil.usesLearned': 'uses what it learned from your data',
    'perfil.usesLearnedIsf': 'uses what it learned from your data (~{v} mg/dL/U, {n} obs.)',
    'perfil.usesLearnedIcr': 'uses what it learned from your data (~{v} g/U, {n} obs.)',
    'perfil.basalDose': 'Daily dose', 'perfil.basalHour': 'Usual time',
    'perfil.basalType': 'Type (toujeo, glargine, degludec…)',
    'perfil.basalNote': 'Basal feeds the model, the copilot context and the daily reminder.',
    'perfil.saveError': "Couldn't save.",
    'pat.title': 'Patterns', 'pat.loadError': "Couldn't load your patterns right now.",
    'pat.tir': 'Time in range', 'pat.days7': '7 days', 'pat.daysN': '{n} days',
    'pat.distribution': 'Distribution · {n} days',
    'pat.distHigh': 'High · > {hi}', 'pat.distRange': 'In range · {lo}–{hi}', 'pat.distLow': 'Low · < {lo}',
    'pat.gmi': 'Estimated GMI', 'pat.gmiSub': '≈ HbA1c · from your average',
    'pat.average': 'Average', 'pat.variability': 'Variability', 'pat.inRange': 'In range',
    'pat.summary': 'Summary · {n} days', 'pat.observations': 'Observations',
    'pat.noPatterns': 'No notable patterns — a good sign.',
    'pat.note': 'Observations from your data. Discuss any adjustments with your care team.',
    'hist.all': 'All', 'hist.meals': 'Meals', 'hist.insulin': 'Insulin',
    'hist.exercise': 'Exercise', 'hist.context': 'Context',
    'hist.empty': 'No entries in this period.', 'hist.loadError': "Couldn't load history.",
    'drive.loadError': "Couldn't load status.",
  },
}

function interpolate(str, vars) {
  if (!vars) return str
  return str.replace(/\{(\w+)\}/g, (_, k) => (vars[k] != null ? vars[k] : `{${k}}`))
}

export function getInitialLang() {
  try {
    const saved = localStorage.getItem('orbit_lang')
    if (saved && DICT[saved]) return saved
  } catch {}
  // fallback: idioma del dispositivo si lo tenemos, si no español
  try {
    const nav = (navigator.language || 'es').slice(0, 2)
    if (DICT[nav]) return nav
  } catch {}
  return 'es'
}

// ── unidad de glucosa (los datos SIEMPRE se guardan en mg/dL; esto es display) ──
export const GLUCOSE_UNITS = [
  { id: 'mgdl', label: 'mg/dL' },
  { id: 'mmol', label: 'mmol/L' },
]
const MMOL_FACTOR = 18.0182   // mmol/L = mg/dL ÷ 18.0182

function getInitialUnit() {
  try {
    const saved = localStorage.getItem('orbit_glucose_unit')
    if (saved === 'mmol' || saved === 'mgdl') return saved
  } catch {}
  return 'mgdl'
}

const LangContext = createContext({ lang: 'es', setLang: () => {}, t: (k) => k, unit: 'mgdl' })

export function LangProvider({ children }) {
  const [lang, setLangState] = useState(getInitialLang)
  const [unit, setUnitState] = useState(getInitialUnit)

  const saveToBackend = (body) => {
    try {
      fetch('/api/copilot/profile', {
        method: 'PUT', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }).catch(() => {})
    } catch {}
  }
  const setLang = useCallback((next) => {
    if (!DICT[next]) return
    setLangState(next)
    try { localStorage.setItem('orbit_lang', next) } catch {}
    saveToBackend({ ui_lang: next })   // el copiloto responde en el mismo idioma
  }, [])
  const setUnit = useCallback((next) => {
    if (next !== 'mmol' && next !== 'mgdl') return
    setUnitState(next)
    try { localStorage.setItem('orbit_glucose_unit', next) } catch {}
    saveToBackend({ glucose_unit: next })   // el copiloto usa la misma unidad
  }, [])

  const t = useCallback((key, vars) => {
    const table = DICT[lang] || DICT.es
    const val = table[key] != null ? table[key] : (DICT.es[key] != null ? DICT.es[key] : key)
    return interpolate(val, vars)
  }, [lang])

  // helpers de glucosa: gVal(número), gDelta(±), gUnit (etiqueta)
  const gUnit = unit === 'mmol' ? 'mmol/L' : 'mg/dL'
  const gVal = useCallback((mgdl) => {
    if (mgdl == null || mgdl === '' || isNaN(mgdl)) return mgdl
    return unit === 'mmol' ? (mgdl / MMOL_FACTOR).toFixed(1) : String(Math.round(mgdl))
  }, [unit])
  const gDelta = useCallback((mgdl) => {
    if (mgdl == null || isNaN(mgdl)) return mgdl
    const s = mgdl >= 0 ? '+' : ''
    return unit === 'mmol' ? s + (mgdl / MMOL_FACTOR).toFixed(1) : s + Math.round(mgdl)
  }, [unit])

  return <LangContext.Provider value={{ lang, setLang, t, unit, setUnit, gUnit, gVal, gDelta }}>{children}</LangContext.Provider>
}

export function useLang() {
  return useContext(LangContext)
}
