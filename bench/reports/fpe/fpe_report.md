# FPE (grasa/proteína) — r2_fpe vs r1 (OFFLINE, no desplegado)

**Óptimo de FPE_GAIN en train = `0.0`** → el óptimo es APAGAR el FPE; el brazo ON usa el mejor positivo (`1.0`) solo para ilustrar la degradación.  ·  prot×0.5 fat×0.1, pico ~150min


## Same-period (TRAIN) — MAE/sesgo/±20 a +60 (OFF→ON)

| régimen | n | MAE +60 | sesgo +60 | ±20 | |
|---|---|---|---|---|---|
| GLOBAL | 192 | 15.0 → 15.7 | -5.0 → -7.7 | 77→75% | ⚠️ |
| post_meal_0_2h | 63 | 19.1 → 20.2 | -10.1 → -13.9 | 67→63% | ⚠️ |
| post_meal_2_5h | 72 | 16.6 → 18.0 | -4.6 → -9.8 | 71→68% | ⚠️ |
| cena | 39 | 14.1 → 16.4 | -6.6 → -10.9 | 77→69% | ⚠️ |
| alta_grasa_proteina | 91 | 16.2 → 17.5 | -6.1 → -11.0 | 74→70% | ⚠️ |
| overnight | 37 | 11.7 → 12.8 | -3.0 → -5.3 | 86→81% | ⚠️ |
| fasting | 86 | 12.3 → 12.4 | -2.3 → -2.7 | 85→85% | ≈ |
| hypo_window | 32 | 13.8 → 13.0 | +3.6 → -1.3 | 81→84% | ✅ |
| stable | 65 | 12.8 → 13.0 | -2.5 → -2.9 | 82→82% | ≈ |

## HELD-OUT (TEST) — MAE/sesgo/±20 a +60 (OFF→ON)

| régimen | n | MAE +60 | sesgo +60 | ±20 | |
|---|---|---|---|---|---|
| GLOBAL | 249 | 15.6 → 16.6 | -9.0 → -10.3 | 75→72% | ⚠️ |
| post_meal_0_2h | 50 | 22.4 → 24.3 | -7.8 → -10.7 | 60→52% | ⚠️ |
| post_meal_2_5h | 70 | 16.7 → 19.3 | -12.1 → -15.0 | 69→63% | ⚠️ |
| cena | 32 | 16.9 → 19.2 | -11.6 → -14.4 | 62→62% | ⚠️ |
| alta_grasa_proteina | 78 | 18.8 → 21.4 | -10.7 → -14.0 | 63→56% | ⚠️ |
| overnight | 50 | 16.3 → 17.6 | -10.9 → -12.6 | 60→60% | ⚠️ |
| fasting | 144 | 13.8 → 13.9 | -8.9 → -9.1 | 80→79% | ≈ |
| hypo_window | 62 | 10.6 → 10.9 | -3.0 → -4.0 | 85→89% | ⚠️ |
| stable | 99 | 15.5 → 15.6 | -11.0 → -11.2 | 76→75% | ≈ |

## Whiteness (innovaciones)

- OFF: std 1.00, ACF₁ 0.24, Ljung-Box 136
- ON : std 1.00, ACF₁ 0.24, Ljung-Box 136

## Criterio de éxito

- ❌ mejora post-meal 2-5h (test)
- ❌ no degradación global (test)
- ❌ no degradación overnight (test)
- ✅ whiteness igual o mejor
- ✅ safety hypo no empeora (test)

**Veredicto:** NO promover — revisar
