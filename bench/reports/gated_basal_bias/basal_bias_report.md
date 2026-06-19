# Experimento r3 — corrección basal neta CON COMPUERTA por glucosa (OFFLINE, ssm_v0_ukf6_basal_ex_r1)

_Solo lectura. No producción, no merge, no deploy. Convención: error = g_real − g_pred; negativo = sobre-predicción. Offset elegido **-0.35 mg/dL/min** (tuneado solo en train, ventanas ['fasting', 'basal_only', 'low_COB', 'no_recent_meal'])._

## Tuning (solo train, ventanas limpias)

| offset | sesgo+60 | MAE+60 |
|--:|--:|--:|
| +0.00 | -8.4 | 11.5 |
| -0.15 | -4.9 | 9.0 |
| -0.20 | -3.7 | 8.4 |
| -0.25 | -2.4 | 8.0 |
| -0.30 | -1.4 | 7.9 |
| -0.35 | -0.3 | 7.7 |

## Gate de éxito (test held-out)

- ✅ 1_global_bias_to_0
- ✅ 2_fasting_bias_improves
- ✅ 3_stable_bias_improves
- ✅ 4_overnight_not_worse
- ✅ 5_postmeal_no_regress
- ✅ 6_hypo_not_unsafe
- ✅ 7_mae_improves_or_neutral
- ✅ 8_no_new_positive_bias
- ✅ 9_holds_heldout

**Recomendación: CANDIDATE for later deploy (pending review + separate interval-calibration experiment)**

## TEST held-out — baseline r1 (offset 0)

| régimen | n | MAE60 | sesgo60 | RMSE60 | p90|e| | ±20 | std(z) | IC90 | ACF₁ |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| GLOBAL | 193 | 16.9 | -9.0 | 23.1 | 34.2 | 65.3% | 1.7 | 71.0% | 0.5 |
| fasting | 71 | 15.2 | -9.4 | 18.8 | 28.6 | 69.0% | 1.4 | 76.1% | 0.3 |
| basal_only | 84 | 15.3 | -8.3 | 19.5 | 34.0 | 67.9% | 1.4 | 73.8% | 0.3 |
| low_COB | 153 | 14.2 | -7.9 | 19.1 | 30.6 | 71.9% | 1.4 | 77.1% | 0.2 |
| no_recent_meal | 101 | 14.0 | -7.6 | 18.3 | 29.4 | 72.3% | 1.4 | 77.2% | 0.3 |
| no_recent_correction | 181 | 17.2 | -9.3 | 23.3 | 34.2 | 65.2% | 1.7 | 70.2% | 0.5 |
| stable_glucose | 110 | 17.4 | -10.0 | 23.3 | 35.7 | 62.7% | 1.7 | 65.5% | 0.2 |
| overnight | 53 | 16.3 | -12.0 | 20.4 | 33.1 | 60.4% | 1.5 | 67.9% | 0.2 |
| post_meal_0_2h | 71 | 23.1 | -10.7 | 30.2 | 45.0 | 50.7% | 2.2 | 56.3% | 0.4 |
| post_meal_2_5h | 51 | 10.9 | -5.9 | 15.8 | 29.4 | 80.4% | 1.2 | 84.3% | -0.0 |
| exercise | 15 | 28.7 | -18.7 | 35.8 | 66.6 | 33.3% | 2.7 | 40.0% | — |
| hypo_window | 35 | 11.3 | +0.1 | 14.8 | 25.9 | 77.1% | 1.0 | 88.6% | 0.1 |
| high_glucose | 1 | 66.6 | -66.6 | 66.6 | 66.6 | 0.0% | 5.1 | 0.0% | — |

## TEST held-out — r2 ON (offset -0.35)

