experiment/r3-glucose-gated-basal-bias
status: offline candidate — passed held-out gates, pending review / live deployment decision

## Objetivo
Corregir el sesgo global de sobre-predicción de `ssm_v0_ukf6_basal_ex_r1`
(confirmado en live: GLOBAL −10.3 a +60, presente en todas las ventanas limpias)
SIN romper el régimen de glucosa baja — el fallo que hundió a r2 (offset constante).

## Mecanismo: glucose-gated basal net bias correction
Offset a la producción endógena neta en dG, atenuado por una compuerta sigmoide
sobre la glucosa actual:

    offset_efectivo(G) = BASAL_NET_OFFSET × gate(G)
    gate(G) = sigmoid((G − GATE_THRESHOLD) / GATE_SOFTNESS)
    GATE_THRESHOLD = 88 mg/dL, GATE_SOFTNESS = 6 mg/dL
    BASAL_NET_OFFSET (base, tuneado solo en train) = −0.35 mg/dL/min

  gate ≈ 0 bajo ~75 · ≈ 0.5 en 88 · ≈ 1 sobre ~105.
Se evalúa sobre el G que evoluciona en la dinámica → la corrección se auto-atenúa si
la trayectoria predicha cae hacia lo bajo.

## Flag OFF = r1 exacto
BASAL_NET_BIAS_ENABLED = False por defecto → el término en dG se saltea por completo
→ modelo BYTE-IDÉNTICO a r1. Verificado (Δ = 0).

## Resultados held-out (tuneado solo en train clean windows; test = período no visto)
| Régimen (test) | sesgo +60 base→on | MAE +60 base→on | n |
|---|---|---|---|
| GLOBAL          | −9.0 → +0.4  | 16.9 → 13.1 | 193 |
| fasting         | −9.4 → −0.9  | 15.2 → 12.1 | 71  |
| basal_only      | −8.3 → +0.2  | 15.3 → 12.6 | 84  |
| stable_glucose  | −10.0 → −0.2 | 17.4 → 13.4 | 110 |
| overnight       | −12.0 → −4.3 | 16.3 → 11.5 | 53  |
| post_meal 0–2h  | −10.7 → +0.6 | 23.1 → 16.9 | 71  |
| post_meal 2–5h  | −5.9 → +1.8  | 10.9 → 9.1  | 51  |
| hypo_window     | +0.1 → +2.4  | 11.3 → 10.3 | 35  |
(high_glucose n=1 → ruido, ignorado.)

## Gate de éxito: 9/9 ✅
1 sesgo global→0 · 2 fasting · 3 stable · 4 overnight no empeora · 5 post-meal no
regresa · 6 hypo no-unsafe · 7 MAE mejora · 8 sin nuevo sesgo positivo · 9 held-out.

## Safety en hypo_window preservada
Donde r2 (offset constante) rompía (sesgo −0.2 → +8.7, MAE peor), r3 lo mantiene
seguro: sesgo +2.4 y MAE incluso MEJORA (11.3 → 10.3). La compuerta apagó la
corrección justo donde el modelo ya estaba sin sesgo.

## Interval calibration +60 — NO tocada (por diseño)
std(z)/IC90 se reportan pero NO se modificaron. La sobreconfianza de intervalos a +60
sigue presente y es un experimento de calibración APARTE, posterior a la corrección
de la media.

## Recomendación
CANDIDATE for later deploy — NOT active.
Cumple todos los gates incluida la seguridad en hipo. Pendiente de:
1. revisión humana (decisión final),
2. si se promueve: versionar y darle su propio veredicto live,
3. experimento separado de calibración de intervalos a +60.

No merge. No deploy. Flag OFF. Producción intacta.
