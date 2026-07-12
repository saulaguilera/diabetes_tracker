"""
utils/hypo_predictor.py
────────────────────────
Predicción de riesgo de hipoglucemia en horizonte corto (15–45 min).

Por qué un módulo separado
--------------------------
El endpoint /api/predict/glucose predice a +30 y +60 min — útil para tendencia
pero NO suficiente para prevenir hipos. En T1D, la insulina rápida tarda 10–15
min en empezar a hacer efecto, por lo que necesitamos detectar la caída
inminente con al menos 15 min de anticipación para tomar acciones preventivas
(carbohidratos rápidos).

Estrategia
----------
Evaluamos múltiples horizontes cortos (15, 20, 30 min) usando el mismo modelo
físico + Monte Carlo del predictor principal, pero específicamente enfocados
en la probabilidad de cruzar el umbral de hipoglucemia.

Para cada horizonte:
    P(G(t) < 70) = ∫_{-∞}^{70} N(μ_t, σ_t²) dt
                 = Φ((70 - μ_t) / σ_t)

donde Φ es la CDF normal estándar. Reutilizamos el MC que ya simula esto
empíricamente (devuelve p_hipo directamente).

Niveles de alarma
-----------------
  critical (p_hipo > 0.5  AND horizon ≤ 30 min)  → "Toma 15-20g AHORA"
  alert    (p_hipo > 0.3)                         → "Considera 10-15g preventivos"
  watch    (p_hipo > 0.15)                        → "Monitorea los próximos minutos"
  normal   (p_hipo < 0.15)                        → sin acción

Referencias
-----------
  - Cengiz & Tamborlane (2009) Diabetes Technol. Ther. — onset NovoRapid ~15min
  - Cobelli et al. (2009) — hypoglycemia detection in CGM
  - ADA Standards of Care 2024 — Rule of 15 (15g carbs, recheck 15min)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("pmm.hypo")

# ── Configuración ────────────────────────────────────────────────────────────
_HORIZONS_MIN     = (15, 20, 30)   # horizontes a evaluar
_HIPO_THRESHOLD   = 70.0           # mg/dL — umbral de hipoglucemia
_N_SIM            = 2_000          # menos sims que el predictor principal (más rápido)
_TAU_ROC          = 30.0           # τ del decaimiento exponencial del ROC (min)
_CACHE_TTL_S      = 60             # 1 min de cache (queremos respuesta fresca)

# ── Multi-criterio risk score ──
# Reemplaza el threshold-only sobre p_hipo. Combina 4 señales:
#   1. p_pred       — P(G<70) del Monte Carlo (señal del modelo)
#   2. proximity    — qué tan cerca está la glucosa actual del threshold
#   3. velocity     — qué tan rápido estás bajando (ROC negativo)
#   4. iob_pressure — insulina activa "presionando" hacia abajo
#
# Cada componente ∈ [0,1]. Combinados con pesos clínicamente justificados.
# Si CUALQUIER señal sola es muy alta (G<70 real, ROC<-2.5), gate critical.
_W_PRED       = 0.40   # peso del modelo
_W_PROXIMITY  = 0.30   # peso de la cercanía al threshold
_W_VELOCITY   = 0.20   # peso de la velocidad de descenso
_W_IOB        = 0.10   # peso del IOB activo

# Hard gates: condiciones que disparan critical/alert independientemente del score
_HARD_GATE_G_HYPO = 75.0   # G actual < 75 → critical
_HARD_GATE_G_LOW  = 85.0   # G actual < 85 → al menos alert
_HARD_GATE_ROC    = -2.5   # ROC < -2.5 → al menos alert

# Thresholds del risk score combinado (más selectivos que p_hypo solo)
_THRESH_CRITICAL  = 0.55
_THRESH_ALERT     = 0.38
_THRESH_WATCH     = 0.22

# Cache en memoria (proceso-local)
_cache: dict = {"computed_at": 0.0, "result": None}


def compute_hypo_risk(force: bool = False) -> dict:
    """
    Calcula el riesgo de hipoglucemia inminente.

    Returns
    -------
    {
        ok            : bool
        active        : bool   — True si nivel >= 'watch'
        level         : str    — 'normal' | 'watch' | 'alert' | 'critical'
        horizon_min   : int    — horizonte del peor caso
        g_pred        : float  — predicción central a ese horizonte
        sigma         : float  — incertidumbre
        p_hipo        : float  — P(G < 70) en el peor horizonte (0-1)
        per_horizon   : list[{horizon_min, g_pred, sigma, p_hipo}]
        action        : str    — acción recomendada en español
        narrativa     : str    — descripción legible
        computed_at   : str    — ISO timestamp
        error         : str | None
    }
    """
    import time as _t

    # ── Cache check ──
    now_mono = _t.monotonic()
    if (not force
        and _cache["result"] is not None
        and now_mono - _cache["computed_at"] < _CACHE_TTL_S):
        return _cache["result"]

    result = {
        "ok":          False,
        "active":      False,
        "level":       "normal",
        "horizon_min": None,
        "g_pred":      None,
        "sigma":       None,
        "p_hipo":      0.0,
        "per_horizon": [],
        "action":      "",
        "narrativa":   "",
        "computed_at": datetime.now().isoformat(),
        "error":       None,
    }

    try:
        import math as _math
        from models import InsulinDose, Meal, Activity
        from helpers import (
            _get_setting, _calcular_isf_personal, _calcular_icr_personal,
            _calcular_isf_circadiano, _isf_para_hora,
            _calcular_icr_circadiano, _icr_para_hora,
        )
        from utils.kinetics import (
            get_kinetics_snapshot, exercise_sensitivity_factor,
            current_iob, current_cob, current_basal_iob,
            dawn_roc_mgdl_min, _basal_inyeccion_reciente,
            _DEFAULT_PEAK_MIN, _DEFAULT_DIA_MIN,
        )
        from utils.monte_carlo import run_monte_carlo

        now  = datetime.now()
        hora = now.hour

        # ── Parámetros del modelo ──
        saved_dia = _get_setting("dia_min")
        dia_min   = int(float(saved_dia)) if saved_dia else _DEFAULT_DIA_MIN
        peak_min  = _DEFAULT_PEAK_MIN

        # PMM ISF/ICR con uncertainty
        isf_personal, _ = _calcular_isf_personal()
        icr_personal, _ = _calcular_icr_personal()
        isf_guardado = float(_get_setting("isf_manual")) if _get_setting("isf_manual") else None
        icr_guardado = float(_get_setting("icr"))         if _get_setting("icr")        else None

        pmm_isf_sigma = None
        pmm_icr_sigma = None
        drift_factor  = 1.0
        try:
            from pmm.core.parameter_store import get_isf_now, get_icr_now
            from pmm.engines.drift import get_drift_status
            pmm_isf = get_isf_now(hora=hora)
            pmm_icr = get_icr_now(hora=hora)
            if pmm_isf.get("source") != "prior" and pmm_isf.get("n_obs", 0) >= 3:
                isf_personal  = pmm_isf["mu"]
                pmm_isf_sigma = pmm_isf["sigma"]
            if pmm_icr.get("source") != "prior" and pmm_icr.get("n_obs", 0) >= 3:
                icr_personal  = pmm_icr["mu"]
                pmm_icr_sigma = pmm_icr["sigma"]
            drift_factor = get_drift_status().get("drift_factor", 1.0)
        except Exception:
            pass

        # PMM speed factors para COB
        pmm_speed_factors: dict | None = None
        try:
            from pmm.engines.absorption import get_speed_factor
            sf_fast = get_speed_factor("Dulces/Postres")
            sf_med  = get_speed_factor("Cereales")
            sf_slow = get_speed_factor("Legumbres")
            if sf_fast != 1.0 or sf_med != 1.0 or sf_slow != 1.0:
                pmm_speed_factors = {"FAST": sf_fast, "MED": sf_med, "SLOW": sf_slow}
        except Exception:
            pass

        # Circadiano + ejercicio + drift
        isf_circ = _calcular_isf_circadiano(days=90)
        isf_bloque, _, _ = _isf_para_hora(hora, isf_circ, isf_personal)
        isf_base = isf_guardado or isf_bloque or isf_personal

        icr_circ = _calcular_icr_circadiano(days=90)
        icr_bloque, _, _ = _icr_para_hora(hora, icr_circ, icr_personal)
        icr = icr_guardado or icr_bloque or icr_personal

        if not isf_base:
            result["error"] = "Sin ISF disponible"
            _cache_set(result)
            return result

        act_cutoff = now - timedelta(hours=24)
        activities = Activity.query.filter(Activity.timestamp >= act_cutoff).all()
        ex_factor  = exercise_sensitivity_factor(activities, at_time=now)

        # Aplicar drift al ISF efectivo (resistance >1 → eff ISF menor)
        isf_ef = round((isf_base or 0) * ex_factor / max(0.1, drift_factor), 1)

        # ── Snapshot actual ──
        snap = get_kinetics_snapshot(hours_lookback=6, dia_min=dia_min, peak_min=peak_min)
        g_actual       = snap["last_glucose"]
        roc            = snap["roc"]
        iob_bolus_now  = snap["iob_bolus"]
        iob_basal_now  = snap["iob_basal"]
        cob_now        = snap["cob"]

        if g_actual is None:
            result["error"] = "Sin lecturas de glucosa recientes"
            _cache_set(result)
            return result

        # Kalman opcional (si está convergido)
        sigma_g0 = 0.0
        try:
            from utils.kalman import get_current_estimate as kalman_estimate
            kalman = kalman_estimate(propagate=True)
            if kalman and kalman.get("sigma_G", 99) < 20:
                g_actual = round(kalman["G"], 1)
                sigma_g0 = kalman["sigma_G"]
                if roc is not None:
                    roc = round(0.7 * kalman["v"] + 0.3 * roc, 3)
                else:
                    roc = round(kalman["v"], 3)
        except Exception:
            pass

        # ── Cargar eventos para proyección ──
        cutoff_iob = now - timedelta(minutes=dia_min)
        boluses    = InsulinDose.query.filter(
            InsulinDose.type == "bolus",
            InsulinDose.timestamp >= cutoff_iob,
        ).all()
        fat_cutoff = now - timedelta(hours=8)
        meals_ext  = Meal.query.filter(Meal.timestamp >= fat_cutoff).all()

        dawn_roc = dawn_roc_mgdl_min(at_time=now)

        # ── Evaluar cada horizonte ──
        per_horizon = []
        worst = None

        for h in _HORIZONS_MIN:
            t_fut    = now + timedelta(minutes=h)
            iob_fut  = current_iob(boluses, at_time=t_fut, peak_min=peak_min, dia_min=dia_min)
            cob_fut  = current_cob(meals_ext, at_time=t_fut,
                                   pmm_speed_factors=pmm_speed_factors)

            iob_basal_fut = current_basal_iob(at_time=t_fut)
            basal_es_reciente = _basal_inyeccion_reciente(now, umbral_h=4)
            d_iob_basal = (iob_basal_now - iob_basal_fut) if basal_es_reciente else 0.0
            d_iob = (iob_bolus_now - iob_fut) + d_iob_basal
            d_cob = cob_now - cob_fut

            roc_eff_min     = _TAU_ROC * (1.0 - _math.exp(-h / _TAU_ROC))
            cob_suppression = max(0.15, 1.0 - (cob_now / 35.0))
            dawn_effect     = dawn_roc * roc_eff_min

            mc = run_monte_carlo(
                g_actual        = g_actual + dawn_effect,
                roc             = roc,
                roc_eff_min     = roc_eff_min,
                cob_suppression = cob_suppression,
                d_iob           = d_iob,
                d_cob           = d_cob,
                isf_base        = isf_ef,
                icr             = icr,
                n               = _N_SIM,
                sigma_g0        = sigma_g0,
                pmm_isf_sigma   = pmm_isf_sigma,
                pmm_icr_sigma   = pmm_icr_sigma,
            )

            # IMPORTANTE: run_monte_carlo retorna p_hipo como entero 0-100 (%)
            # Aquí normalizamos a proporción 0-1 para consistencia con la API
            # (los thresholds _THRESH_* y el JS del banner esperan 0-1).
            p_hipo_prop = mc["p_hipo"] / 100.0
            entry = {
                "horizon_min": h,
                "g_pred":      round(mc["g_pred_median"]),
                "sigma":       round(mc["sigma"], 1),
                "p_hipo":      round(p_hipo_prop, 3),
                "p5":          mc.get("p5"),
            }
            per_horizon.append(entry)

            # Peor caso = mayor p_hipo
            if worst is None or entry["p_hipo"] > worst["p_hipo"]:
                worst = entry

        # ── Multi-criterio risk scoring ──────────────────────────────
        # Combina p_hipo del MC con señales directas (glucemia actual,
        # ROC, IOB) para evitar falsos positivos del modelo sobre-confiado
        # y mejorar recall cuando la hipo es evidente sin necesidad de
        # que el MC la prediga perfectamente.
        p   = worst["p_hipo"]
        h_w = worst["horizon_min"]
        risk_score, components, hard_gate = _compute_risk_score(
            p_hipo_pred = p,
            g_actual    = g_actual,
            roc         = roc,
            iob_bolus   = iob_bolus_now,
        )

        # Determinación de nivel:
        #   - Hard gates dominan: g_actual<75 → siempre critical, etc.
        #   - Sino, el risk_score combinado
        if hard_gate == "critical":
            level = "critical"
            action = ("Toma 15–20g de carbohidratos rápidos AHORA "
                      "(jugo, glucosa en gel, miel). Re-checa en 15 min.")
        elif hard_gate == "alert":
            level = "alert"
            action = ("Tu glucosa o tendencia disparan alerta directa. "
                      "Toma 10–15g de carbohidratos preventivos.")
        elif risk_score >= _THRESH_CRITICAL and h_w <= 30:
            level   = "critical"
            action  = ("Toma 15–20g de carbohidratos rápidos AHORA. "
                       "Re-checa tu glucemia en 15 min.")
        elif risk_score >= _THRESH_ALERT:
            level   = "alert"
            action  = ("Considera tomar 10–15g de carbohidratos preventivos. "
                       "Si vas a manejar o hacer ejercicio, hacelo ya.")
        elif risk_score >= _THRESH_WATCH:
            level   = "watch"
            action  = ("Monitorea tu glucosa en los próximos 15–20 min. "
                       "Evita nuevas dosis de insulina sin re-evaluar.")
        else:
            level   = "normal"
            action  = ""

        # ── Narrativa ──
        narrativa = _build_narrative(level, worst, g_actual, roc)

        result.update({
            "ok":          True,
            "active":      level != "normal",
            "level":       level,
            "risk_score":  round(risk_score, 3),
            "risk_components": components,
            "hard_gate":   hard_gate,
            "horizon_min": h_w,
            "g_pred":      worst["g_pred"],
            "sigma":       worst["sigma"],
            "p_hipo":      p,
            "per_horizon": per_horizon,
            "action":      action,
            "narrativa":   narrativa,
            "g_actual":    g_actual,
            "roc":         round(roc, 2) if roc is not None else None,
            "iob_bolus":   round(iob_bolus_now, 2),
            "cob":         round(cob_now, 1),
        })

    except Exception as exc:
        logger.exception("Error en compute_hypo_risk")
        result["error"] = str(exc)

    _cache_set(result)
    return result


def _cache_set(result: dict) -> None:
    import time as _t
    _cache["computed_at"] = _t.monotonic()
    _cache["result"]      = result


def _build_narrative(level: str, worst: dict, g_actual: float, roc: Optional[float]) -> str:
    """Genera narrativa en español según nivel y datos del peor caso."""
    if level == "normal":
        return ("Sin riesgo de hipoglucemia detectado en los próximos 30 minutos. "
                f"Glucosa actual: {g_actual:.0f} mg/dL.")

    p_pct = round(worst["p_hipo"] * 100)
    h     = worst["horizon_min"]
    g_p   = worst["g_pred"]
    sig   = worst["sigma"]

    trend = ""
    if roc is not None:
        if roc < -1.5:
            trend = f" La glucosa está bajando rápido ({roc:+.1f} mg/dL/min)."
        elif roc < -0.5:
            trend = f" La glucosa está descendiendo ({roc:+.1f} mg/dL/min)."

    if level == "critical":
        prefix = "⚠️ HIPOGLUCEMIA INMINENTE"
    elif level == "alert":
        prefix = "🟡 Riesgo alto de hipoglucemia"
    else:  # watch
        prefix = "🔵 Riesgo moderado de hipoglucemia"

    return (
        f"{prefix}: probabilidad {p_pct}% de caer bajo 70 mg/dL en {h} min "
        f"(predicción {g_p}±{sig:.0f} mg/dL).{trend}"
    )


def _compute_risk_score(
    p_hipo_pred:  float,
    g_actual:     float,
    roc:          Optional[float],
    iob_bolus:    float,
) -> tuple[float, dict, Optional[str]]:
    """
    Calcula un risk_score multi-criterio ∈ [0, 1] desde 4 señales independientes.

    Diseño
    ------
    El modelo MC solo (p_hipo_pred) es ruidoso cuando el SSM está mal calibrado.
    Combinar con señales directas (glucemia actual, velocidad, insulina activa)
    reduce drásticamente los falsos positivos sin sacrificar recall.

    Returns
    -------
    (risk_score, components, hard_gate)
      - risk_score: combinación ponderada de las 4 señales
      - components: dict con cada subseñal (para debug/auditoría)
      - hard_gate:  'critical' | 'alert' | None — sobrescribe el score si
                    una condición clínicamente clara está presente
                    (ej. glucemia < 75 ya es hipo inminente sin importar
                     lo que diga el MC)
    """
    # ── Subseñales ──
    # 1. P(hipo) del MC — señal del modelo (acepta 0-1)
    s_pred = max(0.0, min(1.0, p_hipo_pred))

    # 2. Proximity — qué tan cerca está la glucosa actual del threshold.
    #    G=70 → 1.0, G=100 → 0.5, G=130 → 0
    s_prox = max(0.0, (130.0 - g_actual) / 60.0)
    s_prox = min(1.0, s_prox)

    # 3. Velocity — qué tan rápido estás bajando (solo ROC negativo cuenta)
    #    ROC=-1 → 0.5, ROC=-2 → 1.0, ROC≥0 → 0
    if roc is None or roc >= 0:
        s_velo = 0.0
    else:
        s_velo = min(1.0, abs(roc) / 2.0)

    # 4. IOB pressure — insulina activa rápida
    #    IOB=0.5U → 0, IOB=2.5U → 1.0
    s_iob = max(0.0, (iob_bolus - 0.5) / 2.0)
    s_iob = min(1.0, s_iob)

    # ── Combinación ponderada ──
    risk = (_W_PRED      * s_pred  +
            _W_PROXIMITY * s_prox  +
            _W_VELOCITY  * s_velo  +
            _W_IOB       * s_iob)

    # ── Hard gates (override clínico) ──
    hard_gate = None
    if g_actual < _HARD_GATE_G_HYPO:
        hard_gate = "critical"        # ya estás cerca de hipo real
    elif g_actual < _HARD_GATE_G_LOW:
        hard_gate = "alert"            # glucosa baja, atención obligada
    elif roc is not None and roc <= _HARD_GATE_ROC:
        hard_gate = "alert"            # caída muy rápida — actuar pronto

    components = {
        "p_pred":       round(s_pred, 3),
        "proximity":    round(s_prox, 3),
        "velocity":     round(s_velo, 3),
        "iob_pressure": round(s_iob, 3),
        "weights":      {"pred": _W_PRED, "proximity": _W_PROXIMITY,
                         "velocity": _W_VELOCITY, "iob": _W_IOB},
    }
    return risk, components, hard_gate


def invalidate_cache() -> None:
    """Fuerza recálculo en la próxima llamada (útil tras nuevas dosis/comidas)."""
    _cache["computed_at"] = 0.0
    _cache["result"]      = None
