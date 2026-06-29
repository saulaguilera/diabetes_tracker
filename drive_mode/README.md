# ORBIT Drive Mode

**"Your glucose, safely visible while driving."** — no es un dashboard de diabetes en el auto.

Vista de seguridad glanceable de glucosa para conducir: número actual, tendencia,
estado (stable / attention / low / high / urgent), frescura del sensor y un mensaje
de seguridad corto. **Safety-first, mínima, no distractiva.**

## Qué NO es / NO muestra
- ❌ recomendaciones de dosis / correcciones / bolo / basal
- ❌ predicciones (no usa el modelo experimental)
- ❌ gráficos densos, comidas, IOB, chat, brief, notas clínicas
- ❌ instrucciones médicas ("comé 15g", "inyectá")

Mensajes permitidos (cortos, no prescriptivos): *Stable · Check when safe ·
Stop when safe · Low glucose — stop when safe · Glucose high · Data stale ·
Sensor disconnected.* Lenguaje safety-first.

## Arquitectura (aislada)
```
drive_mode/
  state.py                 # DriveModeState (fuente única de verdad, plano/serializable)
  status_logic.py          # clasificación determinista (pura, testeada) — SIN predicción
  builder.py               # arma el estado desde el pipeline de glucosa existente
  live_activity_adapter.py # mapea DriveModeState → payload para superficies nativas
  tests/                   # tests de la lógica de estado + reglas de seguridad
```
- **No depende de ORBIT Clinic** ni del modelo de predicción.
- Solo usa: última glucosa + tendencia (de la serie reciente) + frescura + conexión.
- `GET /api/copilot/drive` devuelve el payload del adapter — lo consumen todas las
  superficies (web, y a futuro Live Activity / widget / CarPlay).

## Estrategia Live Activity / widget / CarPlay
ORBIT Drive Mode está diseñado como experiencia **Live Activity / widget-first**.
El objetivo es información de seguridad glanceable mientras se conduce.

Una **app CarPlay completa NO se implementa en el MVP** a propósito, por:
- seguridad y distracción al conducir,
- restricciones de entitlement/categoría de CarPlay (no hay categoría "glucosa";
  Apple no aprueba un dashboard de salud como app de CarPlay),
- necesidad de mantener la UI mínima,
- evitar soporte a decisiones médicas mientras se maneja.

**Soporte futuro** (Lock Screen Live Activity, Dynamic Island, Apple Watch y, si
Apple lo permite, una superficie glanceable en CarPlay) debe **reutilizar
`DriveModeState` y `to_live_activity_payload()`** — la capa nativa solo renderiza,
sin lógica. Camino de integración nativa:
1. La app (Capacitor/Swift) consume `GET /api/copilot/drive`.
2. Una Live Activity de ActivityKit (Swift) renderiza el payload (número, flecha,
   pill de estado, "updated N min ago"). Mismos tokens de color (`tint`).
3. La misma vista se expone a Dynamic Island / widget / (a futuro) CarPlay.

## Estados (status_level → color)
`normal` (azul/verde) · `caution` (ámbar) · `urgent` (rojo) · `unavailable` (gris).

Umbrales (simples, documentados en `status_logic.py`): urgent_low <70 · low 70–85 ·
attention 85–100 cayendo rápido · stable 85–180 · high >180 · urgent_high >250 ·
stale >15 min · disconnected sin sensor / >45 min.
