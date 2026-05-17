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

import math
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

# ── 2-compartment absorption constants ───────────────────────────────────────
# Stomach → Gut → Blood model (Dalla Man et al. 2007, simplified)
#   dS/dt = -k_a * S                  (gastric emptying)
#   dI/dt =  k_a * S - k_g * I        (gut absorption)
#   COB(t) = S(t) + I(t)              (total still in GI tract)
#
# k_a: gastric emptying rate — depends on GI, fat, fiber
# k_g: gut→blood transfer rate — roughly constant for mixed meals
_K_GUT     = 0.025   # min⁻¹, intestinal absorption rate (half-life ~28 min)
_K_A_HIGH  = 0.030   # min⁻¹, high GI  (>70) — half-life ~23 min
_K_A_MED   = 0.020   # min⁻¹, medium GI (55-70) — half-life ~35 min
_K_A_LOW   = 0.013   # min⁻¹, low GI   (<55)  — half-life ~53 min
_K_A_DEF   = 0.020   # default when GI unknown

# ── Dawn phenomenon constants ─────────────────────────────────────────────────
# Cortisol + GH secretion pulse 3–8am causes hepatic glucose output.
# Modelled as a Gaussian bell on the glucose ROC.
# Ref: Bolli et al. (1984) NEJM; Perriello et al. (1997) Diabetes.
_DAWN_PEAK_H    = 5.5    # peak at 05:30 local time
_DAWN_SIGMA_H   = 1.3    # Gaussian width (σ); 2σ ≈ 3am–8am window
_DAWN_H_START   = 3.0    # earliest hour where effect applies
_DAWN_H_END     = 8.5    # latest hour where effect applies
_DAWN_MAG_DEF   = 0.45   # default peak magnitude (mg/dL/min) — conservative
_DAWN_MAG_KEY   = "dawn_magnitude"   # UserSettings key for personalised value

# ── Exercise sensitivity model ────────────────────────────────────────────────
# Clinical basis:
#   • Aerobic: enhanced insulin sensitivity +10–35%, lasting 12–24h post-exercise.
#     Peaks at 4–8h. Magnitude depends on intensity and duration.
#     Ref: Borghouts & Keizer (2000) Int J Sports Med; Richter & Hargreaves (2013).
#   • Anaerobic (resistance/HIIT): ACUTE reduction (0–2h) due to counter-regulatory
#     hormones, followed by a recovery enhancement phase (2–16h, smaller than aerobic).
#     Ref: Marliss & Vranic (2002) Diabetes; van Dijk et al. (2012).

_AEROBIC_KEYWORDS = {
    "caminata", "caminar", "correr", "trotar", "trote", "running", "jogging",
    "natacion", "natación", "nadar", "ciclismo", "bicicleta", "cycling",
    "cardio", "yoga", "pilates", "baile", "danza", "zumba", "eliptica",
    "elíptica", "remo", "senderismo", "marcha", "aerobico", "aeróbico",
    "tennis", "tenis", "futbol", "fútbol", "basket", "basquetbol",
}

_ANAEROBIC_KEYWORDS = {
    "pesas", "pesa", "gimnasio", "gym", "crossfit", "hiit", "sentadillas",
    "press", "curl", "mancuernas", "barra", "levantamiento", "powerlifting",
    "sprint", "sprints", "velocidad", "fuerza", "musculacion", "musculación",
    "funcional", "calistenia",
}

# Intensity → effect magnitude multiplier
_INTENSITY_MULT: dict[str, float] = {"baja": 0.6, "media": 1.0, "alta": 1.4}


def _classify_exercise(name: str, exercise_type: Optional[str]) -> str:
    """
    Return 'aerobico', 'anaerobico', or 'mixto'.
    Explicit exercise_type on the Activity model takes precedence;
    otherwise inferred from keyword matching on the activity name.
    """
    if exercise_type in ("aerobico", "anaerobico", "mixto"):
        return exercise_type
    name_lower = (name or "").lower()
    words = set(name_lower.replace(",", " ").replace(";", " ").split())
    is_aerobic   = bool(words & _AEROBIC_KEYWORDS)
    is_anaerobic = bool(words & _ANAEROBIC_KEYWORDS)
    if is_aerobic and is_anaerobic:
        return "mixto"
    if is_aerobic:
        return "aerobico"
    if is_anaerobic:
        return "anaerobico"
    return "mixto"   # unknown → conservative middle-ground


