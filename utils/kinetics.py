"""
utils/kinetics.py — Insulin On Board (IOB), Carbs On Board (COB),
                    Glucose Rate-of-Change (ROC), and DIA estimation.

Models used
-----------
IOB  — Bilinear activity curve (OpenAPS reference implementation).
       Insulin activity rises linearly from injection to peak_min,
       then falls linearly to zero at dia_min.  IOB is the remaining
       integral of that activity from "now" to DIA end.

COB  — Linear absorption model with GI-adjusted absorption time.
       High-GI foods are absorbed faster; low-GI foods slower.
       Fiber slows absorption further (if data available).

ROC  — Weighted linear regression over the last `window_min` minutes
       of CGM readings. Returns slope in mg/dL per minute.

DIA  — Estimated from correction-bolus events: find how long each
       correction bolus took to reach glucose nadir, then scale to
       full DIA (nadir ≈ 55-65 % of DIA for rapid-acting insulins).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

# ── Default pharmacokinetic parameters (NovoRapid / aspart) ──────────────────
_DEFAULT_PEAK_MIN: int = 75    # minutes to peak activity
_DEFAULT_DIA_MIN:  int = 240   # duration of insulin action (4 h)

# GI → absorption time (minutes to absorb 90 % of carbs)
_GI_ABSORPTION: dict[str, int] = {
    "low":    180,   # GI < 55
    "medium": 120,   # 55 ≤ GI ≤ 70
    "high":    75,   # GI > 70
}
_DEFAULT_ABSORPTION_MIN = 120   # used when GI is unknown

# Nadir ratio — for rapid-acting insulins, glucose nadir occurs at roughly
# 60 % of DIA from injection time (OpenAPS empirical data).
_NADIR_RATIO = 0.60


# ── Helper: bilinear insulin activity ────────────────────────────────────────

def _activity_at(t_min: float, peak_min: int, dia_min: int) -> float:
    """
    Fraction of insulin activity (not IOB) at time t_min post-injection.
    Triangle wave: rises 0→1 up to peak_min, falls 1→0 from peak_min to dia_min.
    Normalised so the total area (= total insulin delivery) = 1.
    """
    if t_min <= 0 or t_min >= dia_min:
        return 0.0
    if t_min <= peak_min:
        # Rising slope: 0 → peak_activity
        peak_activity = 2.0 / dia_min  # area of triangle = 1
        return peak_activity * (t_min / peak_min)
    else:
        # Falling slope: peak_activity → 0
        peak_activity = 2.0 / dia_min
        return peak_activity * (1.0 - (t_min - peak_min) / (dia_min - peak_min))


def _iob_fraction(t_min: float, peak_min: int, dia_min: int) -> float:
    """
    Fraction of a single bolus that is still active at t_min post-injection.
    Computed as the integral of _activity_at from t_min to dia_min,
    divided by the total integral (which equals 1 by construction).
    Uses analytical formula for the piecewise linear activity curve.
    Returns value in [0, 1].
    """
    if t_min <= 0:
        return 1.0
    if t_min >= dia_min:
        return 0.0

    def _area_from(start: float) -> float:
        """Integral of activity from `start` to dia_min."""
        if start >= dia_min:
            return 0.0
        # Split at peak_min
        peak_activity = 2.0 / dia_min

        # Area of the rising segment: from start to peak_min
        if start < peak_min:
            t1 = peak_min
            # Rising trapezoid from start to t1
            a_start = peak_activity * (start / peak_min)
            a_end   = peak_activity
            area_rising = (a_start + a_end) / 2 * (t1 - start)
        else:
            area_rising = 0.0

        # Area of the falling segment: from max(start, peak_min) to dia_min
        t_fall_start = max(start, float(peak_min))
        if t_fall_start < dia_min:
            a_fs = peak_activity * (1.0 - (t_fall_start - peak_min) / (dia_min - peak_min))
            a_fe = 0.0
            area_falling = (a_fs + a_fe) / 2 * (dia_min - t_fall_start)
        else:
            area_falling = 0.0

        return area_rising + area_falling

    return _area_from(t_min)  # already normalised (total = 1)


# ── Public: current IOB ───────────────────────────────────────────────────────

def current_iob(
    bolus_list,
    at_time: Optional[datetime] = None,
    peak_min: Optional[int] = None,
    dia_min: Optional[int] = None,
) -> float:
    """
    Compute total active insulin (Units) at `at_time`.

    Parameters
    ----------
    bolus_list : list of InsulinDose model objects (type == 'bolus')
    at_time    : datetime to evaluate at (default: now)
    peak_min   : minutes to peak activity (default: _DEFAULT_PEAK_MIN)
    dia_min    : duration of insulin action in minutes (default: _DEFAULT_DIA_MIN)

    Returns
    -------
    float — total IOB in Units (≥ 0)
    """
    if at_time is None:
        at_time = datetime.now()
    peak = peak_min or _DEFAULT_PEAK_MIN
    dia  = dia_min  or _DEFAULT_DIA_MIN

    total_iob = 0.0
    for dose in bolus_list:
        dt = dose.timestamp
        elapsed_min = (at_time - dt).total_seconds() / 60.0
        if 0 < elapsed_min < dia:
            frac = _iob_fraction(elapsed_min, peak, dia)
            total_iob += dose.units * frac
    return round(total_iob, 2)


# ── Public: current COB ───────────────────────────────────────────────────────

def _absorption_time_for_meal(meal) -> int:
    """
    Estimate carbohydrate absorption time (minutes) for a Meal object.
    Uses the GI of meal components if available; otherwise the default.
    Adjusts for fiber content — more fiber → slower absorption.
    """
    # Collect GI values from components
    gi_values = []
    fiber_total = 0.0
    carbs_total = getattr(meal, "carbs_g", 0) or 0

    if hasattr(meal, "components") and meal.components:
        for comp in meal.components:
            if comp.glycemic_index:
                gi_values.append(comp.glycemic_index)
            fiber_total += getattr(comp, "fiber_g", 0) or 0

    if gi_values:
        avg_gi = sum(gi_values) / len(gi_values)
        if avg_gi < 55:
            base = _GI_ABSORPTION["low"]
        elif avg_gi <= 70:
            base = _GI_ABSORPTION["medium"]
        else:
            base = _GI_ABSORPTION["high"]
    else:
        base = _DEFAULT_ABSORPTION_MIN

    # Fiber adjustment: each 5g of fiber adds ~15 min to absorption
    if carbs_total > 0 and fiber_total > 0:
        fiber_factor = min(fiber_total / 5.0, 4.0)  # cap at +60 min
        base += int(fiber_factor * 15)

    return base


def _cob_fraction_linear(elapsed_min: float, absorption_min: int) -> float:
    """
    Fraction of meal carbs still unabsorbed at `elapsed_min` post-meal.
    Simple linear model (adequate for practical use).
    Returns value in [0, 1].
    """
    if elapsed_min <= 0:
        return 1.0
    if elapsed_min >= absorption_min:
        return 0.0
    return 1.0 - elapsed_min / absorption_min


def current_cob(
    meal_list,
    at_time: Optional[datetime] = None,
) -> float:
    """
    Compute total active carbohydrates (grams) at `at_time`.

    Parameters
    ----------
    meal_list : list of Meal model objects
    at_time   : datetime to evaluate at (default: now)

    Returns
    -------
    float — total COB in grams (≥ 0)
    """
    if at_time is None:
        at_time = datetime.now()

    total_cob = 0.0
    for meal in meal_list:
        carbs = getattr(meal, "carbs_g", 0) or 0
        if carbs <= 0:
            continue
        absorption_min = _absorption_time_for_meal(meal)
        elapsed_min = (at_time - meal.timestamp).total_seconds() / 60.0
        frac = _cob_fraction_linear(elapsed_min, absorption_min)
        total_cob += carbs * frac
    return round(total_cob, 1)


# ── Public: glucose rate of change ───────────────────────────────────────────

def glucose_roc(readings, window_min: int = 20) -> Optional[float]:
    """
    Compute glucose rate-of-change (mg/dL per minute) using weighted
    linear regression over the last `window_min` minutes.

    Parameters
    ----------
    readings   : list of GlucoseReading objects (any order; sorted internally)
    window_min : minutes of history to use

    Returns
    -------
    float slope in mg/dL/min, or None if insufficient data (< 2 points).
    """
    if not readings:
        return None

    now = datetime.now()
    cutoff = now - timedelta(minutes=window_min)
    recent = [r for r in readings if r.timestamp >= cutoff]

    # Need at least 2 points
    if len(recent) < 2:
        return None

    # Weighted least-squares: newer points get higher weight
    times  = [(r.timestamp - recent[0].timestamp).total_seconds() / 60.0 for r in recent]
    values = [r.value_mgdl for r in recent]
    n      = len(times)

    # Weights: linear from 0.5 (oldest) to 1.0 (newest)
    weights = [0.5 + 0.5 * i / (n - 1) for i in range(n)]

    w_sum   = sum(weights)
    t_mean  = sum(w * t for w, t in zip(weights, times)) / w_sum
    v_mean  = sum(w * v for w, v in zip(weights, values)) / w_sum

    num = sum(w * (t - t_mean) * (v - v_mean) for w, t, v in zip(weights, times, values))
    den = sum(w * (t - t_mean) ** 2 for w, t in zip(weights, times))

    if den == 0:
        return None

    slope = num / den  # mg/dL per minute
    return round(slope, 3)


def roc_arrow(slope_mgdl_per_min: Optional[float]) -> str:
    """
    Convert a mg/dL/min slope to a CGM-style trend arrow.
    Thresholds follow Dexcom G6 / Libre 3 conventions.
    """
    if slope_mgdl_per_min is None:
        return "→"
    s = slope_mgdl_per_min
    if s > 3:     return "↑↑"   # > 3 mg/min = rapid rise
    if s > 1.5:   return "↑"    # > 1.5 mg/min = rising
    if s > 0.5:   return "↗"    # > 0.5 mg/min = slight rise
    if s < -3:    return "↓↓"   # < -3 mg/min = rapid fall
    if s < -1.5:  return "↓"    # < -1.5 mg/min = falling
    if s < -0.5:  return "↘"    # < -0.5 mg/min = slight fall
    return "→"                   # stable


# ── Public: estimate DIA from data ───────────────────────────────────────────

def estimate_dia_from_data(days: int = 90, peak_min: int = _DEFAULT_PEAK_MIN) -> dict:
    """
    Estimate personal DIA from correction bolus events in the database.

    Method
    ------
    Priority 1: boluses explicitly labeled purpose='correccion' — most reliable.
    Priority 2: boluses with no meal within ±90 min (inferred corrections).

    For each correction bolus:
    1. Find the glucose nadir in the 4 hours following.
    2. Estimate DIA ≈ time_to_nadir / _NADIR_RATIO.
    3. Return median estimate + details.

    Returns
    -------
    dict with keys:
        estimated_dia_min : int or None
        sample_size       : int (number of usable correction events)
        estimates         : list[int] (individual DIA estimates)
        confidence        : str ("alta" / "media" / "baja" / "insuficiente")
        labeled_count     : int (how many were explicitly labeled)
        message           : str (human-readable explanation)
    """
    from models import InsulinDose, Meal, GlucoseReading

    cutoff = datetime.now() - timedelta(days=days)
    boluses = InsulinDose.query.filter(
        InsulinDose.type == "bolus",
        InsulinDose.timestamp >= cutoff,
    ).order_by(InsulinDose.timestamp).all()

    meals = Meal.query.filter(Meal.timestamp >= cutoff).all()
    meal_times = [m.timestamp for m in meals]

    # Separate explicitly labeled from inferred corrections
    labeled_corrections = [b for b in boluses if b.purpose == "correccion"]
    labeled_count = len(labeled_corrections)

    estimates = []
    labeled_used = 0
    for bolus in boluses:
        bt = bolus.timestamp
        is_labeled = bolus.purpose == "correccion"

        if not is_labeled:
            # Skip if a meal is within ±90 min (not a pure correction)
            confounded = any(
                abs((mt - bt).total_seconds()) < 90 * 60
                for mt in meal_times
            )
            if confounded:
                continue

        # Fetch glucose readings in the 4 hours after the bolus
        window_end = bt + timedelta(hours=4)
        readings = GlucoseReading.query.filter(
            GlucoseReading.timestamp >= bt,
            GlucoseReading.timestamp <= window_end,
        ).order_by(GlucoseReading.timestamp).all()

        if len(readings) < 3:
            continue

        # Find nadir (minimum glucose value)
        nadir_r = min(readings, key=lambda r: r.value_mgdl)
        nadir_min = (nadir_r.timestamp - bt).total_seconds() / 60.0

        # Require nadir between 30 min and 210 min (sanity check)
        if not (30 <= nadir_min <= 210):
            continue

        # Estimate DIA
        dia_est = int(nadir_min / _NADIR_RATIO)

        # Plausible range for NovoRapid: 120–360 min
        if 120 <= dia_est <= 360:
            estimates.append({"dia": dia_est, "labeled": is_labeled})

    if not estimates:
        tip = (
            "Etiquetá tus próximas correcciones como 'Corrección' en el formulario "
            "de insulina para mejorar la estimación."
        ) if labeled_count == 0 else ""
        return {
            "estimated_dia_min": None,
            "sample_size": 0,
            "labeled_count": labeled_count,
            "estimates": [],
            "confidence": "insuficiente",
            "message": (
                f"No se encontraron suficientes eventos de corrección utilizables "
                f"en los últimos {days} días. Se usará DIA por defecto "
                f"({_DEFAULT_DIA_MIN} min). {tip}"
            ),
        }

    dia_values = [e["dia"] for e in estimates]
    n_labeled  = sum(1 for e in estimates if e["labeled"])
    dia_values.sort()
    n = len(dia_values)

    # Median
    median = dia_values[n // 2] if n % 2 else (dia_values[n // 2 - 1] + dia_values[n // 2]) // 2
    # Round to nearest 15 min
    median_rounded = round(median / 15) * 15

    # Higher confidence when events are explicitly labeled
    if n >= 6 or (n >= 3 and n_labeled >= 2):
        confidence = "alta"
    elif n >= 3 or n_labeled >= 1:
        confidence = "media"
    else:
        confidence = "baja"

    label_note = f" ({n_labeled} etiquetadas manualmente)" if n_labeled > 0 else " (inferidas)"

    return {
        "estimated_dia_min": median_rounded,
        "sample_size": n,
        "labeled_count": labeled_count,
        "labeled_used": n_labeled,
        "estimates": dia_values,
        "confidence": confidence,
        "message": (
            f"DIA estimado: {median_rounded} min ({median_rounded // 60}h "
            f"{median_rounded % 60}min) con {n} corrección(es){label_note}. "
            f"Confianza {confidence}."
        ),
    }


# ── Public: load data and compute snapshot ────────────────────────────────────

def get_kinetics_snapshot(
    hours_lookback: int = 6,
    dia_min: Optional[int] = None,
    peak_min: Optional[int] = None,
) -> dict:
    """
    Single-call convenience function: load relevant DB records and return
    the current IOB, COB, glucose ROC, and trend arrow.

    Parameters
    ----------
    hours_lookback : how far back to load boluses and meals (default 6h covers DIA)
    dia_min        : DIA override; if None, reads from UserSettings or uses default
    peak_min       : peak override; if None, uses _DEFAULT_PEAK_MIN

    Returns
    -------
    dict:
        iob        : float (Units)
        cob        : float (grams)
        roc        : float or None (mg/dL/min)
        arrow      : str (trend arrow)
        last_glucose : float or None (most recent reading)
        context    : str (human-readable summary)
    """
    from models import InsulinDose, Meal, GlucoseReading
    from helpers import _get_setting

    now = datetime.now()
    cutoff = now - timedelta(hours=hours_lookback)

    # Resolve DIA from settings or default
    if dia_min is None:
        saved_dia = _get_setting("dia_min")
        dia_min = int(saved_dia) if saved_dia else _DEFAULT_DIA_MIN
    if peak_min is None:
        peak_min = _DEFAULT_PEAK_MIN

    # Load data
    boluses = InsulinDose.query.filter(
        InsulinDose.type == "bolus",
        InsulinDose.timestamp >= cutoff,
    ).all()

    meals = Meal.query.filter(Meal.timestamp >= cutoff).all()

    roc_cutoff = now - timedelta(minutes=30)
    cgm_readings = GlucoseReading.query.filter(
        GlucoseReading.timestamp >= roc_cutoff,
    ).order_by(GlucoseReading.timestamp).all()

    # Compute
    iob   = current_iob(boluses, at_time=now, peak_min=peak_min, dia_min=dia_min)
    cob   = current_cob(meals, at_time=now)
    slope = glucose_roc(cgm_readings, window_min=20)
    arrow = roc_arrow(slope)

    last_glucose = cgm_readings[-1].value_mgdl if cgm_readings else None

    # Human-readable context
    parts = []
    if iob > 0.1:
        parts.append(f"IOB {iob:.1f} U")
    if cob > 1:
        parts.append(f"COB {cob:.0f} g")
    if slope is not None:
        parts.append(f"{arrow} {abs(slope * 10):.0f} mg/dL·10min")
    context = "  ·  ".join(parts) if parts else "Sin insulina ni carbohidratos activos"

    return {
        "iob":          iob,
        "cob":          cob,
        "roc":          slope,
        "arrow":        arrow,
        "last_glucose": last_glucose,
        "context":      context,
        "dia_min":      dia_min,
        "peak_min":     peak_min,
    }


# ── Adjusted meal analysis ────────────────────────────────────────────────────

def adjusted_glucose_delta(
    meal,
    readings,
    isf: float,
    iob_at_meal: float,
    hours_post: int = 3,
) -> Optional[dict]:
    """
    Compute the glucose delta after a meal, adjusted for the IOB present
    at the time of the meal.  This isolates the meal's glycaemic effect
    from the insulin that was already working.

    Delta_neto = ΔGlucosa_real + (IOB_al_comer × ISF)

    A high positive Delta_neto suggests the meal raised glucose more than
    expected.  A near-zero value means glucose was well-managed.

    Parameters
    ----------
    meal        : Meal object
    readings    : list of GlucoseReading objects (pre-loaded, any range)
    isf         : personal ISF in mg/dL per Unit
    iob_at_meal : IOB at the time of the meal (Units)
    hours_post  : window for finding peak / delta

    Returns
    -------
    dict with keys: baseline, peak, raw_delta, iob_correction, adjusted_delta
    or None if insufficient readings.
    """
    mt = meal.timestamp
    window_start = mt - timedelta(minutes=30)
    window_end   = mt + timedelta(hours=hours_post)

    pre  = [r for r in readings if window_start <= r.timestamp <= mt]
    post = [r for r in readings if mt < r.timestamp <= window_end]

    if not pre or not post:
        return None

    baseline  = pre[-1].value_mgdl          # glucose just before meal
    peak_val  = max(r.value_mgdl for r in post)
    raw_delta = peak_val - baseline
    iob_correction = iob_at_meal * isf      # how much glucose IOB was expected to lower

    adjusted_delta = raw_delta + iob_correction

    return {
        "baseline":        round(baseline, 1),
        "peak":            round(peak_val, 1),
        "raw_delta":       round(raw_delta, 1),
        "iob_correction":  round(iob_correction, 1),
        "adjusted_delta":  round(adjusted_delta, 1),
    }
