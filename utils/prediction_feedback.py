"""
utils/prediction_feedback.py — Feedback loop del modelo de predicción.

Responsabilidades
-----------------
1. save_prediction()      — persiste cada predicción generada en glucose_predictions
2. resolve_predictions()  — cuando llega una lectura real, busca predicciones
                            pendientes y calcula el error (g_real − g_pred)
3. get_model_accuracy()   — calcula MAE, bias y tendencia de error de las
                            últimas N predicciones resueltas
4. get_adaptive_bias()    — devuelve el bias promedio para corregir la
                            próxima predicción antes de mostrarla al usuario

Concepto clave: bias adaptivo
-----------------------------
Si en los últimos 14 días el modelo predijo sistemáticamente +18 mg/dL más
de lo que ocurrió, se aplica −18 como corrección en la siguiente predicción.
Este ajuste es transparente (se muestra al usuario) y mejora la percepción
de precisión mientras el modelo de fondo acumula datos para re-calibración.

No es ML — es una corrección de offset lineal simple y explicable.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional


# Tolerancia en minutos para considerar que una lectura "resuelve" una predicción
_WINDOW_30 = 12   # ±12 min alrededor de +30min
_WINDOW_60 = 12   # ±12 min alrededor de +60min

# Número de predicciones resueltas para calcular bias y MAE
_BIAS_WINDOW = 20


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