| régimen | n | MAE60 | sesgo60 | RMSE60 | p90|e| | ±20 | std(z) | IC90 | ACF₁ |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| GLOBAL | 192 | 13.1 | +0.4 | 18.4 | 31.3 | 79.7% | 1.6 | 74.0% | 0.5 |
| fasting | 71 | 12.1 | -0.9 | 15.1 | 24.0 | 85.9% | 1.5 | 73.2% | 0.3 |
| basal_only | 84 | 12.6 | +0.2 | 16.3 | 24.0 | 84.5% | 1.6 | 72.6% | 0.3 |
| low_COB | 153 | 11.2 | +0.8 | 16.0 | 24.0 | 85.6% | 1.5 | 78.4% | 0.3 |
| no_recent_meal | 101 | 11.5 | +0.6 | 15.4 | 24.0 | 86.1% | 1.5 | 76.2% | 0.3 |
| no_recent_correction | 180 | 13.3 | +0.2 | 18.5 | 32.2 | 78.9% | 1.6 | 73.3% | 0.5 |
| stable_glucose | 110 | 13.4 | -0.2 | 19.0 | 33.1 | 79.1% | 1.7 | 75.5% | 0.3 |
| overnight | 53 | 11.5 | -4.3 | 14.9 | 24.6 | 84.9% | 1.4 | 73.6% | 0.1 |
| post_meal_0_2h | 70 | 16.9 | +0.6 | 23.5 | 42.2 | 67.1% | 1.9 | 67.1% | 0.4 |
| post_meal_2_5h | 51 | 9.1 | +1.8 | 14.1 | 23.1 | 88.2% | 1.4 | 84.3% | 0.0 |
| exercise | 15 | 20.7 | -6.0 | 26.9 | 46.6 | 60.0% | 2.1 | 60.0% | — |
| hypo_window | 35 | 10.3 | +2.4 | 13.9 | 24.6 | 85.7% | 1.2 | 82.9% | 0.1 |
| high_glucose | 1 | 42.2 | -42.2 | 42.2 | 42.2 | 0.0% | 3.2 | 0.0% | — |
---

## Interpretación

**La compuerta por glucosa resolvió el problema de r2 — gate 9/9, candidato limpio.**
Misma corrección basal neta, pero atenuada suavemente cerca de hipo (sigmoide,
centro 88, suavidad 6: gate≈0 bajo 75, ≈1 sobre 105). Tuneada solo en train sobre
ventanas limpias (offset base −0.35; más grande que r2 porque la compuerta atenúa),
validada held-out.

| Régimen (test held-out) | sesgo60 base→on | MAE60 base→on | n |
|---|---|---|---|
| GLOBAL | −9.0 → **+0.4** | 16.9 → **13.1** | 193 |
| fasting | −9.4 → −0.9 | 15.2 → 12.1 | 71 |
| basal_only | −8.3 → +0.2 | 15.3 → 12.6 | 84 |
| stable_glucose | −10.0 → −0.2 | 17.4 → 13.4 | 110 |
| overnight | −12.0 → −4.3 | 16.3 → 11.5 | 53 |
| post_meal 0–2h | −10.7 → +0.6 | 23.1 → 16.9 | 71 |
| **hypo_window** | **+0.1 → +2.4** | **11.3 → 10.3** | 35 |

**La diferencia clave vs r2:** en `hypo_window` r2 rompía (sesgo −0.2 → **+8.7**, MAE
peor). r3 lo **mantiene seguro** (sesgo +2.4, y MAE incluso **mejora** 11.3→10.3). La
compuerta apagó la corrección justo donde el modelo ya estaba bien.

**Bonus:** como la compuerta deja pasar corrección PLENA en rango normal/alto (con un
base mayor), el MAE global mejora **más** que en r2 (16.9→13.1 vs 14.7), sin dañar lo bajo.

Es la analogía del volante: se endereza a alta velocidad (rango normal/alto) y se
suelta al estacionar (cerca de hipo).

### Gate de éxito: 9/9 ✅
sesgo global→0 · fasting · stable · overnight no empeora · post-meal no regresa ·
**hypo no-unsafe** · MAE mejora · sin nuevo sesgo positivo · held-out.

## Recomendación: **CANDIDATE for later deploy** — pero NO desplegar todavía
Cumple todos los gates incluido safety en hipo. Es el primer candidato real. Aun así,
queda PENDIENTE:
1. **Revisión humana** (tu decisión final).
2. **Veredicto live** propio si se decide promover (versionar → `..._r2`/`_gbias`).
3. **Calibración de intervalos a +60** (std(z)/IC90 — NO tocada acá, por diseño): la
   sobreconfianza sigue ahí; es el siguiente experimento, después de la media.

Flag OFF (OFF = r1 exacto). No merge. No deploy.
