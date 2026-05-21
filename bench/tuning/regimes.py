"""
bench/tuning/regimes.py
────────────────────────
Regime-specific failure analysis.

Separa predicciones e innovations por contexto fisiológico:

  - fasting     : sin meals en ±4h
  - post_meal   : meal en últimas 4h
  - overnight   : hour ∈ [0, 6] sin meal/exercise
  - exercise    : activity en ±2h
  - high_IOB    : IOB > 2.0 U
  - rapid_ROC   : |ROC| > 1.5 mg/dL/min

Para cada régimen computa:
  - n samples
  - MAE / RMSE / bias
  - IC50 / IC90 coverage
  - mean_z, var_z, kurt
  - verdict accionable

Output útil para sweep refinement:
  "calibration buena en fasting pero mala en post_meal → ajustar K_A_MED"
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional


REGIMES = ("fasting", "post_meal", "overnight", "exercise", "high_IOB", "rapid_ROC")


# ── Regime classifier (con DB) ──────────────────────────────────────────

def _classify_regime(ts: datetime, iob: Optional[float], roc: Optional[float],
                     meals_index: list, acts_index: list) -> str:
    """
    Clasifica un timestamp en exactamente UN régimen.
    Prioridad: exercise > rapid_ROC > high_IOB > post_meal > overnight > fasting
    """
    # Exercise — activity reciente
    for a_ts, a_dur in acts_index:
        # Activity influence ventana = duration + 90min
        dt_min = (ts - a_ts).total_seconds() / 60.0
        if 0 <= dt_min <= (a_dur or 30) + 90:
            return "exercise"

    # rapid_ROC
    if roc is not None and abs(roc) > 1.5:
        return "rapid_ROC"

    # high_IOB
    if iob is not None and iob > 2.0:
        return "high_IOB"

    # post_meal — comida en últimas 4h
    for m_ts, _carbs in meals_index:
        dt = (ts - m_ts).total_seconds() / 60.0
        if 0 <= dt <= 240:
            return "post_meal"

    # overnight (sin meal ni exercise)
    if 0 <= ts.hour < 6:
        return "overnight"

    return "fasting"


def _load_event_indices(t_min: datetime, t_max: datetime) -> tuple:
    """Pre-load ordered lists de meals y activities en la ventana."""
    from models import Meal, Activity
    meals = (Meal.query
             .filter(Meal.timestamp >= t_min - timedelta(hours=4),
                     Meal.timestamp <= t_max)
             .order_by(Meal.timestamp).all())
    acts = (Activity.query
            .filter(Activity.timestamp >= t_min - timedelta(hours=2),
                    Activity.timestamp <= t_max)
            .order_by(Activity.timestamp).all())
    return ([(m.timestamp, m.carbs_g) for m in meals],
            [(a.timestamp, a.duration_min) for a in acts])


# ── Por-régimen métricas ────────────────────────────────────────────────

def _stats_for_records(records: list) -> dict:
    """Computa MAE/RMSE/bias/IC sobre lista de PredictionAudits."""
    if not records:
        return {"n": 0}
    errs    = [r.realized_glucose - r.mu for r in records
               if r.realized_glucose is not None and r.mu is not None]
    if not errs:
        return {"n": len(records), "note": "sin resoluciones"}
    abs_e   = [abs(e) for e in errs]
    sq      = [e * e for e in errs]
    n       = len(errs)

    n_in_50 = sum(1 for r in records if r.inside_ic50)
    n_in_90 = sum(1 for r in records if r.inside_ic90)

    zs = [r.innovation_z for r in records if r.innovation_z is not None]
    if len(zs) >= 2:
        mz = sum(zs) / len(zs)
        vz = sum((z - mz) ** 2 for z in zs) / (len(zs) - 1)
        m4 = sum((z - mz) ** 4 for z in zs) / len(zs)
        kurt = (m4 / max(1e-12, vz ** 2)) - 3.0 if vz > 0 else 0.0
    else:
        mz = vz = kurt = None

    out = {
        "n":              n,
        "mae":            round(sum(abs_e) / n, 2),
        "rmse":           round(math.sqrt(sum(sq) / n), 2),
        "bias":           round(sum(errs) / n, 2),
        "ic50_coverage":  round(n_in_50 / n, 3),
        "ic90_coverage":  round(n_in_90 / n, 3),
        "mean_z":         round(mz, 3) if mz is not None else None,
        "var_z":          round(vz, 3) if vz is not None else None,
        "kurt":           round(kurt, 3) if kurt is not None else None,
        "verdict":        _regime_verdict(mz, vz, n_in_50/n, n_in_90/n),
    }
    return out


def _regime_verdict(mean_z, var_z, ic50, ic90) -> str:
    issues = []
    if mean_z is not None and abs(mean_z) > 0.3:
        d = "subestima" if mean_z > 0 else "sobreestima"
        issues.append(f"bias {d}")
    if var_z is not None:
        if var_z > 1.5:   issues.append("sub-disperso")
        elif var_z < 0.5: issues.append("sobre-disperso")
    if ic90 < 0.80:       issues.append("IC90 narrow")
    elif ic90 > 0.97:     issues.append("IC90 wide")
    if not issues:
        return "OK"
    return " · ".join(issues)


# ── Main entry point ──────────────────────────────────────────────────

def evaluate_regimes(
    days:           int = 14,
    model_version:  str = "ssm_v0_ukf6",
    horizon_min:    int = 30,
) -> dict:
    """
    Carga PredictionAudits del periodo y los segmenta por régimen.
    Computa estadísticas para cada uno.
    """
    from models import PredictionAudit

    cutoff = datetime.now() - timedelta(days=days)
    audits = (PredictionAudit.query
              .filter(PredictionAudit.predicted_at >= cutoff,
                      PredictionAudit.resolved == True,
                      PredictionAudit.horizon_min == horizon_min,
                      PredictionAudit.model_version == model_version)
              .order_by(PredictionAudit.predicted_at).all())
    if not audits:
        return {"n": 0, "note": "sin audits para este horizon"}

    t_min = min(a.predicted_at for a in audits)
    t_max = max(a.predicted_at for a in audits)
    meals_idx, acts_idx = _load_event_indices(t_min, t_max)

    # Para iob/roc usamos columnas auxiliares de PredictionAudit (NULL para
    # versiones antiguas) — fallback a 0
    by_regime: dict[str, list] = {r: [] for r in REGIMES}
    for a in audits:
        # iob / roc no están en PredictionAudit; lo aproximamos vía
        # PredictionAudit.note o fallback. Para v1 inferimos solo via meal/activity.
        regime = _classify_regime(a.predicted_at, iob=None, roc=None,
                                  meals_index=meals_idx, acts_index=acts_idx)
        by_regime[regime].append(a)

    out = {
        "horizon_min":    horizon_min,
        "model_version":  model_version,
        "days":           days,
        "n_total":        len(audits),
        "regimes":        {},
    }
    for regime in REGIMES:
        out["regimes"][regime] = _stats_for_records(by_regime[regime])
    out["worst_regime"] = _worst_regime(out["regimes"])
    return out


def _worst_regime(stats: dict) -> Optional[str]:
    """Identifica el régimen peor calibrado (mayor distancia a verdict OK)."""
    worst_name, worst_score = None, 0
    for name, st in stats.items():
        if st.get("n", 0) < 5: continue
        score = 0
        ic90 = st.get("ic90_coverage")
        if ic90 is not None:
            score += abs(ic90 - 0.90) * 100
        mz = st.get("mean_z")
        if mz is not None:
            score += abs(mz) * 50
        vz = st.get("var_z")
        if vz is not None:
            score += abs(vz - 1.0) * 50
        if score > worst_score:
            worst_score = score
            worst_name = name
    return worst_name


# ── Failure attribution por régimen ────────────────────────────────────

def regime_specific_diagnoses(regime_eval: dict) -> list[dict]:
    """
    Aplica diagnose() sobre cada régimen y agrega regime_name al output.
    Resultado: lista plana ordenada por confidence, con régimen anotado.
    """
    from bench.tuning.attribution import diagnose

    all_diag = []
    for regime, st in (regime_eval.get("regimes") or {}).items():
        if st.get("n", 0) < 5:
            continue
        metrics = {
            "ic50_coverage":  st.get("ic50_coverage"),
            "ic90_coverage":  st.get("ic90_coverage"),
            "abs_mean_z":     abs(st["mean_z"]) if st.get("mean_z") is not None else None,
            "mean_z":         st.get("mean_z"),
            "var_z":          st.get("var_z"),
            "kurt_excess":    st.get("kurt"),
            "mae_30":         st.get("mae"),
            "n_audits":       st.get("n"),
            # specific regime-level metrics que las reglas referencian
            f"regime_{regime}_mean": st.get("mean_z"),
            f"regime_{regime}_var":  st.get("var_z"),
        }
        diags = diagnose(metrics)
        for d in diags:
            d["regime"] = regime
            d["n_in_regime"] = st.get("n")
            all_diag.append(d)
    all_diag.sort(key=lambda d: -d["confidence"])
    return all_diag
