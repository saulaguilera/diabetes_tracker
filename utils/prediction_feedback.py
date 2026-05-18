"""
utils/prediction_feedback.py — Feedback loop del modelo de predicción.

Responsabilidades
-----------------
1. save_prediction()      — persiste cada predicción generada en glucose_predictions
2. resolve_predictions()  — cuando llega una lectura real, busca predicciones
                            pendientes y calcula el error (g_real − g_pred)
3. get_model_accuracy()   — calcula MAE, bias, tendencia y Clarke Error Grid
4. get_adaptive_bias()    — devuelve el bias promedio para corregir la
                            próxima predicción antes de mostrarla al usuario
5. clarke_zone()          — clasifica un par (real, pred) en zona A-E del Clarke EGA
6. clarke_error_grid()    — aplica Clarke EGA sobre un conjunto de predicciones

Concepto clave: bias adaptivo
-----------------------------
Si en los últimos 14 días el modelo predijo sistemáticamente +18 mg/dL más
de lo que ocurrió, se aplica −18 como corrección en la siguiente predicción.
Este ajuste es transparente (se muestra al usuario) y mejora la percepción
de precisión mientras el modelo de fondo acumula datos para re-calibración.

No es ML — es una corrección de offset lineal simple y explicable.

Clarke Error Grid (CEG)
-----------------------
Estándar clínico para evaluar la exactitud de mediciones/predicciones de
glucosa (Clarke et al., Diabetes Care 1987).  Clasifica cada par (real, pred)
en zonas A-E según el impacto clínico del error:

  A — Clínicamente precisa (error ≤ 20 % del valor real, o ambos < 70)
  B — Aceptable (error benigno, no conduce a tratamiento incorrecto)
  C — Sobrecorrección (predicción lleva a tratar un valor aceptable)
  D — Falla en detectar hipo/hiperglucemia peligrosa
  E — Tratamiento opuesto (el más peligroso)

Para predicciones de glucosa (no SMBG puntual) la referencia clínica más
relevante es la proporción en Zona A ≥ 90 % a +30 min y ≥ 80 % a +60 min.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional


# Tolerancia en minutos para considerar que una lectura "resuelve" una predicción
_WINDOW_30 = 12   # ±12 min alrededor de +30min
_WINDOW_60 = 12   # ±12 min alrededor de +60min

# Número de predicciones resueltas para calcular bias y MAE
_BIAS_WINDOW = 20


# ── Clarke Error Grid ─────────────────────────────────────────────────────────

def clarke_zone(g_real: float, g_pred: float) -> str:
    """
    Clasifica el par (g_real, g_pred) en una zona del Clarke Error Grid (A-E).

    Algoritmo basado en Clarke et al. (1987) y Kovatchev et al. (2004).
    x = valor de referencia (real), y = valor estimado (predicho).

    Zonas (en orden de peligrosidad ascendente):
      A — precisa: |y−x|/x ≤ 20%, o ambos < 70 mg/dL
      B — aceptable: error >20% pero clínicamente benigno
      C — sobrecorrección de valor aceptable (puede causar hipo/hiperglucemia iatrogénica)
      D — falla en detectar hipo (<70) o hiperglucemia severa (>240) real
      E — tratamiento opuesto (predice hiperglucemia en hipoglucemia real o viceversa)
    """
    x, y = float(g_real), float(g_pred)

    # ── Zona A: dentro del 20% o ambos hipoglucémicos ──────────────────────
    if x < 70.0 and y < 70.0:
        return "A"
    if x > 0 and abs(y - x) / x <= 0.20:
        return "A"

    # ── Zona E: tratamiento completamente opuesto (más peligroso) ──────────
    # Predice hiperglucemia severa cuando el real es hipoglucemia
    if x <= 70.0 and y >= 180.0:
        return "E"
    # Predice hipoglucemia severa cuando el real es hiperglucemia
    if x >= 180.0 and y <= 70.0:
        return "E"

    # ── Zona D: falla en detectar evento clínico peligroso ─────────────────
    # D inferior: glucemia real < 70 (hipo), predicción en rango normal
    if x < 70.0 and 70.0 <= y <= 180.0:
        return "D"
    # D superior: glucemia real ≥ 240 (hiper severa), predicción en rango normal
    if x >= 240.0 and 70.0 <= y <= 180.0:
        return "D"

    # ── Zona C: sobrecorrección ─────────────────────────────────────────────
    # C superior: predicción muy alta para valor real aceptable/leve (llevaría a
    #             tratar un hiper que no existe o corregir agresivamente)
    #             Región: x ∈ [70, 180], y > x·1.20 y y ≥ 180
    if 70.0 <= x <= 180.0 and y > x * 1.20 and y >= 180.0:
        return "C"
    # C inferior: predicción demasiado baja para valor real aceptable/levemente
    #             elevado (llevaría a agregar carbohidratos innecesarios)
    #             Región: x ∈ [120, 240], y < x·0.80 y y ≤ 70
    if 120.0 <= x <= 240.0 and y < x * 0.80 and y <= 70.0:
        return "C"

    # ── Zona B: todo lo demás (error >20% pero clínicamente aceptable) ─────
    return "B"


def clarke_error_grid(pairs: list[tuple[float, float]]) -> dict:
    """
    Aplica el Clarke Error Grid sobre una lista de pares (g_real, g_pred).

    Args:
        pairs: lista de (g_real, g_pred) en mg/dL.

    Returns:
        {
          "zonas": {"A": n, "B": n, "C": n, "D": n, "E": n},
          "pct":   {"A": %, "B": %, "C": %, "D": %, "E": %},
          "n":     total de pares,
          "zona_a_b_pct": % combinado A+B (clínicamente aceptable),
          "clinicamente_seguro": True si zona_a_b_pct ≥ 95%
        }
    """
    if not pairs:
        return {
            "zonas": {z: 0 for z in "ABCDE"},
            "pct":   {z: 0 for z in "ABCDE"},
            "n":     0,
            "zona_a_b_pct": 0,
            "clinicamente_seguro": False,
        }

    conteo = {z: 0 for z in "ABCDE"}
    for g_real, g_pred in pairs:
        conteo[clarke_zone(g_real, g_pred)] += 1

    n = len(pairs)
    pct = {z: round(100 * conteo[z] / n) for z in "ABCDE"}
    ab  = round(100 * (conteo["A"] + conteo["B"]) / n)

    return {
        "zonas": conteo,
        "pct":   pct,
        "n":     n,
        "zona_a_b_pct":         ab,
        "clinicamente_seguro":  ab >= 95,
    }


def save_prediction(
    predicted_at: datetime,
    g_actual: float,
    g_pred_30: float,
    g_pred_60: float,
    iob: float,
    cob: float,
    roc: Optional[float],
    isf_used: Optional[float],
    icr_used: Optional[float],
    ex_factor: float,
) -> None:
    """
    Persiste una predicción en la tabla glucose_predictions.
    Llamar inmediatamente después de calcular la predicción.
    """
    from models import db, GlucosePrediction

    # Evitar duplicados: no guardar si ya existe una predicción en los
    # últimos 8 minutos (el dashboard se refresca periódicamente)
    cutoff = predicted_at - timedelta(minutes=8)
    existe = GlucosePrediction.query.filter(
        GlucosePrediction.predicted_at >= cutoff,
        GlucosePrediction.predicted_at <= predicted_at,
    ).first()
    if existe:
        return

    pred = GlucosePrediction(
        predicted_at = predicted_at,
        g_actual     = round(g_actual, 1),
        g_pred_30    = round(g_pred_30, 1),
        g_pred_60    = round(g_pred_60, 1),
        iob          = round(iob, 3),
        cob          = round(cob, 1),
        roc          = round(roc, 3) if roc is not None else None,
        isf_used     = isf_used,
        icr_used     = icr_used,
        ex_factor    = round(ex_factor, 3),
    )
    db.session.add(pred)
    db.session.commit()


def resolve_predictions(readings: list) -> int:
    """
    Dado un iterable de GlucoseReading recién insertados (o todos los recientes),
    busca predicciones pendientes cuyo horizonte temporal coincide y las resuelve.

    Returns: número de predicciones resueltas en esta llamada.
    """
    from models import db, GlucosePrediction

    if not readings:
        return 0

    # Predicciones aún no resueltas completamente
    pendientes = GlucosePrediction.query.filter(
        db.or_(
            GlucosePrediction.resolved_30 == False,
            GlucosePrediction.resolved_60 == False,
        )
    ).all()

    if not pendientes:
        return 0

    resueltas = 0
    for pred in pendientes:
        t30 = pred.predicted_at + timedelta(minutes=30)
        t60 = pred.predicted_at + timedelta(minutes=60)

        for reading in readings:
            rt = reading.timestamp
            rv = reading.value_mgdl

            # Resolver horizonte +30min
            if not pred.resolved_30:
                if abs((rt - t30).total_seconds()) <= _WINDOW_30 * 60:
                    pred.g_real_30   = round(rv, 1)
                    pred.error_30    = round(rv - pred.g_pred_30, 1)
                    pred.resolved_30 = True
                    resueltas += 1

            # Resolver horizonte +60min
            if not pred.resolved_60:
                if abs((rt - t60).total_seconds()) <= _WINDOW_60 * 60:
                    pred.g_real_60   = round(rv, 1)
                    pred.error_60    = round(rv - pred.g_pred_60, 1)
                    pred.resolved_60 = True
                    resueltas += 1

    if resueltas:
        db.session.commit()

    return resueltas


def get_model_accuracy(n: int = _BIAS_WINDOW) -> dict:
    """
    Calcula métricas de accuracy del modelo sobre las últimas `n` predicciones
    resueltas.

    Returns dict:
        n_30, n_60        — número de predicciones resueltas por horizonte
        mae_30, mae_60    — Error Absoluto Medio (mg/dL)
        bias_30, bias_60  — Error promedio con signo (+= modelo subestima)
        rmse_30, rmse_60  — Root Mean Square Error
        pct_dentro_20_30  — % predicciones con error ≤ 20 mg/dL (horizonte 30)
        pct_dentro_20_60  — idem horizonte 60
        tendencia         — "mejorando" | "empeorando" | "estable" | "insuficiente"
    """
    from models import GlucosePrediction

    resueltas_30 = GlucosePrediction.query.filter(
        GlucosePrediction.resolved_30 == True,
        GlucosePrediction.error_30.isnot(None),
    ).order_by(GlucosePrediction.predicted_at.desc()).limit(n).all()

    resueltas_60 = GlucosePrediction.query.filter(
        GlucosePrediction.resolved_60 == True,
        GlucosePrediction.error_60.isnot(None),
    ).order_by(GlucosePrediction.predicted_at.desc()).limit(n).all()

    def _metrics(items, attr):
        errs = [getattr(i, attr) for i in items if getattr(i, attr) is not None]
        if not errs:
            return {"n": 0, "mae": None, "bias": None, "rmse": None, "pct_20": None}
        n_e   = len(errs)
        mae   = round(sum(abs(e) for e in errs) / n_e, 1)
        bias  = round(sum(errs) / n_e, 1)
        rmse  = round((sum(e**2 for e in errs) / n_e) ** 0.5, 1)
        p20   = round(100 * sum(1 for e in errs if abs(e) <= 20) / n_e)
        return {"n": n_e, "mae": mae, "bias": bias, "rmse": rmse, "pct_20": p20}

    m30 = _metrics(resueltas_30, "error_30")
    m60 = _metrics(resueltas_60, "error_60")

    # Tendencia: comparar MAE de la mitad más reciente vs la más antigua
    tendencia = "insuficiente"
    if m30["n"] >= 10:
        mitad = m30["n"] // 2
        recientes = resueltas_30[:mitad]
        antiguas  = resueltas_30[mitad:]
        mae_rec = sum(abs(i.error_30) for i in recientes) / len(recientes)
        mae_ant = sum(abs(i.error_30) for i in antiguas)  / len(antiguas)
        if mae_rec < mae_ant * 0.90:
            tendencia = "mejorando"
        elif mae_rec > mae_ant * 1.10:
            tendencia = "empeorando"
        else:
            tendencia = "estable"

    # ── Clarke Error Grid ───────────────────────────────────────────────────
    pairs_30 = [
        (i.g_real_30, i.g_pred_30)
        for i in resueltas_30
        if i.g_real_30 is not None and i.g_pred_30 is not None
    ]
    pairs_60 = [
        (i.g_real_60, i.g_pred_60)
        for i in resueltas_60
        if i.g_real_60 is not None and i.g_pred_60 is not None
    ]
    ceg_30 = clarke_error_grid(pairs_30)
    ceg_60 = clarke_error_grid(pairs_60)

    return {
        "n_30":           m30["n"],
        "mae_30":         m30["mae"],
        "bias_30":        m30["bias"],
        "rmse_30":        m30["rmse"],
        "pct_dentro_20_30": m30["pct_20"],
        "n_60":           m60["n"],
        "mae_60":         m60["mae"],
        "bias_60":        m60["bias"],
        "rmse_60":        m60["rmse"],
        "pct_dentro_20_60": m60["pct_20"],
        "tendencia":      tendencia,
        # Clarke Error Grid — horizonte +30 min
        "ceg_30":         ceg_30,
        # Clarke Error Grid — horizonte +60 min
        "ceg_60":         ceg_60,
    }


def get_adaptive_bias() -> dict:
    """
    Devuelve el bias a aplicar a la próxima predicción para corregir el
    desvío sistemático observado en las últimas predicciones resueltas.

    Returns:
        bias_30 : float  — mg/dL a sumar a g_pred_30 (negativo = modelo sobreestima)
        bias_60 : float  — idem para +60min
        confiable: bool  — True si hay ≥5 predicciones resueltas
    """
    from models import GlucosePrediction

    MIN_SAMPLES = 5

    def _bias(attr, resolved_field):
        items = GlucosePrediction.query.filter(
            resolved_field == True,
            getattr(GlucosePrediction, attr).isnot(None),
        ).order_by(GlucosePrediction.predicted_at.desc()).limit(_BIAS_WINDOW).all()
        if len(items) < MIN_SAMPLES:
            return 0.0, False
        errs = [getattr(i, attr) for i in items]
        # El bias es el error promedio; para corregir lo restamos a la predicción
        # bias = mean(real − pred) → corrección = −bias aplicado a pred
        # En realidad: pred_corr = pred + bias (si el modelo subestima, bias>0)
        return round(sum(errs) / len(errs), 1), True

    b30, ok30 = _bias("error_30", GlucosePrediction.resolved_30)
    b60, ok60 = _bias("error_60", GlucosePrediction.resolved_60)

    return {
        "bias_30":    b30,
        "bias_60":    b60,
        "confiable":  ok30 or ok60,
    }


# ── Incertidumbre y probabilidades ────────────────────────────────────────────

# Sigma por defecto de la literatura para modelos lineales de primer orden
# sobre datos CGM (Cobelli et al. 2009; Hovorka et al. 2004)
_DEFAULT_SIGMA_30 = 22.0   # mg/dL — horizonte +30min
_DEFAULT_SIGMA_60 = 35.0   # mg/dL — horizonte +60min


def _normal_cdf(x: float, mu: float, sigma: float) -> float:
    """P(X ≤ x) para X ~ N(mu, sigma²) usando math.erf (stdlib, sin scipy)."""
    if sigma <= 0:
        return 1.0 if x >= mu else 0.0
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


def get_prediction_sigma(n: int = 30) -> dict:
    """
    Devuelve la desviación estándar (σ) del error de predicción estimada
    desde las últimas `n` predicciones resueltas.

    Usa valores de literatura si hay menos de 5 muestras.

    Returns:
        sigma_30   : float — σ del error a +30min (mg/dL)
        sigma_60   : float — σ del error a +60min (mg/dL)
        data_based : bool  — True si viene de datos reales (≥5 muestras)
        n_30, n_60 : int   — cantidad de muestras usadas
    """
    from models import GlucosePrediction

    def _sigma_from(attr, resolved_field):
        items = GlucosePrediction.query.filter(
            resolved_field == True,
            getattr(GlucosePrediction, attr).isnot(None),
        ).order_by(GlucosePrediction.predicted_at.desc()).limit(n).all()
        errs = [getattr(i, attr) for i in items]
        if len(errs) < 5:
            return None, len(errs)
        mean = sum(errs) / len(errs)
        var  = sum((e - mean) ** 2 for e in errs) / max(len(errs) - 1, 1)
        return round(var ** 0.5, 1), len(errs)

    s30, n30 = _sigma_from("error_30", GlucosePrediction.resolved_30)
    s60, n60 = _sigma_from("error_60", GlucosePrediction.resolved_60)

    return {
        "sigma_30":   s30 or _DEFAULT_SIGMA_30,
        "sigma_60":   s60 or _DEFAULT_SIGMA_60,
        "data_based": s30 is not None or s60 is not None,
        "n_30":       n30,
        "n_60":       n60,
    }


def prediction_probabilities(
    g_pred: float,
    sigma:  float,
    hipo_thresh:  float = 70.0,
    hiper_thresh: float = 180.0,
) -> dict:
    """
    Dado un valor predicho y su incertidumbre, calcula:
    - P(hipo)  : P(X < 70)
    - P(rango) : P(70 ≤ X ≤ 180)
    - P(hiper) : P(X > 180)
    - ci_68    : intervalo al 68% (±1σ)
    - ci_90    : intervalo al 90% (±1.645σ)

    La distribución del error se modela como N(g_pred, sigma²).
    Supuesto: el bias ya fue corregido antes de llamar a esta función.
    """
    p_below_hipo  = _normal_cdf(hipo_thresh,  g_pred, sigma)
    p_below_hiper = _normal_cdf(hiper_thresh, g_pred, sigma)

    p_hipo  = round(p_below_hipo * 100)
    p_hiper = round((1.0 - p_below_hiper) * 100)
    p_rango = max(0, 100 - p_hipo - p_hiper)

    # Estado dominante (el de mayor probabilidad)
    estado = max(
        [("hipo", p_hipo), ("rango", p_rango), ("hiper", p_hiper)],
        key=lambda x: x[1],
    )[0]

    return {
        "p_hipo":   p_hipo,
        "p_rango":  p_rango,
        "p_hiper":  p_hiper,
        "estado":   estado,             # estado más probable
        "ci_68":    [round(g_pred - sigma),         round(g_pred + sigma)],
        "ci_90":    [round(g_pred - 1.645 * sigma), round(g_pred + 1.645 * sigma)],
        "sigma":    sigma,
    }


# ── Recalibración automática ───────────────────────────────────────────────────

def get_recalibration_suggestions() -> dict:
    """
    Analiza datos reales de correcciones y comidas para detectar si el ISF
    o el ICR configurados difieren significativamente de lo que los datos sugieren.

    Genera sugerencias accionables con botón "Aplicar" en la UI.

    Fuentes de análisis
    -------------------
    1. ISF calculado vs ISF configurado
       — Usa _calcular_isf_personal() sobre los últimos 60 días.
       — Sugiere ajuste si la diferencia es ≥ 10% y hay ≥ 5 correcciones.

    2. ICR calculado vs ICR configurado
       — Usa _calcular_icr_personal() sobre los últimos 90 días.
       — Sugiere ajuste si la diferencia es ≥ 10% y hay ≥ 5 comidas.

    3. Bias sistemático del feedback loop
       — Si el bias a +30min es ≥ 15 mg/dL con ≥ 10 predicciones,
         estima el ajuste de ISF necesario para eliminarlo.

    Niveles de confianza
    --------------------
    alta  : ≥ 15 muestras — cambio recomendado
    media : 7–14 muestras — cambio razonable
    baja  :  5–6 muestras — dato orientativo, usar con criterio

    Returns
    -------
    dict con:
        sugerencias : list[dict] — lista de sugerencias ordenadas por prioridad
        resumen     : str        — texto corto para mostrar en UI
        hay_algo    : bool       — True si hay al menos una sugerencia
    """
    from helpers import (
        _calcular_isf_personal, _calcular_icr_personal, _get_setting,
    )

    sugerencias = []

    # ── 1. ISF ────────────────────────────────────────────────────────────────
    isf_calc, n_isf   = _calcular_isf_personal(days=60)
    isf_config_raw    = _get_setting("isf_manual")
    isf_config        = float(isf_config_raw) if isf_config_raw else None

    if isf_calc and n_isf >= 5:
        referencia_isf = isf_config or isf_calc   # para calcular diferencia %
        if isf_config:
            diff_abs = isf_calc - isf_config
            diff_pct = abs(diff_abs) / isf_config * 100
            if diff_pct >= 10:
                confianza = "alta" if n_isf >= 15 else "media" if n_isf >= 7 else "baja"
                direccion = "más sensible" if diff_abs > 0 else "más resistente"
                sugerencias.append({
                    "tipo":           "ISF",
                    "setting_key":    "isf_manual",
                    "valor_actual":   isf_config,
                    "valor_sugerido": round(isf_calc, 1),
                    "diferencia_pct": round(diff_pct, 1),
                    "diferencia_abs": round(diff_abs, 1),
                    "n_muestras":     n_isf,
                    "confianza":      confianza,
                    "titulo":         "ISF puede estar desactualizado",
                    "mensaje": (
                        f"Tus últimas {n_isf} correcciones reales sugieren un ISF de "
                        f"{isf_calc:.1f} mg/dL·U — un {diff_pct:.0f}% "
                        f"{'mayor' if diff_abs > 0 else 'menor'} que el configurado "
                        f"({isf_config}). Tu glucemia responde {direccion} a la insulina "
                        f"de lo que el modelo asume."
                    ),
                    "impacto": (
                        f"Actualizar ISF a {isf_calc:.1f} haría que las correcciones "
                        f"{'sean más pequeñas' if diff_abs > 0 else 'sean más grandes'}, "
                        f"reduciendo el error sistemático en la calculadora."
                    ),
                })

    # ── 2. ICR ────────────────────────────────────────────────────────────────
    icr_calc, n_icr = _calcular_icr_personal(days=90)
    icr_config_raw  = _get_setting("icr")
    icr_config      = float(icr_config_raw) if icr_config_raw else None

    if icr_calc and n_icr >= 5:
        if icr_config:
            diff_abs = icr_calc - icr_config
            diff_pct = abs(diff_abs) / icr_config * 100
            if diff_pct >= 10:
                confianza = "alta" if n_icr >= 15 else "media" if n_icr >= 7 else "baja"
                # ICR mayor = menos insulina por gramo = más resistente post-comida
                # ICR menor = más insulina por gramo = más sensible post-comida
                if diff_abs > 0:
                    interpretacion = "necesitás menos insulina por gramo de CH de lo que el modelo asume"
                else:
                    interpretacion = "necesitás más insulina por gramo de CH de lo que el modelo asume"
                sugerencias.append({
                    "tipo":           "ICR",
                    "setting_key":    "icr",
                    "valor_actual":   icr_config,
                    "valor_sugerido": round(icr_calc, 1),
                    "diferencia_pct": round(diff_pct, 1),
                    "diferencia_abs": round(diff_abs, 1),
                    "n_muestras":     n_icr,
                    "confianza":      confianza,
                    "titulo":         "ICR puede estar desactualizado",
                    "mensaje": (
                        f"Tus últimas {n_icr} comidas con bolus sugieren un ICR de "
                        f"{icr_calc:.1f} g/U — un {diff_pct:.0f}% "
                        f"{'mayor' if diff_abs > 0 else 'menor'} que el configurado "
                        f"({icr_config}). Parece que {interpretacion}."
                    ),
                    "impacto": (
                        f"Con ICR {icr_calc:.1f} el bolo de comida "
                        f"{'sería menor' if diff_abs > 0 else 'sería mayor'} — "
                        f"reduciendo el desvío post-prandial."
                    ),
                })

    # ── 3. Bias sistemático del feedback loop → inferir ajuste de ISF ─────────
    try:
        accuracy  = get_model_accuracy(n=30)
        bias_30   = accuracy.get("bias_30")    # positivo = modelo subestima (pred < real)
        n_pred_30 = accuracy.get("n_30", 0)

        # Bias significativo: ≥15 mg/dL y ≥10 predicciones resueltas
        if bias_30 is not None and abs(bias_30) >= 15 and n_pred_30 >= 10:
            # Si el modelo sistemáticamente predice bajo (bias>0), el ISF real
            # podría ser mayor (insulina menos potente de lo asumido).
            # Estimación: si bias ≈ error_corrección ≈ delta_ISF × bolus_promedio / ISF
            # Para simplificar: mostramos el bias y dejamos la interpretación al usuario.
            direccion_bias = "subestima" if bias_30 > 0 else "sobreestima"
            sugerencias.append({
                "tipo":           "BIAS_LOOP",
                "setting_key":    None,   # no hay setting directo para aplicar
                "valor_actual":   None,
                "valor_sugerido": None,
                "diferencia_pct": None,
                "diferencia_abs": round(bias_30, 1),
                "n_muestras":     n_pred_30,
                "confianza":      "media" if n_pred_30 >= 20 else "baja",
                "titulo":         f"Bias sistemático detectado en predicciones",
                "mensaje": (
                    f"El modelo {direccion_bias} la glucemia real por "
                    f"{abs(bias_30):.1f} mg/dL en promedio a +30min "
                    f"(sobre {n_pred_30} predicciones). "
                    f"El bias adaptivo ya corrige esto automáticamente, pero un "
                    f"bias persistente puede indicar que el ISF o el ICR necesitan ajuste."
                ),
                "impacto": (
                    "Revisá si tu ISF está generando correcciones "
                    f"{'insuficientes' if bias_30 > 0 else 'excesivas'}. "
                    "Si el bias persiste >30 días, considerá recalibrarlo."
                ),
            })
    except Exception:
        pass

    # ── Resumen ───────────────────────────────────────────────────────────────
    n_sug = len(sugerencias)
    if n_sug == 0:
        resumen = "Tus parámetros están alineados con los datos reales."
    elif n_sug == 1:
        resumen = f"1 parámetro con diferencia significativa detectada."
    else:
        resumen = f"{n_sug} parámetros con diferencias significativas detectadas."

    return {
        "sugerencias": sugerencias,
        "resumen":     resumen,
        "hay_algo":    n_sug > 0,
        "isf_calc":    round(isf_calc, 1) if isf_calc else None,
        "isf_n":       n_isf,
        "icr_calc":    round(icr_calc, 1) if icr_calc else None,
        "icr_n":       n_icr,
    }
