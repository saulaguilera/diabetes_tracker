# Solicitud de entitlement CarPlay — ORBIT (borrador para Apple)

Form: https://developer.apple.com/contact/carplay/  ·  Entitlement: `com.apple.developer.carplay-audio`

> Estrategia: ORBIT no encaja en una categoría de salud (no existe en CarPlay), así
> que se solicita la categoría **audio**, que es la vía por la que apps de CGM
> (monitoreo continuo de glucosa) ya están en CarPlay. Encuadre: **visor secundario
> de glucosa**, glanceable, **sin dosificación ni decisiones médicas**.

---

## Texto sugerido (EN, para el formulario)

**App name:** ORBIT — Glucose safety companion

**What does your app do, and why does it need CarPlay?**
ORBIT is a companion app for people with type 1 diabetes. The requested CarPlay
feature, "ORBIT Drive Mode," is a **secondary, glanceable display of the user's
current glucose value, trend, and a short non-prescriptive safety message**
(e.g., "Stable", "Check when safe", "Stop when safe"). For a driver with type 1
diabetes, low glucose is a real safety hazard; a quick, low-distraction glance at
current glucose and trend helps them decide whether to pull over — **without
picking up the phone**.

**Why this category / why the audio entitlement?**
CarPlay has no glucose/health category. ORBIT Drive Mode presents the same kind of
minimal, glanceable glucose information that existing continuous-glucose-monitoring
apps already surface in CarPlay. The UI is intentionally minimal (one value, one
arrow, one short message) and refreshes passively.

**Safety / scope (important):**
- It is **display only**: it shows current glucose, trend, sensor freshness and a
  short safety message.
- It does **NOT** provide insulin dosing, bolus calculations, corrections, or any
  treatment recommendation.
- It does **NOT** show predictions/forecasts or complex charts while driving.
- If sensor data is missing or stale, it shows "Sensor disconnected" / "Data
  stale" and never asserts that glucose is safe.
- Messaging is non-prescriptive and safety-first ("Stop when safe", not "eat X").

**How is distraction minimized?**
Single large number + trend arrow + one short status line, updated passively
(~30s). No interactive controls beyond what CarPlay templates allow. No data entry.

**Data source:** the user's own CGM data, already in the ORBIT account; the CarPlay
scene reads a read-only endpoint and renders it. No new data collection.

---

## Checklist antes de enviar
- [ ] Cuenta Apple Developer de pago activa.
- [ ] App ID de ORBIT con el entitlement habilitado (tras aprobación).
- [ ] App con build en TestFlight/App Store (ayuda a la aprobación).
- [ ] Capturas de la pantalla Drive Mode (mostrar lo mínima/no-distractiva que es).
- [ ] Enfatizar: visor secundario, sin dosificación, sin predicción.

## Expectativa de tiempo
Apple **no publica SLA**. Anecdóticamente: semanas a meses, con silencios
frecuentes (reenviar/insistir). Es el paso de mayor incertidumbre — por eso el
código ya queda listo para no perder tiempo cuando llegue.
