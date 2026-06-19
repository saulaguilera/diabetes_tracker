# Experimento r2 — corrección del sesgo basal neto (OFFLINE, ssm_v0_ukf6_basal_ex_r1)

_Solo lectura. No producción, no merge, no deploy. Convención: error = g_real − g_pred; negativo = sobre-predicción. Offset elegido **-0.20 mg/dL/min** (tuneado solo en train, ventanas ['fasting', 'basal_only', 'low_COB', 'no_recent_meal'])._

## Tuning (solo train, ventanas limpias)

| offset | sesgo+60 | MAE+60 |
|--:|--:|--:|
| +0.00 | -8.5 | 11.5 |
| -0.05 | -6.4 | 10.2 |
| -0.10 | -4.2 | 9.2 |
| -0.15 | -2.0 | 8.4 |
| -0.20 | +0.4 | 8.1 |
| -0.25 | +2.8 | 8.2 |

## Gate de éxito (test held-out)

- ✅ 1_global_bias_to_0
- ✅ 2_fasting_bias_improves
- ✅ 3_stable_bias_improves
- ✅ 4_overnight_not_worse
- ✅ 5_postmeal_no_regress
- ❌ 6_hypo_not_unsafe
- ✅ 7_mae_improves_or_neutral
- ❌ 8_no_new_positive_bias
- ✅ 9_holds_heldout

**Recomendación: KEEP OFFLINE — bias improves but some secondary gate failed; needs refinement**

## TEST held-out — baseline r1 (offset 0)

| régimen | n | MAE60 | sesgo60 | RMSE60 | p90|e| | ±20 | std(z) | IC90 | ACF₁ |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| GLOBAL | 193 | 17.0 | -9.0 | 23.1 | 34.2 | 65.3% | 1.7 | 71.0% | 0.5 |
| fasting | 71 | 15.1 | -9.5 | 18.8 | 28.6 | 69.0% | 1.4 | 76.1% | 0.3 |
| basal_only | 84 | 15.2 | -8.3 | 19.5 | 34.0 | 67.9% | 1.4 | 73.8% | 0.3 |
| low_COB | 153 | 14.2 | -8.0 | 19.1 | 30.6 | 71.9% | 1.4 | 77.1% | 0.2 |
| no_recent_meal | 101 | 14.0 | -7.7 | 18.3 | 29.4 | 72.3% | 1.4 | 77.2% | 0.3 |
| no_recent_correction | 181 | 17.2 | -9.3 | 23.3 | 34.2 | 65.2% | 1.7 | 70.2% | 0.5 |
| stable_glucose | 110 | 17.4 | -10.1 | 23.3 | 35.7 | 62.7% | 1.7 | 65.5% | 0.2 |
| overnight | 53 | 16.2 | -12.1 | 20.4 | 33.1 | 60.4% | 1.5 | 67.9% | 0.2 |
| post_meal_0_2h | 71 | 23.1 | -10.7 | 30.2 | 45.0 | 50.7% | 2.2 | 56.3% | 0.4 |
| post_meal_2_5h | 51 | 11.1 | -6.0 | 15.8 | 29.4 | 80.4% | 1.2 | 84.3% | -0.0 |
| exercise | 15 | 28.7 | -18.7 | 35.8 | 66.6 | 33.3% | 2.7 | 40.0% | — |
| hypo_window | 35 | 11.4 | -0.2 | 14.9 | 25.9 | 77.1% | 1.0 | 88.6% | 0.1 |
| high_glucose | 1 | 66.6 | -66.6 | 66.6 | 66.6 | 0.0% | 5.1 | 0.0% | — |

## TEST held-out — r2 ON (offset -0.20)