def exercise_sensitivity_factor(
    activities,
    at_time: Optional[datetime] = None,
) -> float:
    """
    Compute an insulin-sensitivity multiplier from recent exercise.

    Returns
    -------
    float  — multiplier applied to ISF:
        > 1.0  more sensitive (aerobic recovery) → smaller correction dose
        < 1.0  less sensitive (anaerobic acute)  → larger correction dose
        = 1.0  no relevant exercise

    Clamped to [0.70, 1.50] for safety.

    Model (clinical references in module docstring)
    -----------------------------------------------
    Aerobic exercise:
        • Duration multiplier  = min(duration_h, 2.0)  [caps at 2h equivalent]
        • Intensity multiplier = 0.6 / 1.0 / 1.4  (baja/media/alta)
        • Max Δ sensitivity    = +35 % × intensity × duration_mult
        • Time curve           : small at 0–2h → rises → peaks 4–12h → fades 12–24h

    Anaerobic exercise:
        • Acute phase (0–2h)   : −15 % × intensity  (counter-regulatory hormones)
        • Recovery phase (2–16h): +15 % × intensity × duration_mult
          peaks 4–10h post-exercise, fades to zero at 16h

    Mixed exercise: average of both profiles.
    """
    if at_time is None:
        at_time = datetime.now()

    total_delta = 0.0

    for act in activities:
        elapsed_h = (at_time - act.timestamp).total_seconds() / 3600.0
        if elapsed_h < 0 or elapsed_h > 24:
            continue

        duration_h    = ((act.duration_min or 30) / 60.0)
        dur_mult      = min(duration_h, 2.0)           # cap at 2h equivalent
        int_mult      = _INTENSITY_MULT.get(act.intensity or "media", 1.0)
        ex_type       = _classify_exercise(act.activity_type, act.exercise_type)

        if ex_type == "aerobico":
            # Max Δ at full intensity + 2h duration = +35 %
            max_delta = 0.35 * int_mult * dur_mult
            if elapsed_h < 2:
                delta = max_delta * 0.30 * (elapsed_h / 2)
            elif elapsed_h < 4:
                delta = max_delta * (0.30 + 0.35 * (elapsed_h - 2) / 2)
            elif elapsed_h < 12:
                # Peak plateau
                delta = max_delta * (0.65 + 0.35 * (elapsed_h - 4) / 8)
            elif elapsed_h < 24:
                delta = max_delta * (1.0 - (elapsed_h - 12) / 12)
            else:
                delta = 0.0
            total_delta += delta

        elif ex_type == "anaerobico":
            if elapsed_h < 2:
                # Acute: counter-regulatory response, less sensitive
                acute_max = -0.15 * int_mult
                delta = acute_max * (1.0 - elapsed_h / 2.0)
            elif elapsed_h < 16:
                # Recovery enhancement
                recovery_max = 0.15 * int_mult * dur_mult
                if elapsed_h < 6:
                    delta = recovery_max * (elapsed_h - 2) / 4.0
                elif elapsed_h < 10:
                    delta = recovery_max
                else:
                    delta = recovery_max * (1.0 - (elapsed_h - 10) / 6.0)
            else:
                delta = 0.0
            total_delta += delta

        else:  # mixto
            # Mild acute dip, then moderate recovery
            if elapsed_h < 1:
                delta = -0.05 * int_mult
            elif elapsed_h < 12:
                delta = 0.20 * int_mult * dur_mult * (elapsed_h - 1) / 11.0
            elif elapsed_h < 24:
                delta = 0.20 * int_mult * dur_mult * (1.0 - (elapsed_h - 12) / 12.0)
            else:
                delta = 0.0
            total_delta += delta

    factor = 1.0 + total_delta
    factor = max(0.70, min(1.50, factor))
    return round(factor, 3)

