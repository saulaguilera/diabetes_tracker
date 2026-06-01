# Post-Fix Validation Playbook

> **Fase actual**: Observación + cobertura + validación + instrumentación.
> **Fase NO actual**: tuning, retuneo del SSM, ajuste de parámetros, UI nueva.

---

## 1. Por qué NO tocamos el SSM todavía

El UKF y el modelo de estado están sanos:

- Covariance PSD ok en 100% de los audits.
- Innovations sin outliers >3σ.
- Sigma de predicción razonable.
- Estructura matemática estable.

**El problema no era el modelo, era la evidencia.**

Durante semanas el bench evaluó al modelo bajo condiciones donde:

1. El SSM sólo corría cuando el usuario abría `/calcular` → 24 predicciones/día en lugar de ~288.
2. Los `PredictionAudit` del background predictor nunca se guardaban (TypeError silencioso por kwargs incorrectos).
3. La ventana nocturna del `hypo_risk_engine` estaba desfasada 3–4 horas (UTC vs local).
4. `services/hypo_outcome_tracker`, `hypo_metrics`, `pmm/engines/observation` recortaban su ventana 3–4 h por el mismo bug TZ.
5. `bench/metrics/coverage.skill_score` devolvía `-Infinity` literal y rompía el JSON del frontend.
6. `utils/audit_logger.condition` devolvía `+Infinity` que llegaba a la DB.
7. El verdict del bench elegía el modelo legacy (`mc_ar_gp_pmm_v1`) por orden alfabético, no el SSM activo.
8. `resolve_predictions` / `resolve_audits` cargaban toda la historia no-resuelta en cada sync — iba a escalar mal con el background predictor activo.

**Tunear el modelo bajo esas condiciones habría sido tunear contra ruido.** Cualquier ganancia que se viera en el bench podría ser un artefacto de los bugs, no del cambio.

---

## 2. Bugs ya corregidos (snapshot)

| # | Archivo | Tipo | Severidad |
|---|---|---|---|
| 1 | `bench/metrics/coverage.py` | `-Infinity` literal | 🔴 |
| 2 | `utils/audit_logger.py` | `+Infinity` literal | 🔴 |
| 3 | `utils/hypo_risk_engine.py` | TZ — ventana nocturna corrida 3–4h | 🔴 |
| 4 | `services/background_predictor.py` | kwargs inválidos → audits perdidos | 🔴 |
| 5 | `services/hypo_outcome_tracker.py` | TZ | 🟡 |
| 6 | `services/hypo_metrics.py` | TZ | 🟡 |
| 7 | `blueprints/bench_bp.py` | TZ en `hypo_post_mortem` | 🟡 |
| 8 | `pmm/engines/observation.py` | TZ — ventana PMM recortada | 🟡 |
| 9 | `bench/metrics/accuracy.py` + `calibration.py` | NaN literal en métricas vacías | 🟡 |
| 10 | `utils/prediction_feedback.py` | Carga toda la historia en cada sync | 🟠 |
| 11 | `utils/audit_logger.py:resolve_audits` | Mismo problema | 🟠 |
| 12 | `bench/runner.py` | Verdict elegía modelo viejo | 🟢 |
| 13 | `blueprints/bench_bp.py` | Misma familia (verdict) | 🟢 |
| 14 | `pmm/ssm/filter.py:_warm_start_cob` | Leía clave `cob` inexistente → COB inicial SIEMPRE 0, SSM ciego a carbos al arrancar | 🔴 |
| 15 | `app.py` alerta nocturna | `last_cgm.value` (no existe; es `value_mgdl`) dentro de `except` silencioso → alerta muerta; + `utcnow` | 🔴 |

> **Bugs #14/#15 detectados en review externo (2026-05-31).** El #14 contaminaba
> toda la evidencia post-comida que se venía juntando (el warm-start del COB del
> SSM caía a 0 sin importar lo comido). Por eso **el reloj de evidencia limpia
> para métricas post-comida se reinicia desde esta fecha**; cobertura general y
> nocturna no se ven afectadas. Guard de regresión: `utils/tests/test_cob_contract.py`.

---

## 3. Qué datos estamos esperando