| régimen | n | MAE60 | sesgo60 | RMSE60 | p90|e| | ±20 | std(z) | IC90 | ACF₁ |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| GLOBAL | 193 | 14.7 | +0.4 | 21.0 | 34.7 | 77.2% | 1.5 | 79.8% | 0.5 |
| fasting | 71 | 12.8 | +0.3 | 16.2 | 24.5 | 83.1% | 1.2 | 84.5% | 0.3 |
| basal_only | 84 | 13.4 | +1.3 | 17.5 | 29.2 | 81.0% | 1.3 | 82.1% | 0.3 |
| low_COB | 153 | 12.1 | +1.3 | 17.3 | 24.5 | 84.3% | 1.2 | 85.6% | 0.2 |
| no_recent_meal | 101 | 12.4 | +1.9 | 16.6 | 25.6 | 82.2% | 1.2 | 84.2% | 0.3 |
| no_recent_correction | 181 | 14.7 | +0.1 | 21.1 | 34.7 | 76.2% | 1.5 | 79.0% | 0.5 |
| stable_glucose | 110 | 14.6 | -0.9 | 20.7 | 35.0 | 75.5% | 1.5 | 77.3% | 0.2 |
| overnight | 53 | 13.1 | -2.5 | 16.9 | 28.3 | 75.5% | 1.2 | 79.2% | 0.2 |
| post_meal_0_2h | 71 | 19.9 | -1.4 | 27.8 | 43.0 | 66.2% | 2.0 | 70.4% | 0.4 |
| post_meal_2_5h | 51 | 10.0 | +3.0 | 15.2 | 23.1 | 84.3% | 1.1 | 86.3% | -0.0 |
| exercise | 15 | 25.6 | -8.7 | 31.0 | 50.2 | 46.7% | 2.4 | 60.0% | — |
| hypo_window | 35 | 12.2 | +8.7 | 17.0 | 32.0 | 82.9% | 1.2 | 85.7% | 0.1 |
| high_glucose | 1 | 50.2 | -50.2 | 50.2 | 50.2 | 0.0% | 3.9 | 0.0% | — |
---

## Interpretación

**Hipótesis CONFIRMADA — el sesgo vive en el término de producción endógena neta.**
Un único offset constante (−0.20 mg/dL/min, tuneado **solo en train** sobre ventanas
limpias) **elimina el sesgo global held-out** (GLOBAL −9.0 → **+0.4**) y **mejora el
MAE en TODOS los regímenes sustanciales**:

| Régimen (test) | sesgo60 base→on | MAE60 base→on | n |
|---|---|---|---|
| GLOBAL | −9.0 → **+0.4** | 17.0 → **14.7** | 193 |
| fasting | −9.5 → +0.3 | 15.1 → 12.8 | 71 |
| basal_only | −8.3 → +1.3 | 15.2 → 13.4 | 84 |
| stable_glucose | −10.1 → −0.9 | 17.4 → 14.6 | 110 |
| overnight | −12.1 → −2.5 | 16.2 → 13.1 | 53 |
| post_meal 0–2h | −10.7 → −1.4 | 23.1 → 19.9 | 71 |

Generaliza held-out: la mejora vista en train se sostiene en el período de test que
el tuneo no vio. **R corrigió dispersión; este offset corrige la media.**

**El único problema real: sobre-corrige el régimen de glucosa baja.**
- `hypo_window` (g_actual<80, n=35): el baseline ya estaba **sin sesgo** (−0.2) →
  el offset constante lo empuja a **+8.7** (ahora sub-predice en lo bajo) y empeora
  MAE (11.4 → 12.2). Falla los gates 6 y 8.
- (`high_glucose` n=**1** → un solo punto, ruido, se ignora.)

**Causa:** el sesgo **no es constante, es dependiente de la glucosa**: está presente
en niveles normales/altos pero **ausente en lo bajo** (coherente con la contra-
regulación — el hígado sube EGP cuando hay hipo, así que ahí el supuesto del modelo
ya es correcto). Un offset **constante** no puede capturar eso.

### Gate de éxito: 7/9
✅ sesgo global → 0 · ✅ fasting · ✅ stable · ✅ overnight no empeora · ✅ post-meal
no regresa · ✅ MAE mejora · ✅ held-out · ❌ hypo no-unsafe (sobre-corrige lo bajo) ·
❌ sin nuevo sesgo positivo (hypo +8.7).

## Recomendación: **KEEP OFFLINE — no desplegar**
El mecanismo es el correcto (confirma EGP/basal) y el resultado global es fuerte,
pero el offset constante es **demasiado romo**: rompe el régimen de glucosa baja.
NO promover a deploy.

**Refinamiento para el próximo experimento** (sigue siendo ~una perilla):
- corrección **dependiente de glucosa**: aplicar el offset solo por encima de un
  umbral (p.ej. G>90) o atenuarlo cerca de lo bajo (piso de seguridad) → conserva la
  ganancia global (−9→0, MAE −2.3) **sin** dañar las ventanas de hipo.

_(La sobreconfianza de intervalos a +60 — std(z)/IC90 — NO se tocó acá, como se
pidió. Es un experimento de calibración aparte, posterior a la corrección de la media.)_