# ── Fat + Protein glucose-equivalent model ────────────────────────────────────
# Clinical basis:
#   • Wolpert et al. (2013) Diabetes Care: 50g fat → ~50–90 mg/dL glucose rise
#     over 5–8h in T1D. Effect peaks ~4–5h post-meal.
#   • Smart et al. (2013) Diabetes Care: >75g protein raises glucose significantly
#     independent of carbs, peaking ~3–4h post-meal.
#   • Conversion fractions (conservative clinical approximation):
#       Fat:     ~10% converts to glucose equivalent (glycerol → gluconeogenesis)
#       Protein: ~50% converts to glucose equivalent (glucogenic amino acids)
#   These are intentionally conservative to avoid hypoglycemia from over-bolusing.
_FAT_TO_GLUCOSE_FRAC   = 0.10   # 10 % of fat → glucose equivalent
_PROT_TO_GLUCOSE_FRAC  = 0.50   # 50 % of protein → glucose equivalent
_FAT_ABSORPTION_MIN    = 300    # peak glucose effect ~5h after meal
_PROT_ABSORPTION_MIN   = 240    # peak glucose effect ~4h after meal

# Thresholds that trigger clinically significant delayed rise
_FAT_THRESHOLD_G  = 25   # > 25g fat in a meal → extended bolus consideration
_PROT_THRESHOLD_G = 40   # > 40g protein → extended bolus consideration

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


# ── Basal insulin (long-acting analogs) ──────────────────────────────────────
#
# Long-acting insulins (glargina, detemir, degludec) have a peakless flat
# activity profile — appropriate for a linear decay model (no triangular peak).
#
# IOB_basal(t) = dose × max(0, 1 − elapsed_min / dia_min)
#
# This is simpler than the bolus bilinear model because there is no pronounced
# peak; the full dose starts acting at injection and decays linearly to zero.

_BASAL_DIA_MIN: dict = {
    "glargina":   1440,   # 24 h — Lantus, Basaglar, Semglee
    "toujeo":     2160,   # 36 h — Glargina U-300
    "detemir":     960,   # 16 h — Levemir (average across dose range)
    "degludec":   2520,   # 42 h — Tresiba
    "nph":         720,   # 12 h — Insulina NPH / isofánica
}
_BASAL_DIA_DEFAULT = 1440  # glargina como fallback


def current_basal_iob(at_time: Optional[datetime] = None) -> float:
    """
    Computa el IOB de insulina basal en `at_time`.

    Prioridad 1 — Registros reales de la DB (InsulinDose type='basal'):
        Si el usuario registra su inyección basal diariamente (como hace con
        el bolo), la función la encuentra aquí y calcula el IOB con precisión.
        El tipo de insulina (para saber el DIA) se lee de UserSettings.

    Prioridad 2 — Configuración manual (basal_dose_u + basal_hora):
        Fallback para usuarios que nunca han registrado una inyección basal.

    Modelo: decaimiento lineal peakless (sin pico — análogos de larga duración):
        iob_fraction(t) = max(0, 1 − elapsed_min / dia_min)
    """
    from helpers import _get_setting

    if at_time is None:
        at_time = datetime.now()

    tipo = (_get_setting("basal_tipo") or "glargina").lower().strip()
    dia  = _BASAL_DIA_MIN.get(tipo, _BASAL_DIA_DEFAULT)

    # ── Prioridad 1: inyecciones basales registradas en la DB ─────────────────
    try:
        from models import InsulinDose
        # Ventana: DIA + 2h de buffer para capturar la dosis más reciente
        cutoff = at_time - timedelta(minutes=dia + 120)
        doses  = InsulinDose.query.filter(
            InsulinDose.type      == "basal",
            InsulinDose.timestamp >= cutoff,
            InsulinDose.timestamp <= at_time,
        ).all()

        if doses:
            total = 0.0
            for d in doses:
                elapsed = (at_time - d.timestamp).total_seconds() / 60.0
                frac    = max(0.0, 1.0 - elapsed / dia)
                total  += d.units * frac
            return round(total, 2)
    except Exception:
        pass   # DB no disponible (tests, migraciones) → fallback

    # ── Prioridad 2: configuración manual (UserSettings) ─────────────────────
    dose_str = _get_setting("basal_dose_u")
    if not dose_str:
        return 0.0
    try:
        dose = float(dose_str)
    except (ValueError, TypeError):
        return 0.0
    if dose <= 0:
        return 0.0

    hora = int(_get_setting("basal_hora") or 22)
    inj  = at_time.replace(hour=hora, minute=0, second=0, microsecond=0)
    if inj > at_time:
        inj -= timedelta(days=1)

    elapsed = (at_time - inj).total_seconds() / 60.0
    frac    = max(0.0, 1.0 - elapsed / dia)
    return round(dose * frac, 2)


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
    """Linear fallback — kept for fat/protein extended absorption."""
    if elapsed_min <= 0:
        return 1.0
    if elapsed_min >= absorption_min:
        return 0.0
    return 1.0 - elapsed_min / absorption_min