Con los fixes desplegados, **cada CGM sync** (~5 min):

- Corre `run_filter` → `forward_predict(30, 60)`.
- Persiste un `GlucosePrediction` con `model_version = "ssm_v0_ukf6_basal"`.
- Persiste 2 `PredictionAudit` (h=30, h=60) con covariance + innovations.
- Persiste innovations granulares en `SSMInnovation`.
- El `hypo_risk_engine` corre, crea `HypoRiskAudit` cuando aplica.
- El `outcome tracker` resuelve audits viejos contra los CGM nuevos.

Volumen esperado en 7 días:

| Tabla | Esperado en 7d | Hoy (estimado) |
|---|---|---|
| `GlucosePrediction` (ssm_basal) | ~2000 | ~250 |
| `PredictionAudit` (ssm_basal) | ~4000 | depende |
| `SSMInnovation` | ~5000 | depende |
| `HypoRiskAudit` resueltos | ≥ hipos reales en la ventana | 0 desde el fix |

---

## 4. Qué correr después de 7–10 días

En este orden:

```
1.  GET /api/model-health
2.  GET /api/model-health/ready
3.  GET /api/bench/run?days=10
4.  GET /api/bench/hypo_post_mortem?days=10&horizon=30
5.  GET /api/hypo-risk/performance
```

### 4.1. `/api/model-health`

Debe devolver `status: "healthy"`.

Mirar especialmente:

- `coverage.coverage_ratio` ≥ 0.75
- `coverage.missing_hours` vacío o sólo 1–2 horas no críticas
- `audits.prediction_audits` ≈ 2 × `coverage.predictions_in_window`
- `audits.ssm_innovations` > 0 y creciendo
- `audits.hypo_audits_stale_12h` cercano a 0
- `blocking_issues` vacío
- `warnings` vacío o sólo informativos

### 4.2. `/api/model-health/ready`

Debe devolver `ready: true`. Si no, leer `missing[]` — dice exactamente qué falta.

**No correr el bench hasta que `ready: true`.** Cualquier número del bench previo a esto está sesgado por cobertura insuficiente.

### 4.3. `/api/bench/run?days=10` (con `ready: true`)

Veredicto honesto del modelo. Mirar:

| Métrica | Saludable | Aceptable | Acción |
|---|---|---|---|
| MAE_30 | ≤ 18 | ≤ 22 | > 25 → revisar parámetros fisiológicos |
| MAE_60 | ≤ 25 | ≤ 30 | > 35 → idem |
| BIAS abs | ≤ 3 | ≤ 5 | > 5 sostenido → revisar `F_BIO_BASAL` |
| ECE | ≤ 0.05 | ≤ 0.08 | > 0.10 → sigma mal calibrado |
| Stable MAE | ≤ 12 | ≤ 15 | > 18 → algo grave en el filtro |
| post_meal MAE | ≤ 22 | ≤ 27 | > 30 → revisar `K_A` / absorption profile |
| high 180–250 MAE | ≤ 30 | ≤ 45 | > 50 → COB dynamics o sobreestimación de IOB |

### 4.4. `/api/bench/hypo_post_mortem?days=10&horizon=30`

```
n_no_prediction:   < 20% del total            ✓ cobertura ok
n_pred_above_70:   variable                   → si > 50% del resto: modelo
                                                 no anticipa caídas
n_sigma_too_wide:  variable                   → si > 30% del resto: sigma
                                                 demasiado ancho — bajar
                                                 HYPO_ALERT_PROB de 0.30 a 0.20
n_alert_triggered: ideal > 50% de las hipos reales
```

### 4.5. `/api/hypo-risk/performance`

Sistema de alertas real-time (separado del bench).

- `precision` ≥ 0.5 → al menos la mitad de las alertas son hipos reales.
- `recall` ≥ 0.6 → atrapamos la mayoría de las hipos.
- `mean_warning_lead_time_min` ≥ 15 → suficiente tiempo para actuar.
- `false_positive_rate` ≤ 0.3.

---

## 5. Cómo decidir qué hacer al final de la ventana

