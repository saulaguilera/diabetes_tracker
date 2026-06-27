# Experimento r4 — calibración de intervalos +60 (OFFLINE, ssm_v0_ukf6_basal_ex_r2_gated_bias)

_Post-hoc σ multiplier. NO cambia media/MAE/sesgo (g_pred intacto). Ideal: IC90≈90%, std(z)≈1. m(+60) tuneado solo en train = **1.64**._

## Gate de éxito (test held-out)

- ✅ 1_IC90_toward_90
- ✅ 2_stdz_toward_1
- ✅ 3_mean_unchanged
- ✅ 4_mae_unchanged
- ✅ 5_bias_unchanged
- ✅ 6_hypo_cov_not_worse
- ✅ 7_intervals_not_absurd
- ✅ 8_holds_heldout

**Recomendación: CANDIDATE for later deploy (post-hoc +60 sigma multiplier; mean untouched)**

## TEST held-out — baseline (σ actual)

| régimen | n | std(z) | IC50 | IC80 | IC90 | IC95 | ancho90 |
|---|--:|--:|--:|--:|--:|--:|--:|
| GLOBAL | 263 | 1.59 | 41% | 70% | 81% | 86% | 33 |
| fasting | 141 | 1.49 | 40% | 70% | 83% | 87% | 31 |
| basal_only | 162 | 1.51 | 37% | 69% | 82% | 87% | 31 |
| stable_glucose | 163 | 1.47 | 46% | 74% | 83% | 87% | 32 |
| overnight | 67 | 1.43 | 33% | 67% | 79% | 84% | 32 |
| post_meal_0_2h | 54 | 2.06 | 43% | 65% | 76% | 78% | 37 |
| post_meal_2_5h | 68 | 1.36 | 44% | 72% | 82% | 88% | 32 |
| hypo_window | 42 | 1.67 | 29% | 69% | 81% | 88% | 33 |

## TEST held-out — calibrado (σ ×1.64 a +60)

| régimen | n | std(z) | IC50 | IC80 | IC90 | IC95 | ancho90 |
|---|--:|--:|--:|--:|--:|--:|--:|
| GLOBAL | 263 | 0.97 | 63% | 87% | 92% | 94% | 53 |
| fasting | 141 | 0.91 | 64% | 89% | 94% | 95% | 52 |
| basal_only | 162 | 0.92 | 62% | 88% | 94% | 94% | 52 |
| stable_glucose | 163 | 0.90 | 67% | 88% | 94% | 95% | 53 |
| overnight | 67 | 0.87 | 66% | 85% | 94% | 96% | 52 |
| post_meal_0_2h | 54 | 1.25 | 56% | 78% | 85% | 89% | 60 |
| post_meal_2_5h | 68 | 0.83 | 68% | 90% | 94% | 94% | 52 |
| hypo_window | 42 | 1.02 | 67% | 88% | 93% | 93% | 55 |
---

## Interpretación
El multiplicador único de σ a +60 (**m=1.64**, tuneado solo en train, validado en el
período test que no vio) **calibra los intervalos** sin tocar la media:

- GLOBAL: **IC90 81% → 92%**, **std(z) 1.59 → 0.97** (en el blanco).
- Mejora en TODOS los regímenes (fasting/basal/stable/overnight/post-meal/hypo).
- **hypo_window: IC90 81% → 93%** (seguridad preservada).
- media/MAE/sesgo SIN CAMBIO (imposible que cambien: g_pred intacto).
- ancho del IC90 ~33 → ~53 mg/dL (±26): más honesto, no absurdo.

**Matiz:** `post_meal_0_2h` queda en IC90 85% (std 1.25) — las comidas generan los
errores más grandes y un multiplicador único las sub-infla un poco. Igual MEJORA
(76→85%). Si se quisiera afinar, Option C (multiplicador regime-aware, más inflación
post-meal) cerraría esa brecha — pero no es necesario: el multiplicador simple ya
pasa 8/8.

## Gate: 8/8 ✅
## Recomendación: CANDIDATE for later deploy — NO desplegar (flag OFF)
Si se promueve: versionar (p.ej. `..._r3_cal60`), flag ON con SIGMA_MULT_60=1.64,
y darle su propio veredicto live. NO toca la media; es ortogonal a r2_gated_bias.