def _ka_for_meal(meal) -> float:
    """
    Estimate gastric-emptying rate k_a (min⁻¹) for a Meal object.

    Higher GI → faster emptying → larger k_a.
    Fat and fiber slow gastric emptying (clinically established).
    Ref: Horowitz et al. (2002) Diabetes Care.
    """
    gi_values   = []
    fiber_total = 0.0
    fat_total   = getattr(meal, "fat_g", 0) or 0.0

    if hasattr(meal, "components") and meal.components:
        for comp in meal.components:
            if comp.glycemic_index:
                gi_values.append(float(comp.glycemic_index))
            fiber_total += float(getattr(comp, "fiber_g", 0) or 0)

    avg_gi = sum(gi_values) / len(gi_values) if gi_values else None

    if avg_gi is None:
        k_a_base = _K_A_DEF
    elif avg_gi > 70:
        k_a_base = _K_A_HIGH
    elif avg_gi >= 55:
        k_a_base = _K_A_MED
    else:
        k_a_base = _K_A_LOW

    # Fat slows gastric emptying: each 10g fat adds ~15 min to half-life
    fat_factor   = 1.0 / (1.0 + fat_total   * 0.015)
    # Fiber slows absorption: each 5g fiber adds ~12 min to half-life
    fiber_factor = 1.0 / (1.0 + fiber_total * 0.040)

    return k_a_base * fat_factor * fiber_factor


def _cob_fraction_2comp(elapsed_min: float, k_a: float,
                         k_g: float = _K_GUT) -> float:
    """
    2-compartment carbohydrate absorption model.

    Returns the fraction of ingested carbs still in the GI tract
    (stomach S + intestine I) at elapsed_min post-ingestion.

    Analytical solution (Dalla Man et al. 2007, simplified):
        S(t) = e^(-k_a * t)
        I(t) = k_a / (k_g - k_a) * (e^(-k_a*t) - e^(-k_g*t))
        COB_fraction = S(t) + I(t)

    Differences from linear model
    ─────────────────────────────
    • Pico de absorción más tardío (carbos llegan a sangre con demora)
    • Cola más larga (el intestino libera gradualmente)
    • Sensible al IG: alto IG → k_a grande → pico anterior y más agudo
    """
    if elapsed_min <= 0:
        return 1.0

    # Avoid numerical singularity when k_a ≈ k_g
    if abs(k_a - k_g) < 5e-4:
        k_a = k_a + 0.001

    S = math.exp(-k_a * elapsed_min)
    I = k_a / (k_g - k_a) * (math.exp(-k_a * elapsed_min) -
                               math.exp(-k_g * elapsed_min))
    return max(0.0, S + I)


def fat_protein_glucose_equiv(fat_g: float, protein_g: float) -> float:
    """
    Glucose-equivalent grams from fat and protein (Wolpert / Smart model).
    Returns the total glucose-equivalent contribution (g) from fat+protein.
    """
    return round(fat_g * _FAT_TO_GLUCOSE_FRAC + protein_g * _PROT_TO_GLUCOSE_FRAC, 1)


