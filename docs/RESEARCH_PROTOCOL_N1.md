# Protocolo de estudio N=1 — Predicción de glucosa con SSM-UKF

> **Estado**: borrador para **pre-registro**. Congelar antes de mirar resultados del bench.
> **Sujeto**: un individuo (autoestudio), T1D, basal Toujeo U-300 + bolos rápidos.
> **Modelo bajo estudio**: `ssm_v0_ukf6_basal` (versión congelada).
> **Separación regulatoria**: este track es **research personal**, NO es parte del
> producto (Orbit Copilot / Clinic). El producto no muestra predicciones ni alertas
> predictivas. Ver `POST_FIX_VALIDATION_PLAYBOOK.md`.

---

## 0. Por qué pre-registrar

En un n=1 el mayor riesgo de credibilidad es el **sobreajuste a tus propios datos**:
elegir métricas, ventanas o subgrupos *después* de ver qué hace lucir bien al modelo.
Para evitarlo, este documento se **congela** (commit con fecha) antes de correr el
bench definitivo. Si después se cambia algo, se documenta como enmienda con fecha.

**Regla de oro**: el modelo NO se tunea contra los datos de evaluación. La versión
reportada es la congelada al inicio (`ssm_v0_ukf6_basal`). Si se ajusta un parámetro
(p. ej. `F_BIO_BASAL`), eso abre un **estudio nuevo / versión nueva**, no se mezcla.

---

## 1. Pregunta de investigación

**Primaria**
> ¿Con qué exactitud predice un State-Space Model con UKF la glucosa intersticial
> a 30 y 60 min de horizonte, en un único individuo con T1D, en condiciones de vida
> real y de forma prospectiva (out-of-sample)?

**Secundarias**
1. ¿Está bien **calibrada la incertidumbre** del modelo (el ±σ que reporta)?
2. ¿El rendimiento depende del **contexto** (estable / post-comida / nocturno / post-ejercicio)?
3. ¿El modelo **anticipa hipoglucemias** (<70 mg/dL) con tiempo útil?
4. ¿El SSM **supera a baselines triviales** (persistencia, extrapolación lineal)?

---

## 2. Diseño

- **Tipo**: estudio observacional prospectivo n=1, sin intervención sobre el manejo.
- **Naturaleza prospectiva (clave)**: cada predicción se persiste en `PredictionAudit`
  con `predicted_at` ANTES de conocerse la glucosa real; se resuelve después contra
  el CGM. No hay look-ahead. El modelo está congelado durante toda la ventana.
- **Sin train/test split artificial**: el modelo no se entrena sobre la ventana; se
  evalúa puramente forward. (ISF/ICR/PMM aprendidos son del histórico previo, no de
  la ventana de evaluación.)

---

## 3. Datos

- **Fuente CGM**: Abbott FreeStyle Libre vía LibreLinkUp, ~cada 5–15 min.
- **Ground truth**: lectura CGM en el instante objetivo (t+30, t+60).
- **Ventana de inclusión**: desde `[FECHA_INICIO]` (post-fix COB/alerta, 2026-06-01)
  hasta acumular **≥ 14 días** con `coverage_ratio ≥ 0.75` y `status: healthy`
  en `/api/model-health`. No correr el análisis primario antes de eso.
- **Exclusiones (definir ahora, aplicar ciegamente)**:
  - Warm-up de sensor (primeras ~Xh de cada sensor nuevo).
  - Lecturas marcadas como artefacto / corregidas retroactivamente (`original_value_mgdl`).
  - Gaps de CGM > 20 min (predicción sin ground truth a horizonte → se descarta el par).
  - Compression lows nocturnos identificables (criterio a fijar).

---

## 4. Métricas (definidas ANTES de ver resultados)

Todas ya computadas por `bench/` salvo donde se indica "AÑADIR (solo análisis)".

### 4.1 Exactitud puntual (primaria)
| Métrica | Horizonte | Umbral pre-declarado de "bueno" |
|---|---|---|
| MAE  | 30 / 60 min | ≤ 18 / ≤ 25 mg/dL |
| RMSE | 30 / 60 min | — (reportar) |
| MARD | 30 / 60 min | ≤ 12% / ≤ 15% |
| BIAS (error con signo) | 30 / 60 min | \|bias\| ≤ 3 (aceptable ≤ 5) |

