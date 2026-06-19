experiment/r2-basal-net-bias
status: offline candidate rejected / not promoted
reason: improves global bias but unsafe in hypo windows

Resumen:
- Confirmó que el sesgo global de sobre-predicción vive en el término basal neto.
- Un offset constante (−0.20 mg/dL/min) corrige GLOBAL, fasting, basal-only,
  stable, overnight y post-meal (held-out: sesgo +60 −9.0 → +0.4; MAE 17.0 → 14.7).
- PERO sobre-corrige hypo_window (n=35: sesgo −0.2 → +8.7), donde el baseline ya
  estaba bien → en diabetes, safety manda → NO se promueve.

Aprendizaje: el sesgo es dependiente de la glucosa (ausente en lo bajo por
contra-regulación). Un offset constante es demasiado bruto.

Sucesor: experiment/r3-glucose-gated-basal-bias (corrección con compuerta suave
por glucosa: activa en rango normal/alto, se apaga cerca de hipo).

Flag OFF por defecto (OFF = r1 exacto). No merge. No deploy.
