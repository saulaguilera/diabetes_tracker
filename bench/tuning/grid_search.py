"""
bench/tuning/grid_search.py
────────────────────────────
Structured grid search sobre el espacio de SSMParameters.

Diseño
------
- Una `ExperimentSpec` define qué hyperparámetros barrer y con qué valores
- `run_experiment()` enumera el producto cartesiano, evalúa cada combo via
  replay determinístico, computa promotion_score, persiste en DB
- Idempotente: si un (name, param_hash) ya existe, no se re-evalúa (skip)
- Tolerante a fallos: una combo que crashea se loguea con error y sigue

Uso
---
    spec = ExperimentSpec(
        name="q_si_sweep",
        param_grid={
            "Q_SI":   [1e-7, 5e-7, 1e-6, 5e-6],
            "LAMBDA_SI": [1/(3*24*60), 1/(7*24*60), 1/(14*24*60)],
        },
        days=14,
        decision_every_min=30,
    )
    results = run_experiment(spec)
"""
from __future__ import annotations

import itertools
import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from pmm.ssm.parameters import SSMParameters

logger = logging.getLogger("bench.tuning")


# ── Spec ────────────────────────────────────────────────────────────────

@dataclass
class ExperimentSpec:
    """
    Especificación declarativa de un experimento.

    name        : identificador (e.g. "q_si_sweep_v1")
    param_grid  : dict {param_name: [values to try]}
    days        : ventana histórica
    decision_every_min : frecuencia de "predicciones" en el replay
    base_params : SSMParameters base sobre la que aplicar overrides
    skip_existing : si True, omite combos ya evaluadas
    """
    name:               str
    param_grid:         dict[str, list]
    days:               int = 14
    decision_every_min: int = 30
    base_params:        Optional[SSMParameters] = None
    skip_existing:      bool = True

    def total_combos(self) -> int:
        n = 1
        for vals in self.param_grid.values():
            n *= len(vals)
        return n


# ── Resultado de un solo experiment run ─────────────────────────────────

@dataclass
class ExperimentResult:
    """Resultado de evaluar UN config dentro del grid."""
    name:            str
    params:          SSMParameters
    param_hash:      str
    n_records:       int = 0
    duration_ms:     int = 0
    score_breakdown: Optional[dict] = None     # composite + sub-scores
    metrics:         dict = field(default_factory=dict)
    verdict:         str  = ""
    note:            str  = ""
    error:           Optional[str] = None

    def to_db_row(self) -> dict:
        sb = self.score_breakdown or {}
        return dict(
            name=self.name,
            param_hash=self.param_hash,
            params_json=self.params.to_json(),
            days_window=self.metrics.get("days_window"),
            n_records=self.n_records,
            git_commit=_get_git_commit(),
            score_calibration = sb.get("calibration"),
            score_innovation  = sb.get("innovation"),
            score_clinical    = sb.get("clinical"),
            score_stability   = sb.get("stability"),
            score_accuracy    = sb.get("accuracy"),
            score_composite   = sb.get("composite"),
            metrics_json      = json.dumps(self.metrics, default=float),
            verdict           = self.verdict,
            note              = self.note,
            error             = self.error,
            duration_ms       = self.duration_ms,
        )


# ── Runner principal ────────────────────────────────────────────────────

def run_experiment(spec: ExperimentSpec) -> list[ExperimentResult]:
    """
    Enumera el grid, evalúa cada combo via replay, persiste en DB.
    Devuelve lista de ExperimentResult (incluso para combos que fallaron).
    """
    from models import db, TuningExperiment
    from bench.replay_ssm import replay_window, ReplayConfig
    from bench.metrics.accuracy    import accuracy_summary, accuracy_by_horizon
    from bench.metrics.calibration import calibration_summary
    from bench.tuning.promotion_score import (
        compute_promotion_score, flatten_metrics_for_score,
    )

    base = spec.base_params or SSMParameters()
    combos = list(_iter_grid(spec.param_grid))
    logger.info(f"Grid search '{spec.name}': {len(combos)} combos × {spec.days}d window")

    results = []
    for i, override_dict in enumerate(combos):
        params = base.override(**override_dict)
        ph     = params.fingerprint()

        # Skip si ya existe
        if spec.skip_existing:
            existing = (TuningExperiment.query
                        .filter_by(name=spec.name, param_hash=ph).first())
            if existing:
                logger.debug(f"  [{i+1}/{len(combos)}] skip existing {ph} — {override_dict}")
                continue

        logger.info(f"  [{i+1}/{len(combos)}] evaluating {ph}: {override_dict}")
        t0 = time.time()
        try:
            res = _evaluate_single(params, spec, override_dict)
            res.name = spec.name
        except Exception as exc:
            res = ExperimentResult(
                name=spec.name, params=params, param_hash=ph,
                error=str(exc),
            )
            logger.exception(f"  combo {ph} falló")

        res.duration_ms = int((time.time() - t0) * 1000)

        # Persistir
        try:
            row = TuningExperiment(**res.to_db_row())
            db.session.add(row)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.error(f"persistir falló: {exc}")
        results.append(res)
    return results


