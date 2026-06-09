# Diagnóstico de sesgo — `ssm_v0_ukf6_basal_ex_r1` (OFFLINE, solo lectura)

_Convención: error = g_real − g_pred. **Negativo = sobre-predicción.** Outliers |error|>100 excluidos (artefactos)._

## B. REPLAY — comportamiento de r1 (params de producción), N alto sobre histórico

_Replay cold-start sobre 290 timestamps; R_BASE=1.2, R_MARD=0.027. (No es el filtro continuo live.)_

| régimen | n | MAE30 | MAE60 | sesgo30 | sesgo60 | mediana60 | p90|e|60 | std(z) | ACF₁ | cob90 |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| GLOBAL | 289 | 11.5 | 15.1 | -4.6 | -7.1 | -8.6 | 30.0 | 1.6 | -0.2 | 78.9% |
| fasting | 95 | 8.2 | 12.1 | -4.1 | -6.3 | -7.8 | 25.1 | 1.2 | -0.0 | 84.2% |
| basal_only | 107 | 8.5 | 12.9 | -4.9 | -7.8 | -9.3 | 25.6 | 1.2 | -0.1 | 81.3% |
| overnight | 63 | 9.5 | 13.6 | -6.0 | -10.1 | -10.1 | 30.0 | 1.3 | -0.1 | 79.4% |
| low_IOB | 212 | 9.1 | 13.5 | -4.9 | -8.7 | -9.1 | 27.4 | 1.3 | -0.1 | 81.1% |
| low_COB | 227 | 9.2 | 12.9 | -4.8 | -7.1 | -8.1 | 27.2 | 1.2 | -0.1 | 82.4% |
| no_recent_meal | 145 | 8.2 | 12.2 | -4.6 | -6.5 | -7.9 | 25.1 | 1.2 | -0.0 | 84.8% |
| no_recent_correction | 264 | 10.8 | 14.0 | -3.4 | -6.1 | -7.9 | 28.4 | 1.4 | -0.0 | 81.1% |
| stable_glucose | 203 | 10.6 | 14.5 | -5.3 | -6.7 | -9.2 | 27.0 | 1.5 | -0.1 | 81.3% |
| post_meal_0_2h | 108 | 16.4 | 19.5 | -3.9 | -7.0 | -8.7 | 46.0 | 2.1 | -0.2 | 68.5% |
| post_meal_2_5h | 86 | 8.9 | 12.9 | -5.9 | -8.2 | -9.2 | 25.5 | 1.2 | -0.1 | 86.0% |
| exercise | 26 | 14.2 | 21.7 | -6.5 | -10.2 | -15.9 | 46.5 | 2.0 | — | 50.0% |

## A. LIVE — predicciones reales en tiempo real, por versión (sin mezclar)

> ⚠️ `ssm_v0_ukf6_basal_ex_r1` aún no tiene datos LIVE en este export. La versión live más reciente es `ssm_v0_ukf6_basal_ex`. Se listan las de N útil para mostrar **persistencia del sesgo entre versiones**.


### `ssm_v0_ukf6_basal`  (n=1509, outliers excl=0)

