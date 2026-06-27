experiment/r4-interval-calibration-h60
status: offline candidate — passed held-out gates (8/8), pending review / live deployment decision

objetivo: calibrar los intervalos +60 del modelo de producción
          ssm_v0_ukf6_basal_ex_r2_gated_bias (sobreconfiados: std(z)≈1.63, IC90≈80%).
mecanismo: multiplicador post-hoc de σ a +60 (SIGMA_MULT_60). NO toca la media,
           NO toca gated bias, R, dinámica EGP/basal, hypo engine, Copilot/Clinic.
flag OFF = modelo de producción exacto (sigma_mult()=1.0).

tuning: m = RMS(z_train) = 1.64 (solo en train).
held-out (test 25–27 Jun, n=263):
  GLOBAL   IC90 81%→92%   std(z) 1.59→0.97
  hypo_window IC90 81%→93% (seguridad preservada)
  todos los regímenes mejoran; post_meal_0_2h 76%→85% (único parcial).
media/MAE/sesgo: SIN CAMBIO por construcción.

gate: 8/8 passed.
recomendación: candidate for later deploy, NOT active. Flag OFF. No merge. No deploy.
nota: ortogonal a la corrección de la media (r2_gated_bias); se podría promover
      por separado con su propia versión y veredicto live.