def extended_bolus_recommendation(fat_g: float, protein_g: float, icr: float) -> Optional[dict]:
    """
    If fat+protein is clinically significant, return a split-bolus recommendation.

    Returns dict or None:
        fp_glucose_equiv : float (g glucose equivalent)
        deferred_units   : float (U to give 2–3h later)
        deferred_at      : str  ("2–3 horas después de comer")
        trigger          : str  ("grasa" | "proteina" | "ambos")
    """
    if not icr or icr <= 0:
        return None
    fat_g   = fat_g   or 0
    protein_g = protein_g or 0
    fp_equiv = fat_protein_glucose_equiv(fat_g, protein_g)
    if fp_equiv < 5:
        return None

    trigger = (
        "ambos"    if fat_g > _FAT_THRESHOLD_G and protein_g > _PROT_THRESHOLD_G else
        "grasa"    if fat_g > _FAT_THRESHOLD_G else
        "proteina" if protein_g > _PROT_THRESHOLD_G else
        None
    )
    if trigger is None:
        return None

    deferred_units = round(fp_equiv / icr * 2) / 2  # round to 0.5U
    return {
        "fp_glucose_equiv": fp_equiv,
        "deferred_units":   deferred_units,
        "deferred_at":      "2–3 horas después de comer",
        "trigger":          trigger,
    }


def current_cob(
    meal_list,
    at_time: Optional[datetime] = None,
) -> float:
    """
    Compute total active carbohydrates (grams) at `at_time`.
    Includes only fast carbohydrate absorption (legacy compatible).
    For full fat+protein picture use current_cob_detailed().
    """
    return current_cob_detailed(meal_list, at_time)["carbs_cob"]


def current_cob_detailed(
    meal_list,
    at_time: Optional[datetime] = None,
) -> dict:
    """
    Full COB breakdown including fat+protein glucose equivalent.

    Returns
    -------
    dict:
        carbs_cob     : float  — fast carbs still absorbing (g)
        fat_cob       : float  — glucose equiv from fat still releasing (g)
        prot_cob      : float  — glucose equiv from protein still releasing (g)
        fp_cob        : float  — fat+protein combined (g)
        total_cob     : float  — carbs + fat/prot equiv (g)
        has_extended  : bool   — True if fp_cob > 2 g
        peak_fat_min  : int|None — minutes until fat glucose peak from now
        meals_detail  : list   — per-meal breakdown
    """
    if at_time is None:
        at_time = datetime.now()

    total_carbs = 0.0
    total_fat   = 0.0
    total_prot  = 0.0
    meals_detail = []

    for meal in meal_list:
        carbs   = getattr(meal, "carbs_g",   0) or 0
        fat_g   = getattr(meal, "fat_g",     0) or 0
        prot_g  = getattr(meal, "protein_g", 0) or 0

        elapsed_min = (at_time - meal.timestamp).total_seconds() / 60.0
        if elapsed_min < 0:
            continue

        # Fast carbs — modelo 2 compartimentos (gástrico → intestinal → sangre)
        carbs_cob = 0.0
        if carbs > 0:
            k_a       = _ka_for_meal(meal)
            carbs_cob = carbs * _cob_fraction_2comp(elapsed_min, k_a)

        # Fat glucose equivalent
        fat_equiv = fat_g * _FAT_TO_GLUCOSE_FRAC
        fat_cob   = fat_equiv * _cob_fraction_linear(elapsed_min, _FAT_ABSORPTION_MIN)

        # Protein glucose equivalent
        prot_equiv = prot_g * _PROT_TO_GLUCOSE_FRAC
        prot_cob   = prot_equiv * _cob_fraction_linear(elapsed_min, _PROT_ABSORPTION_MIN)

        total_carbs += carbs_cob
        total_fat   += fat_cob
        total_prot  += prot_cob

        if carbs_cob > 0.5 or fat_cob > 0.5 or prot_cob > 0.5:
            meals_detail.append({
                "name":       meal.name,
                "elapsed_h":  round(elapsed_min / 60, 1),
                "carbs_cob":  round(carbs_cob, 1),
                "fat_cob":    round(fat_cob,   1),
                "prot_cob":   round(prot_cob,  1),
            })

    fp_cob    = round(total_fat + total_prot, 1)
    carbs_cob = round(total_carbs, 1)

    return {
        "carbs_cob":    carbs_cob,
        "fat_cob":      round(total_fat,  1),
        "prot_cob":     round(total_prot, 1),
        "fp_cob":       fp_cob,
        "total_cob":    round(carbs_cob + fp_cob, 1),
        "has_extended": fp_cob >= 2.0,
        "meals_detail": meals_detail,
    }


