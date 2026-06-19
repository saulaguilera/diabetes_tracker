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
"""
MODEL_VERSION = "ssm_v0_ukf6_basal_ex_r2_gated_bias"