| régimen | n | MAE30 | MAE60 | sesgo30 | sesgo60 | mediana60 | p90|e|60 | std(z) | ACF₁ | cob90 |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| GLOBAL | 1483 | 11.5 | 17.4 | -6.8 | -11.3 | -11.9 | 35.0 | 1.3 | 0.8 | 84.5% |
| fasting | 586 | 9.7 | 14.9 | -6.8 | -9.6 | -11.1 | 27.2 | 1.1 | 0.7 | 90.1% |
| basal_only | 693 | 9.8 | 15.1 | -6.9 | -10.2 | -11.8 | 27.1 | 1.1 | 0.7 | 90.6% |
| overnight | 359 | 10.5 | 15.6 | -7.1 | -12.7 | -12.3 | 31.2 | 1.2 | 0.7 | 87.2% |
| low_IOB | 1338 | 10.5 | 16.2 | -7.1 | -11.3 | -12.1 | 31.1 | 1.2 | 0.8 | 86.8% |
| low_COB | 1229 | 9.9 | 15.5 | -7.1 | -10.4 | -11.6 | 29.7 | 1.2 | 0.7 | 88.4% |
| no_recent_meal | 855 | 9.7 | 15.1 | -7.0 | -9.9 | -11.6 | 27.6 | 1.1 | 0.7 | 90.3% |
| no_recent_correction | 1353 | 10.6 | 16.3 | -6.4 | -10.7 | -11.8 | 31.1 | 1.2 | 0.7 | 86.8% |
| stable_glucose | 1012 | 10.0 | 16.0 | -6.6 | -10.3 | -11.6 | 30.4 | 1.2 | 0.6 | 88.0% |
| post_meal_0_2h | 445 | 15.7 | 22.9 | -6.6 | -14.3 | -14.5 | 54.4 | 1.7 | 0.7 | 72.1% |
| post_meal_2_5h | 452 | 9.6 | 15.4 | -6.9 | -10.5 | -11.9 | 29.0 | 1.2 | 0.6 | 89.4% |
| exercise | 77 | 14.9 | 22.3 | -7.7 | -16.8 | -19.1 | 42.5 | 1.5 | 0.2 | 74.0% |

### `mc_ar_gp_pmm_v1`  (n=183, outliers excl=5)

| régimen | n | MAE30 | MAE60 | sesgo30 | sesgo60 | mediana60 | p90|e|60 | std(z) | ACF₁ | cob90 |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| GLOBAL | 176 | 18.5 | 25.6 | 6.2 | +10.0 | +9.0 | 56.0 | 1.7 | 0.0 | 69.9% |
| fasting | 22 | 11.4 | 22.8 | 5.3 | +18.1 | +15.5 | 41.0 | 1.5 | — | 68.2% |
| overnight | 17 | 15.3 | 29.2 | 6.6 | +1.4 | +2.0 | 70.0 | 1.5 | — | 76.5% |
| low_IOB | 3 | 21.0 | 37.3 | 7.7 | +5.3 | +29.0 | 48.0 | 2.1 | — | 0.0% |
| low_COB | 114 | 16.0 | 23.9 | 6.0 | +12.3 | +9.5 | 54.0 | 1.7 | 0.2 | 71.9% |
| no_recent_meal | 35 | 10.5 | 18.8 | 6.1 | +13.1 | +10.0 | 36.0 | 1.3 | -0.1 | 80.0% |
| no_recent_correction | 145 | 17.1 | 24.2 | 7.2 | +11.2 | +9.0 | 54.0 | 1.7 | 0.1 | 70.3% |
| stable_glucose | 139 | 17.2 | 23.8 | 7.5 | +11.5 | +9.0 | 54.0 | 1.6 | 0.1 | 72.7% |
| post_meal_0_2h | 119 | 21.2 | 28.6 | 5.1 | +7.7 | +9.0 | 62.0 | 1.8 | 0.1 | 65.5% |
| post_meal_2_5h | 35 | 13.8 | 17.1 | 10.9 | +12.8 | +7.0 | 42.0 | 1.4 | 0.2 | 85.7% |
| exercise | 24 | 18.2 | 29.8 | 7.2 | +5.5 | +8.5 | 42.0 | 1.6 | — | 70.8% |

### `ssm_v0_ukf6_basal_ex`  (n=128, outliers excl=0)