# ── Dawn phenomenon (fenómeno del alba) ──────────────────────────────────────

def dawn_roc_mgdl_min(at_time: datetime | None = None) -> float:
    """
    Returns the dawn phenomenon glucose ROC component (mg/dL/min) at at_time.

    Physiological basis
    ───────────────────
    Cortisol and growth hormone secretion peaks 3–8am, stimulating hepatic
    glucose output. The effect is modelled as a Gaussian bell centred at
    05:30 local time. Magnitude is personalised from the user's CGM history
    (stored in UserSettings under 'dawn_magnitude'); falls back to a
    conservative default (0.45 mg/dL/min) if not yet estimated.

    References: Bolli et al. (1984) NEJM; Perriello et al. (1997) Diabetes.
    """
    if at_time is None:
        at_time = datetime.now()

    hour = at_time.hour + at_time.minute / 60.0
    if hour < _DAWN_H_START or hour > _DAWN_H_END:
        return 0.0

    try:
        from helpers import _get_setting
        mag_raw   = _get_setting(_DAWN_MAG_KEY)
        magnitude = float(mag_raw) if mag_raw else _DAWN_MAG_DEF
    except Exception:
        magnitude = _DAWN_MAG_DEF

    effect = magnitude * math.exp(
        -0.5 * ((hour - _DAWN_PEAK_H) / _DAWN_SIGMA_H) ** 2
    )
    return round(max(0.0, effect), 4)


