# Experimento RECHAZADO — FPE (Fat/Protein Effect) · r2_fpe

**Branch:** `experiment/r2-fpe-rejected`
**Estado:** ❌ RECHAZADO · no mergeado · no desplegado · `FPE_ENABLED=False` por defecto
**Baseline comparado:** `ssm_v0_ukf6_basal_ex_r1`
**Fecha:** 2026-06-09
**Datos:** export local `diabetes_20260609_1632.db` (46.8 días; train 05-16→06-05, test held-out 06-05→06-09)

## Hipótesis (probada)
El error residual post-comida, especialmente **2–5h después de comidas altas en
grasa/proteína**, se debería a una aparición **retardada** de glucosa no modelada
(gluconeogénesis de la proteína + "efecto pizza" de la grasa). Se implementó un
componente FPE: reservorio lento (kernel gamma-2, pico ~2.5h) que convierte
grasa+proteína en una tasa de subida de glucosa sumada a la dinámica.

## Resultado — la hipótesis se RECHAZA para este dataset y esta versión del modelo

- **El óptimo held-out fue `FPE_GAIN = 0`** (es decir, FPE apagado).
- **Cualquier `gain` positivo empeoró post-meal 2–5h** (test +60: MAE 16.7 → 19.3,
  sesgo −12.1 → −15.0).
- **También empeoró high-fat/protein** (MAE 18.8 → 21.4) **y global** (15.6 → 16.6).
- Whiteness sin cambios; safety (ventanas de hipo) sin empeorar.

### Causa raíz
El sesgo post-comida **ya es negativo** (`g_real − g_pred < 0`): el modelo **ya
sobre-predice** después de comer. El FPE *suma* glucosa → empuja la predicción aún
más arriba → empeora. La hipótesis era **al revés de la realidad**.

El residual real **no es macro-específico**: es un **sesgo global de sobre-predicción**
(presente incluso en **ayunas: −8.9 a +60**). Esa es la palanca a investigar, no la
grasa/proteína.

## Criterio de éxito (todos los relevantes fallaron)
- ❌ mejora post-meal 2–5h
- ❌ no degradación global
- ❌ no degradación overnight
- ✅ whiteness igual o mejor
- ✅ safety hypo no empeora

**Veredicto: NO promover.**

## Por qué se preserva
Este resultado negativo es **evidencia científica útil**: documenta que el FPE, tal
como se modeló, no aporta sobre estos datos. Sirve para **no volver a probar la misma
hipótesis sin nueva evidencia** (p.ej. un período con comidas distintas, más datos de
high-fat/protein, o un modelo que primero corrija el sesgo global). El código queda
apagable e intacto en esta branch para reproducir o retomar si aparece esa evidencia.

## Reproducir
```bash
git checkout experiment/r2-fpe-rejected
python3 -m bench.eval_fpe --max 130 --test-frac 0.25
# → bench/reports/fpe/{fpe_report.md,fpe_report.json,fpe_report.csv}
```

## Próximo experimento (no acá)
Diagnosticar el **sesgo global de sobre-predicción**, especialmente en ventanas
**fasting / basal-only / overnight** (apunta a EGP/producción hepática o efecto basal).
Mismo método: branch, apagable, held-out, por régimen.