### 4.2 Calibración de la incertidumbre (secundaria 1)
- **ECE** (ya en `bench/metrics/calibration.py`) — pre-declarado ≤ 0.05.
- **Cobertura de intervalos**: ¿el IC 90% contiene la verdad ~90% de las veces?
- **PIT histogram** — AÑADIR (solo análisis): uniforme = bien calibrado.

### 4.3 Clínica (secundaria) — AÑADIR (solo análisis, no toca el modelo)
- **Clarke / Parkes Error Grid** (estándar en literatura CGM; los revisores lo esperan).
  Reportar % en zonas A+B. Es post-hoc sobre pred vs real, no modifica el SSM.

### 4.4 Estratificación por régimen (secundaria 2)
Reportar 4.1 separado por: estable / post-comida / nocturno / post-ejercicio
(el bench ya segmenta por regímenes — ver `bench/tuning/regimes.py`).

### 4.5 Anticipación de hipoglucemia (secundaria 3)
- Sensibilidad / especificidad para predecir CGM < 70 a 30 min.
- **Lead time** mediano de aviso (`hypo_post_mortem` ya lo soporta).
- Curva precision-recall variando el umbral de probabilidad.

### 4.6 Comparación contra baselines (secundaria 4 — CRÍTICA para publicar)
El SSM debe **batir** baselines triviales o el resultado no es interesante:
- **Persistencia**: ĝ(t+h) = g(t) (última lectura).
- **Extrapolación lineal**: ĝ(t+h) = g(t) + ROC·h.
- Reportar `skill_score = 1 − MAE_modelo / MAE_baseline` (ya existe el concepto en
  `bench/metrics/coverage.py`). AÑADIR baselines explícitos al reporte si faltan.

---

## 5. Plan de análisis

1. Esperar a `status: healthy` + `coverage_ratio ≥ 0.75` + ≥14 días.
2. `GET /api/model-health/ready` → debe ser `ready: true`. No analizar antes.
3. `GET /api/bench/run?days=14` → métricas 4.1–4.4.
4. `GET /api/bench/hypo_post_mortem?days=14&horizon=30` → métrica 4.5.
5. Calcular baselines (4.6) y Error Grid (4.3) — post-proceso, sin tocar el SSM.
6. Reporte siguiendo **TRIPOD** (Transparent Reporting of a multivariable
   prediction model) — checklist estándar para modelos predictivos.

**Análisis de subgrupos**: solo los 4 regímenes pre-declarados (4.4). Nada de
"buscar" subgrupos donde el modelo luce bien post-hoc.

---

## 6. Ética y reporte

- **Autoexperimentación n=1**: sujeto = autor. Generalmente no requiere IRB por ser
  datos propios, pero anticipar una declaración de ética para el journal.
- **Datos**: propios, anonimizables. Sin datos de terceros.
- **Conflictos / encuadre**: el modelo NO se presenta como dispositivo ni como apto
  para decisiones de dosificación. Es una caracterización de exactitud predictiva.
- **Limitaciones a declarar de entrada**: n=1 (no generaliza a población), un solo
  sensor/marca, un solo régimen de insulina (Toujeo), periodo acotado.

---

## 7. Venue sugerido

- **Preprint**: medRxiv (clínico) o arXiv (metodológico) primero.
- **Journal**: *Journal of Diabetes Science and Technology* (JDST) — hogar natural
  de trabajos de predicción CGM y Error Grid. Alternativa: *Diabetes Technology &
  Therapeutics*.

---

## 8. Qué NO hacer (consistente con el playbook)

- No tunear el SSM contra la ventana de evaluación.
- No agregar métricas/subgrupos después de ver resultados.
- No mezclar versiones del modelo en el mismo reporte.
- No reportar nada antes de `ready: true`.
- Error Grid, PIT y baselines son **post-proceso** — no modifican el modelo.

---

## 9. Checklist de pre-registro (firmar/fechar antes del análisis)

- [ ] Ventana de inclusión y exclusiones fijadas
- [ ] Métricas y umbrales pre-declarados (sección 4)
- [ ] 4 regímenes de estratificación fijados (sin más)
- [ ] Baselines definidos (persistencia + lineal)
- [ ] Versión del modelo congelada: `ssm_v0_ukf6_basal`
- [ ] Commit de este documento con fecha == pre-registro