def estimate_dawn_magnitude(days: int = 45) -> dict:
    """
    Estimate the user's personal dawn phenomenon magnitude from CGM history.

    Method
    ──────
    1. Compute per-interval ROC for all readings in the last `days` days.
    2. Separate into two cohorts:
       • dawn_window (3–8am): dawn ROC expected
       • baseline   (0–3am): no dawn effect, used as overnight reference
    3. magnitude = max(0, mean_dawn_ROC − mean_baseline_ROC)
    4. Persist result in UserSettings['dawn_magnitude'].

    Requires ≥ 20 intervals in each cohort for a reliable estimate.
    """
    from models import GlucoseReading
    from helpers import _set_setting

    cutoff   = datetime.now() - timedelta(days=days)
    readings = (
        GlucoseReading.query
        .filter(GlucoseReading.timestamp >= cutoff,
                GlucoseReading.value_mgdl > 20)
        .order_by(GlucoseReading.timestamp)
        .all()
    )

    if len(readings) < 50:
        return {"ok": False, "error": "Menos de 50 lecturas disponibles"}

    roc_dawn, roc_baseline = [], []

    for i in range(1, len(readings)):
        r1, r2   = readings[i - 1], readings[i]
        dt_min   = (r2.timestamp - r1.timestamp).total_seconds() / 60.0
        if dt_min <= 0 or dt_min > 25:   # ignorar brechas (cambio de sensor, etc.)
            continue
        roc  = (r2.value_mgdl - r1.value_mgdl) / dt_min
        hour = r1.timestamp.hour + r1.timestamp.minute / 60.0

        if _DAWN_H_START <= hour <= _DAWN_H_END:
            roc_dawn.append(roc)
        elif 0.0 <= hour < _DAWN_H_START:
            roc_baseline.append(roc)

    if len(roc_dawn) < 20 or len(roc_baseline) < 10:
        return {
            "ok":    False,
            "error": f"Datos nocturnos insuficientes (alba: {len(roc_dawn)}, baseline: {len(roc_baseline)})",
        }

    avg_dawn     = sum(roc_dawn)     / len(roc_dawn)
    avg_baseline = sum(roc_baseline) / len(roc_baseline)
    magnitude    = round(max(0.0, avg_dawn - avg_baseline), 4)

    _set_setting(_DAWN_MAG_KEY, magnitude)

    return {
        "ok":              True,
        "magnitude":       magnitude,
        "avg_dawn_roc":    round(avg_dawn,     4),
        "avg_baseline_roc": round(avg_baseline, 4),
        "n_dawn":          len(roc_dawn),
        "n_baseline":      len(roc_baseline),
        "days_used":       days,
        "estimated_at":    datetime.now().isoformat(),
    }


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
    from models import InsulinDose, Meal, GlucoseReading, Activity
    from helpers import _get_setting

    now = datetime.now()
    cutoff = now - timedelta(hours=hours_lookback)

    # Resolve DIA from settings or default
    if dia_min is None:
        saved_dia = _get_setting("dia_min")
        dia_min = int(float(saved_dia)) if saved_dia else _DEFAULT_DIA_MIN
    if peak_min is None:
        peak_min = _DEFAULT_PEAK_MIN

    # Load data
    boluses = InsulinDose.query.filter(
        InsulinDose.type == "bolus",
        InsulinDose.timestamp >= cutoff,
    ).all()

    roc_cutoff = now - timedelta(minutes=30)
    cgm_readings = GlucoseReading.query.filter(
        GlucoseReading.timestamp >= roc_cutoff,
    ).order_by(GlucoseReading.timestamp).all()

    # Meals lookback for fat+protein needs longer window (up to 8h)
    fat_cutoff = now - timedelta(hours=max(hours_lookback, 8))
    meals_extended = Meal.query.filter(Meal.timestamp >= fat_cutoff).all()

    # Activities: 24h lookback for exercise sensitivity
    act_cutoff = now - timedelta(hours=24)
    activities = Activity.query.filter(Activity.timestamp >= act_cutoff).all()

    # Compute
    iob_bolus   = current_iob(boluses, at_time=now, peak_min=peak_min, dia_min=dia_min)
    iob_basal   = current_basal_iob(at_time=now)
    iob         = round(iob_bolus + iob_basal, 2)
    cob_data    = current_cob_detailed(meals_extended, at_time=now)
    slope       = glucose_roc(cgm_readings, window_min=20)
    arrow       = roc_arrow(slope)
    ex_factor   = exercise_sensitivity_factor(activities, at_time=now)

    last_glucose = cgm_readings[-1].value_mgdl if cgm_readings else None

    # Describe exercise effect
    ex_label = None
    if ex_factor >= 1.10:
        ex_label = f"+{round((ex_factor - 1) * 100):.0f}% sensibilidad (ejercicio)"
    elif ex_factor <= 0.92:
        ex_label = f"−{round((1 - ex_factor) * 100):.0f}% sensibilidad (ejercicio agudo)"

    # Human-readable context
    parts = []
    if iob > 0.1:
        parts.append(f"IOB {iob:.1f} U")
    if cob_data["carbs_cob"] > 1:
        parts.append(f"COB {cob_data['carbs_cob']:.0f}g CH")
    if cob_data["fp_cob"] >= 2:
        parts.append(f"+{cob_data['fp_cob']:.0f}g grasa/prot")
    if ex_label:
        parts.append(ex_label)
    if slope is not None:
        parts.append(f"{arrow} {abs(slope * 10):.0f} mg/dL·10min")
    context = "  ·  ".join(parts) if parts else "Sin insulina ni carbohidratos activos"

    return {
        "iob":             iob,           # total (bolus + basal)
        "iob_bolus":       iob_bolus,
        "iob_basal":       iob_basal,
        "cob":             cob_data["carbs_cob"],
        "cob_detail":      cob_data,
        "roc":             slope,
        "arrow":           arrow,
        "last_glucose":    last_glucose,
        "context":         context,
        "dia_min":         dia_min,
        "peak_min":        peak_min,
        "exercise_factor": ex_factor,
        "exercise_label":  ex_label,
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