def _evaluate_single(params: SSMParameters, spec: ExperimentSpec,
                     overrides: dict) -> ExperimentResult:
    """
    Re-corre el SSM con los `params` sobre la ventana histórica y computa
    todas las métricas necesarias para promotion score.
    """
    from bench.replay_ssm import replay_window, ReplayConfig
    from bench.metrics.accuracy    import accuracy_summary, accuracy_by_horizon
    from bench.metrics.calibration import calibration_summary
    from bench.metrics.clinical    import clinical_summary
    from bench.tuning.promotion_score import compute_promotion_score

    # 1. Replay
    cfg = ReplayConfig(
        lookback_hours = params.LOOKBACK_HOURS,
    )
    # Inyectamos params explícitamente al replay_window vía monkey patch
    # mínimo: por ahora, replay_window no acepta SSMParameters directamente,
    # así que evaluamos invocando run_filter con params dentro de cada step.
    records = _replay_with_params(
        days=spec.days,
        decision_every_min=spec.decision_every_min,
        params=params,
        cfg=cfg,
    )

    if not records:
        return ExperimentResult(
            params=params, param_hash=params.fingerprint(), name=spec.name,
            n_records=0, error="sin records — ventana sin datos suficientes",
            note=f"overrides={overrides}",
        )

    # 2. Métricas básicas
    acc   = accuracy_summary(records)
    acc_h = accuracy_by_horizon(records)
    cal   = calibration_summary(records)
    clin  = clinical_summary(records)

    # 3. Flatten + promotion score (usamos los records directamente porque
    # tienen sigma; no necesitamos PredictionAudit para esta evaluación)
    flat = {
        "mae_30":               acc_h.get("+30min", {}).get("mae"),
        "mae_60":               acc_h.get("+60min", {}).get("mae"),
        "ic50_coverage":        _ic_coverage(records, 0.6745),
        "ic90_coverage":        _ic_coverage(records, 1.6449),
        "mce":                  cal.get("ece"),    # MCE ≈ ECE en este context
        "ece":                  cal.get("ece"),
        "mean_z":               _mean_innov_z(records),
        "var_z":                _var_innov_z(records),
        "lb_pvalue":            None,              # requiere innovations granulares
        "hypo_recall_30":       clin.get("hypo_+30min", {}).get("recall"),
        "false_alarms_per_day": clin.get("hypo_+30min", {}).get("false_alarm_rate_per_day"),
        "n_audits":             len(records),
        "n_non_psd":            0,                 # replay no captura PSD events
        "n_explosion":          0,
        "n_collapse":           0,
        "n_high_kappa":         0,
    }
    pscore = compute_promotion_score(flat)
    sb = pscore.to_dict()

    # Verdict heurístico breve
    if pscore.composite >= 0.70:
        verdict = "excelente"
    elif pscore.composite >= 0.55:
        verdict = "aceptable"
    elif pscore.composite >= 0.40:
        verdict = "marginal"
    else:
        verdict = "rechazado"

    metrics_full = {
        "days_window":   spec.days,
        "n_records":     len(records),
        "accuracy":      acc,
        "by_horizon":    acc_h,
        "calibration":   cal,
        "clinical":      clin,
        "flat":          flat,
        "overrides":     overrides,
    }
    return ExperimentResult(
        params=params, param_hash=params.fingerprint(), name=spec.name,
        n_records=len(records),
        score_breakdown=sb,
        metrics=metrics_full,
        verdict=verdict,
        note=f"overrides={overrides}",
    )


# ── Helpers ────────────────────────────────────────────────────────────

def _iter_grid(param_grid: dict[str, list]) -> "list[dict]":
    """Producto cartesiano enumerado a dicts {param: value}."""
    if not param_grid:
        return [{}]
    keys = list(param_grid.keys())
    combos = []
    for vals in itertools.product(*[param_grid[k] for k in keys]):
        combos.append(dict(zip(keys, vals)))
    return combos