| régimen | n | MAE30 | MAE60 | sesgo30 | sesgo60 | mediana60 | p90|e|60 | std(z) | ACF₁ | cob90 |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| GLOBAL | 123 | 10.7 | 15.3 | -7.1 | -10.6 | -11.2 | 28.2 | 1.1 | 0.7 | 90.2% |
| fasting | 24 | 8.0 | 16.6 | -7.6 | -3.1 | -8.4 | 29.2 | 1.2 | — | 83.3% |
| basal_only | 44 | 8.9 | 15.1 | -8.7 | -6.6 | -10.3 | 26.3 | 1.1 | 0.5 | 90.9% |
| overnight | 34 | 13.7 | 15.0 | -5.9 | -9.8 | -9.9 | 33.2 | 1.1 | 0.6 | 88.2% |
| low_IOB | 111 | 10.0 | 14.8 | -6.9 | -10.0 | -11.1 | 26.5 | 1.0 | 0.7 | 91.9% |
| low_COB | 99 | 8.9 | 14.6 | -7.7 | -10.1 | -11.1 | 26.6 | 1.0 | 0.7 | 91.9% |
| no_recent_meal | 61 | 9.7 | 16.5 | -9.5 | -10.4 | -11.2 | 29.2 | 1.2 | 0.5 | 86.9% |
| no_recent_correction | 123 | 10.7 | 15.3 | -7.1 | -10.6 | -11.2 | 28.2 | 1.1 | 0.7 | 90.2% |
| stable_glucose | 96 | 10.5 | 14.7 | -7.3 | -9.0 | -10.7 | 28.2 | 1.1 | 0.6 | 90.6% |
| post_meal_0_2h | 45 | 13.5 | 14.8 | -4.6 | -10.5 | -10.9 | 29.7 | 1.1 | 0.6 | 91.1% |
| post_meal_2_5h | 54 | 9.4 | 15.0 | -9.2 | -14.1 | -12.9 | 26.6 | 1.1 | 0.6 | 92.6% |
| exercise | 30 | 15.1 | 20.7 | -9.7 | -15.7 | -16.9 | 45.4 | 1.5 | 0.7 | 76.7% |

### `ssm_v0_ukf6`  (n=43, outliers excl=0)

| régimen | n | MAE30 | MAE60 | sesgo30 | sesgo60 | mediana60 | p90|e|60 | std(z) | ACF₁ | cob90 |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| GLOBAL | 43 | 28.1 | 38.6 | -19.9 | -29.3 | -35.9 | 65.0 | 2.0 | 0.1 | 44.2% |
| fasting | 4 | 27.4 | 21.9 | -27.4 | -21.9 | -22.3 | 39.5 | 1.3 | — | 75.0% |
| overnight | 7 | 29.8 | 54.0 | -28.8 | -29.9 | -37.3 | 84.5 | 2.8 | — | 28.6% |
| low_COB | 24 | 27.3 | 38.7 | -25.4 | -27.2 | -32.6 | 73.1 | 2.0 | — | 45.8% |
| no_recent_meal | 9 | 22.1 | 31.8 | -22.1 | -20.3 | -26.8 | 54.5 | 1.7 | — | 66.7% |
| no_recent_correction | 43 | 28.1 | 38.6 | -19.9 | -29.3 | -35.9 | 65.0 | 2.0 | 0.1 | 44.2% |
| stable_glucose | 33 | 26.9 | 37.3 | -19.5 | -30.4 | -37.3 | 54.5 | 2.0 | 0.4 | 42.4% |
| post_meal_0_2h | 28 | 30.2 | 42.1 | -19.1 | -31.6 | -38.3 | 76.1 | 2.2 | — | 35.7% |
| post_meal_2_5h | 11 | 22.7 | 35.6 | -19.3 | -26.2 | -31.3 | 54.5 | 1.8 | — | 54.5% |
| exercise | 5 | 27.9 | 39.1 | -27.9 | -34.1 | -41.3 | 54.5 | 1.9 | — | 20.0% |

### `ssm_v0_ukf6_tuned1`  (n=29, outliers excl=0)

| régimen | n | MAE30 | MAE60 | sesgo30 | sesgo60 | mediana60 | p90|e|60 | std(z) | ACF₁ | cob90 |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| GLOBAL | 29 | 12.7 | 13.9 | -3.3 | -1.7 | -1.9 | 26.7 | 0.9 | — | 93.1% |
| fasting | 8 | 7.5 | 11.2 | -5.9 | -6.0 | -6.7 | 26.7 | 0.7 | — | 100.0% |
| overnight | 2 | 5.8 | 12.1 | 5.8 | +12.1 | +12.1 | 13.4 | 0.6 | — | 100.0% |
| low_COB | 17 | 8.1 | 10.1 | -4.6 | -2.4 | -1.9 | 18.5 | 0.6 | — | 100.0% |
| no_recent_meal | 11 | 6.7 | 10.6 | -3.8 | -3.0 | -6.1 | 18.5 | 0.7 | — | 100.0% |
| no_recent_correction | 21 | 13.2 | 15.2 | -1.5 | -0.5 | +4.0 | 26.7 | 1.0 | — | 90.5% |
| stable_glucose | 21 | 12.0 | 14.6 | -6.0 | -3.6 | -6.1 | 26.7 | 0.9 | — | 90.5% |
| post_meal_0_2h | 15 | 17.9 | 17.1 | -2.8 | -0.4 | +4.0 | 36.2 | 1.0 | — | 86.7% |
| post_meal_2_5h | 6 | 6.6 | 9.7 | -1.1 | +1.0 | +3.0 | 18.2 | 0.6 | — | 100.0% |
---

