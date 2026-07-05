"""
pmm/ssm/version.py
───────────────────
Versión activa del modelo SSM — UNA sola fuente de verdad.

Bumpeá `MODEL_VERSION` cuando cambie la dinámica o los inputs del modelo,
para que el bench y el model-health midan cada variante por separado
(validación científica limpia: cada modelo acumula su propio track record
y no se contaminan los baldes de predicciones).

Historial
─────────
  ssm_v0_ukf6_basal      — 6 estados + basal Toujeo determinística.
  ssm_v0_ukf6_basal_ex   — agrega ejercicio como input determinístico
                           (baja directa insulino-independiente + cola de
                           sensibilidad post-ejercicio). [Hito 8]
  ssm_v0_ukf6_basal_ex_r1 — recalibra el ruido de observación R (×0.30) por
                           whitening de innovaciones: innovaciones blancas
                           (ACF₁ 0.60→0.15) y calibradas (std(z) 0.67→1.0);
                           mejora el pronóstico (MAE +60 17.8→15.4). [Hito 9]
  ssm_v0_ukf6_basal_ex_r2_gated_bias — corrige el sesgo global de sobre-predicción
                           con un offset a la producción endógena neta, atenuado
                           por una compuerta sigmoide sobre la glucosa (apagada
                           cerca de hipo). Held-out 9/9: sesgo +60 −10.3→~0, MAE
                           mejora en todos los regímenes, hipo preservada. NO toca
                           R ni la calibración de intervalos +60 (experimento
                           aparte). Ver pmm/ssm/basal_bias.py. [Hito 10]
  ssm_v0_ukf6_basal_ex_r3_cal60 — calibra los intervalos a +60 con un multiplicador
                           post-hoc de σ (×1.64, tuneado solo en train). La media
                           g_pred queda intacta (MAE/sesgo sin cambio por
                           construcción); escala la σ reportada → IC50/IC90 y
                           p_hypo/p_hyper. Validado held-out 25-27/6 (8/8 gates,
                           IC90 81→92%) y re-validado en semana virgen 28/6–5/7
                           (n=946: IC90 74→88%, ventana de hipo 35→82%, std(z)
                           1.85→1.13, recall de hipos sin regresión). Rollback =
                           INTERVAL_CALIB_ENABLED=False. Ver
                           pmm/ssm/interval_calib.py. [Hito 11]
  ssm_v0_ukf6_basal_ex_r4_cal3060 — extiende la calibración de intervalos a +30
                           (σ×1.68, tuneado solo en train 19-30/6 n=1231).
                           Held-out 1-5/7 n=625: IC90 75→89%, ventana de hipo
                           50→71%, std(z) 1.89→1.12, recall sin regresión,
                           precisión de alarmas 24→30%. Media intacta. Ambos
                           horizontes ahora calibrados. Rollback =
                           SIGMA_MULT_30=1.0. [Hito 12]
"""
MODEL_VERSION = "ssm_v0_ukf6_basal_ex_r4_cal3060"
