# r5 — calibración de intervalos a +30 · CANDIDATO offline (8/8 gates)

**Rama:** `experiment/r5-interval-calibration-h30` · **Fecha:** 2026-07-05
**Cambio:** `SIGMA_MULT_30: 1.0 → 1.68` en `pmm/ssm/interval_calib.py` (una constante).
**Mecanismo:** idéntico a r4/Hito 11 — multiplicador post-hoc de σ por horizonte.
La media g_pred queda intacta → MAE/sesgo sin cambio por construcción.

## Tuning (SOLO train)
- Datos: audits resueltos de `ssm_v0_ukf6_basal_ex_r2_gated_bias` a +30
  (la σ reportada a +30 es idéntica bajo r3_cal60, que solo escala +60).
- Train 19–30/6 (n=1231): `m30 = RMS(z_train) = 1.68`.

## Validación held-out (1–5/7, n=625) — nunca visto en tuning
| Métrica | Producción (m=1.0) | Candidato (m=1.68) | Gate |
|---|---|---|---|
| std(z) (ideal 1.0) | 1.89 | **1.12** | ✅ |
| IC90 global (ideal 90%) | 75% | **89%** | ✅ |
| IC90 ventana de hipo (n=38) | 50% | **71%** | ✅ mejora |
| IC50 global (ideal 50%) | 42% | 62% | ✅ mismo patrón que r4 |
| Ancho IC90 | ±15 mg/dL | ±25 mg/dL | ✅ no absurdo |
| MAE / sesgo | — | sin cambio | ✅ por construcción |
| Recall hipos (p_hypo≥0.3, n=15) | 13% | 13% | ✅ sin regresión |
| Alarmas / precisión (<80) | 25 / 24% | 27 / **30%** | ✅ mejora leve |

## Qué NO toca
Media/gated bias, R/Q, dinámica EGP/basal/ejercicio, hypo engine, thresholds,
Copilot/Clinic. En `main` la constante sigue 1.0 — **byte-idéntico a producción
hasta la promoción**.

## Promoción propuesta
Merge + `MODEL_VERSION → ssm_v0_ukf6_basal_ex_r4_cal3060` (Hito 12), tras revisión.
Rollback: `SIGMA_MULT_30 = 1.0`.