```
┌──────────────────────────────────────────────────────────────────┐
│  ¿`/api/model-health/ready` = true?                              │
└─────────────┬────────────────────────────────────────────────────┘
              │
       ┌──────┴──────┐
       │ NO          │ SÍ
       ▼             ▼
  Esperar más    ¿Bench dentro de aceptable?
  (logs van a    ────┬─────────┬────────────────────
   decir qué      SÍ │      NO │
   falta)            ▼         ▼
                ┌────────┐  ¿Qué falla?
                │ Modelo │  │
                │ apto   │  ├─ BIAS > +3 sostenido  → tunear F_BIO_BASAL
                │ para   │  │                          (NO cambiar SSM)
                │ uso    │  ├─ post_meal MAE > 25   → revisar K_A y meal
                │ real.  │  │                          absorption profile
                │ Ahora  │  ├─ high 180-250 alto   → revisar COB1/COB2
                │ sí     │  │                          timing
                │ podés  │  ├─ sigma SUBESTIMA      → inflar Q (process
                │ pensar │  │ (var_z < 0.5)            noise) o R (obs noise)
                │ en UI. │  └─ sigma SOBREESTIMA    → bajar Q
                └────────┘    (var_z > 2)
```

**Importante**: si el modelo necesita ajustes, son **parámetros fisiológicos finos**, no cambios estructurales. NO:

- No agregar estados.
- No cambiar las ecuaciones del UKF.
- No introducir HRV / stress / illness.
- No introducir un segundo modelo en paralelo.
- No usar Claude/LLM para decisiones críticas.

---

## 6. Guardrails activos en producción

Cada sync de LibreLinkUp ejecuta `services.model_health.log_health_warnings()`. Si el estado es `warning` o `critical`, emite líneas al logger `model_health`:

```
WARNING model_health:warn: coverage_ratio=0.42 — aún acumulando evidencia
ERROR   model_health:blocking: última predicción hace 87min — background
        predictor probablemente caído
```

Si todo está bien, **silencio absoluto** — no contamina el log normal.

Buscalos con (en Railway):

```bash
gh logs --service web | grep model_health
```

---

## 7. Qué NO hacer hasta tener evidencia limpia

| ❌ No hacer | Por qué |
|---|---|
| Tunear `F_BIO_BASAL` | Sin BIAS confirmado sobre 2000+ predicciones es ruido |
| Cambiar `K_A` o `K_G` | Idem |
| Agregar UI compleja al dashboard | Habría que rehacerla si el modelo cambia |
| Bajar `HYPO_ALERT_PROB` | Sin medir false_alarm_rate real es flying blind |
| Agregar features grandes | Cada feature nuevo es más superficie de bug |
| "Reescribir" el SSM | La estructura es correcta. El problema era la evidencia |
| Cambiar thresholds clínicos (70 / 180 / 250) | Son ADA estándar — no tocarlos |
| Usar Claude/LLM para alertas críticas | Determinismo > LLM para riesgo |

---

## 8. Direccionalidad estratégica

La app debe sentirse como **"un copiloto metabólico que me acompaña"**, no como **"un dashboard médico que me bombardea con números"**.

Cuando el modelo esté validado:

- El SSM debe ser **invisible** al usuario.
- Las predicciones se muestran como narrativa ("te vas a quedar estable las próximas 2 horas"), no como `g_pred_30 = 142 ± 18 mg/dL`.
- Las alertas son **deterministas** y **explicables** ("vas a tocar 65 en ~75 min si no comés").
- La UI sigue siendo **minimalista y oscura**.

Cuando todo esto pase, la pregunta no será "¿el modelo predice bien?", será "¿la app me ayudó a evitar hipos esta semana?". Esa segunda pregunta sólo se responde con el outcome tracker corriendo limpio.

---

## 9. Snapshot del estado de hoy

- Background predictor activo ✓
- Audits del background predictor guardándose ✓ (era el último bug grave)
- TZ alineada en todos los servicios de validación ✓
- JSON del bench sanitizado ✓
- Endpoints de health desplegados ✓
- Sistema en modo **observación**: acumular 7–10 días limpios y volver a evaluar.

Próxima decisión: **en 7 días, no antes.**