def _replay_with_params(days: int, decision_every_min: int,
                        params: SSMParameters, cfg=None) -> list:
    """
    Replay determinístico inyectando params. Similar a bench.replay_ssm
    pero con params propagated end-to-end.
    """
    from datetime import datetime, timedelta
    from models import GlucoseReading
    from pmm.ssm.filter import run_filter, forward_predict
    from bench.replay import PredictionRecord

    now    = datetime.now()
    cutoff = now - timedelta(days=days)
    end    = now - timedelta(minutes=60)

    # Determinar puntos de decisión
    dts = []
    t = cutoff
    while t <= end:
        dts.append(t); t += timedelta(minutes=decision_every_min)

    # Pre-load CGM
    cgm = (GlucoseReading.query
           .filter(GlucoseReading.timestamp >= cutoff,
                   GlucoseReading.timestamp <= now)
           .order_by(GlucoseReading.timestamp).all())

    records = []
    for dt in dts:
        try:
            result = run_filter(now=dt, params=params)
            if result.error or result.n_cgm_used < 3:
                continue
            preds = forward_predict(result, horizons_min=(30, 60), params=params)
        except Exception:
            continue
        g_actual = _nearest_cgm(cgm, dt, tol_min=10)
        if g_actual is None: continue
        for h in (30, 60):
            from datetime import timedelta as _td
            g_real = _nearest_cgm(cgm, dt + _td(minutes=h), tol_min=7)
            if g_real is None: continue
            p = preds[h]
            records.append(PredictionRecord(
                predicted_at=dt, horizon_min=h,
                g_actual=float(g_actual),
                g_pred=float(p.g_pred), g_real=float(g_real),
                sigma=float(p.sigma),
                model_version=f"ssm_tune_{params.fingerprint()}",
            ))
    return records


def _nearest_cgm(cgm_list, t, tol_min: float = 5):
    best, best_dt = None, float("inf")
    for r in cgm_list:
        dt = abs((r.timestamp - t).total_seconds() / 60.0)
        if dt < best_dt and dt <= tol_min:
            best, best_dt = r, dt
    return best.value_mgdl if best else None


def _ic_coverage(records, z: float) -> Optional[float]:
    """% real dentro de μ ± z·σ."""
    valid = [r for r in records if r.sigma and r.sigma > 0]
    if not valid: return None
    n_in = sum(1 for r in valid if abs(r.error) <= z * r.sigma)
    return round(n_in / len(valid), 4)


def _mean_innov_z(records) -> Optional[float]:
    zs = [r.error / r.sigma for r in records if r.sigma and r.sigma > 0]
    if not zs: return None
    return round(sum(zs) / len(zs), 4)


def _var_innov_z(records) -> Optional[float]:
    zs = [r.error / r.sigma for r in records if r.sigma and r.sigma > 0]
    if len(zs) < 2: return None
    m = sum(zs) / len(zs)
    return round(sum((z - m) ** 2 for z in zs) / (len(zs) - 1), 4)


def _get_git_commit() -> str:
    """SHA del HEAD actual. Útil para reproducibilidad."""
    try:
        result = subprocess.run(["git", "rev-parse", "--short=10", "HEAD"],
                                capture_output=True, text=True, timeout=2)
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


# ── Reporting ──────────────────────────────────────────────────────────

def load_experiment_results(name: str) -> list[dict]:
    """Carga todos los results persistidos para un experiment name."""
    from models import TuningExperiment
    rows = (TuningExperiment.query
            .filter_by(name=name)
            .order_by(TuningExperiment.score_composite.desc())
            .all())
    out = []
    for r in rows:
        out.append({
            "param_hash":   r.param_hash,
            "params":       json.loads(r.params_json) if r.params_json else {},
            "metrics":      json.loads(r.metrics_json) if r.metrics_json else {},
            "scores": {
                "calibration": r.score_calibration,
                "innovation":  r.score_innovation,
                "clinical":    r.score_clinical,
                "stability":   r.score_stability,
                "accuracy":    r.score_accuracy,
                "composite":   r.score_composite,
            },
            "verdict":      r.verdict,
            "n_records":    r.n_records,
            "duration_ms":  r.duration_ms,
            "error":        r.error,
            "created_at":   r.created_at.isoformat() if r.created_at else None,
            "git_commit":   r.git_commit,
        })
    return out


def best_configs(name: str, top_k: int = 5) -> list[dict]:
    """Top-K configs por composite score."""
    return load_experiment_results(name)[:top_k]


def experiment_summary(name: str) -> dict:
    """Resumen estadístico del experiment + recomendaciones."""
    results = load_experiment_results(name)
    if not results:
        return {"name": name, "n_runs": 0}
    valid = [r for r in results if r.get("scores", {}).get("composite") is not None]
    if not valid:
        return {"name": name, "n_runs": len(results), "n_valid": 0}

    composites = [r["scores"]["composite"] for r in valid]
    best = valid[0]
    return {
        "name":           name,
        "n_runs":         len(results),
        "n_valid":        len(valid),
        "best":           best,
        "score_range":    [round(min(composites), 4), round(max(composites), 4)],
        "best_composite": best["scores"]["composite"],
        "best_params":    best["params"],
        "best_hash":      best["param_hash"],
    }