## Interpretación

**El sesgo negativo es GLOBAL y estructural (opción 1).** Aparece en TODOS los
regímenes limpios — fasting (−6.3), basal-only (−7.8), low-COB (−7.1),
stable-glucose (−6.7) en el replay de r1; y −9 a −11 en las mismas ventanas del
modelo live `ssm_v0_ukf6_basal` (n=1483). No es post-meal (post-meal tiene el
mismo nivel que el resto), no es regímen-específico.

**Tres evidencias de que es la dinámica del SSM, no el sensor/lag (descarta op. 4):**
1. El sesgo persiste con **glucosa estable** (|roc|≤0.5) y en **ayunas** — un lag
   daría errores que se cancelan en la media, no un offset constante.
2. **Persiste entre versiones** del SSM (`_basal`, `_ex`, replay r1) → no es tuning.
3. El modelo de **otra familia** (`mc_ar_gp_pmm_v1`) tiene el sesgo de **signo
   OPUESTO** (+10) sobre los mismos datos → el sensor/datos no explican el signo;
   lo explica un supuesto del SSM.

**Componente secundario overnight/basal (parte de op. 2):** overnight (−10.1
replay / −12.7 live) y exercise (−16.8 live) son MÁS negativos que el offset
global → encima del sesgo base hay un extra nocturno (dawn/EGP) y post-ejercicio.

**Localización fisiológica:** en ventanas fasting/basal-only no hay carbos ni bolo
reciente; los únicos motores de glucosa en el modelo son **EGP (producción
hepática, +) − efecto basal (−) − captación no-insulínica (−)**. Que el modelo
prediga ~7–10 mg/dL de MÁS implica que su **producción neta endógena está un poco
alta**: EGP_BASAL demasiado alta y/o el efecto de la basal subestimado.

**Hallazgo aparte (calibración, no sesgo):** en el replay de r1 std(z) es alto
(1.16–2.07, peor post-meal 2.07) → con la R reducida, los intervalos a +60 quedan
**angostos** (sobreconfianza). Es la tensión que ya marcó el harness held-out. Es
un problema de **ancho de intervalo** (Q/σ), distinto del sesgo (offset de la media).

### Veredicto de clasificación
- ✅ **(1) Global en todas las ventanas limpias** — causa dominante.
- ⚠️ (2) Componente extra overnight/dawn y post-ejercicio, encima del global.
- ❌ (3) post-meal — no es el driver.
- ❌ (4) sensor/lag — descartado por la evidencia de arriba.
- ❌ (5) regímen-específico — no.

## Próximo experimento recomendado (offline, no acá)
Corregir el **balance de producción endógena neta** del SSM con una perilla única
y apagable, tuneada SOLO en train sobre ventanas **fasting / basal-only** (donde el
sesgo es más limpio), validada held-out por régimen:

- candidato primario: bajar levemente **EGP_BASAL** (o equivalentemente reforzar el
  efecto basal/NIM) ~0.1–0.15 mg/dL/min, que compensa ~6–9 mg/dL a +60;
- chequear que **no** induzca sesgo positivo (under-prediction) ni empeore las
  ventanas de hipo (safety), y revisar overnight por separado (posible 2ª perilla
  dawn);
- por separado, recalibrar el **ancho de intervalo a +60** (inflar Q con el horizonte
  o σ post-hoc) para llevar std(z)→1.

Mismo método disciplinado: branch, apagable, held-out, por régimen. _Esto es
diagnóstico/calibración del modelo; no implica ningún cambio de tratamiento ni dosis._
