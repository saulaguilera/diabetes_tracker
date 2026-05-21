"""
bench/tuning/protocol.py
─────────────────────────
Protocolo estándar de tuning inicial.

Responsabilidades
-----------------
  1. Sugerir ranges razonables por parámetro (basado en literatura + defaults)
  2. Validar que una combinación sea físicamente plausible
  3. Estimar runtime ANTES de correr el grid
  4. Generar specs baseline pre-configuradas (baseline_v1, q_focus, r_focus...)

Esto sustituye el "tuning manual ad-hoc" por un workflow predecible y
reproducible. El protocolo NO decide qué tunear, solo provee infraestructura.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Optional

from pmm.ssm.parameters import SSMParameters


# ── Ranges sugeridos por parámetro ──────────────────────────────────────
# Cada entry: (lo, hi, n_values_default, scale)
#   scale: 'linear' | 'log' | 'physiological'
# Estos ranges salen de:
#   - Hovorka 2004 (UVA/Padova)
#   - Bergman 1981 (minimal model insulin sensitivity)
#   - Calibración empírica vs el modelo biexponencial actual

SUGGESTED_RANGES: dict[str, dict] = {
    # Process noise
    "Q_G":       {"lo": 0.5,    "hi": 12.0,   "n": 4, "scale": "log",   "default_sweep": [1, 2, 4, 8]},
    "Q_IOB":     {"lo": 1e-4,   "hi": 1e-2,   "n": 4, "scale": "log",   "default_sweep": [1e-4, 1e-3, 1e-2]},
    "Q_IOB_EFF": {"lo": 1e-4,   "hi": 1e-2,   "n": 4, "scale": "log",   "default_sweep": [1e-4, 1e-3, 1e-2]},
    "Q_COB1":    {"lo": 0.01,   "hi": 1.0,    "n": 4, "scale": "log",   "default_sweep": [0.01, 0.05, 0.1, 0.5]},
    "Q_COB2":    {"lo": 0.01,   "hi": 1.0,    "n": 4, "scale": "log",   "default_sweep": [0.01, 0.05, 0.1, 0.5]},
    "Q_SI":      {"lo": 1e-9,   "hi": 1e-4,   "n": 5, "scale": "log",   "default_sweep": [1e-8, 1e-7, 1e-6, 1e-5]},

    # Observation noise
    "R_CGM_BASE": {"lo": 2.0,   "hi": 12.0,   "n": 4, "scale": "linear","default_sweep": [3, 4, 6, 8]},
    "R_CGM_MARD": {"lo": 0.04,  "hi": 0.15,   "n": 4, "scale": "linear","default_sweep": [0.06, 0.09, 0.12]},

    # Physiological dynamics
    "K_PI":      {"lo": 0.010,  "hi": 0.060,  "n": 4, "scale": "linear","default_sweep": [0.015, 0.025, 0.035, 0.045]},
    "K_IE":      {"lo": 0.010,  "hi": 0.040,  "n": 4, "scale": "linear","default_sweep": [0.012, 0.018, 0.025, 0.035]},
    "K_ACT":     {"lo": 0.005,  "hi": 0.040,  "n": 4, "scale": "linear","default_sweep": [0.0083, 0.0167, 0.0333]},
    "K_A_MED":   {"lo": 0.010,  "hi": 0.050,  "n": 4, "scale": "linear","default_sweep": [0.015, 0.020, 0.025, 0.035]},
    "K_G":       {"lo": 0.020,  "hi": 0.080,  "n": 4, "scale": "linear","default_sweep": [0.025, 0.035, 0.045, 0.060]},
    "LAMBDA_SI": {"lo": 1/(30*24*60), "hi": 1/(1*24*60), "n": 4,
                  "scale": "physiological", "default_sweep": [1/(14*24*60),
                  1/(7*24*60), 1/(3*24*60), 1/(1*24*60)]},
    "EGP_BASAL": {"lo": 0.2,    "hi": 1.5,    "n": 4, "scale": "linear","default_sweep": [0.35, 0.55, 0.80]},

    # Regularization
    "PSD_JITTER":    {"lo": 1e-8, "hi": 1e-4, "n": 4, "scale": "log",   "default_sweep": [1e-7, 1e-6, 1e-5]},
    "INFLATION":     {"lo": 0.8,  "hi": 3.0,  "n": 4, "scale": "linear","default_sweep": [1.0, 1.25, 1.5, 2.0]},
    "SIGMA_FLOOR_G": {"lo": 0.5,  "hi": 5.0,  "n": 4, "scale": "linear","default_sweep": [0.5, 1.0, 2.0]},

    # Initial uncertainty
    "G_SIGMA_INIT":   {"lo": 3,  "hi": 25,  "n": 3, "scale": "linear","default_sweep": [5, 10, 15]},
    "S_I_SIGMA_INIT": {"lo": 5,  "hi": 25,  "n": 3, "scale": "linear","default_sweep": [8, 12, 18]},

    # UKF
    "UKF_ALPHA": {"lo": 1e-4, "hi": 1e-1, "n": 4, "scale": "log",       "default_sweep": [1e-3, 1e-2]},
}


def suggest_range(param_name: str) -> Optional[list]:
    """Lista de valores sugeridos para barrer un parámetro."""
    info = SUGGESTED_RANGES.get(param_name)
    return list(info["default_sweep"]) if info else None


# ── Validación de combinaciones físicamente absurdas ────────────────────

def validate_combination(params: SSMParameters) -> list[str]:
    """
    Detecta combinaciones físicamente implausibles ANTES de gastar runtime.
    Retorna lista de warnings (vacía = OK).

    Reglas:
      - Q_G alto + R_CGM_BASE alto → filter no converge (todo es ruido)
      - K_PI <= K_IE → no hay transporte (insulin se queda en plasma)
      - LAMBDA_SI muy alto → S_I revierte tan rápido que no aprende
      - INFLATION > 2 + Q_G alto → covariance explosion garantizada
      - SIGMA_FLOOR_G > G_SIGMA_INIT → estado inicial inconsistente
    """
    problems = []

    # 1. Filter no convergente
    if params.Q_G > 8 and params.R_CGM_BASE > 8:
        problems.append(
            f"Q_G ({params.Q_G}) + R_CGM_BASE ({params.R_CGM_BASE}) ambos altos: "
            "filter no extraerá señal de la observación"
        )

    # 2. Insulin transport degenerado
    if params.K_PI <= params.K_IE * 0.6:
        problems.append(
            f"K_PI ({params.K_PI}) <= 0.6×K_IE ({params.K_IE}): "
            "insulin se acumula en plasma sin transferir"
        )

    # 3. S_I reversion demasiado rápida
    half_life_days = (1.0 / params.LAMBDA_SI) / (60 * 24 * 0.693)
    if half_life_days < 0.5:
        problems.append(
            f"LAMBDA_SI half-life {half_life_days:.2f}d < 0.5d: S_I no aprende"
        )

    # 4. Covariance explosion
    if params.INFLATION > 2.0 and params.Q_G > 6:
        problems.append(
            f"INFLATION ({params.INFLATION}) × Q_G ({params.Q_G}) alto: "
            "riesgo de covariance explosion"
        )

    # 5. Init inconsistencia
    if params.SIGMA_FLOOR_G > params.G_SIGMA_INIT:
        problems.append(
            f"SIGMA_FLOOR_G ({params.SIGMA_FLOOR_G}) > G_SIGMA_INIT ({params.G_SIGMA_INIT})"
        )

    # 6. UKF instability
    if params.UKF_ALPHA > 0.5:
        problems.append(f"UKF_ALPHA ({params.UKF_ALPHA}) > 0.5: sigma points dispersos")

    return problems


# ── Runtime estimation ──────────────────────────────────────────────────

# Constantes calibradas empíricamente (M1 MacBook, datos reales pequeños).
# Ajustar tras observar runtimes reales en Railway.
_RUNTIME_PER_DECISION_MS = 12.0   # ~ 12ms por punto de decisión (UKF 13 sigma)
_RUNTIME_BASELINE_MS     = 80.0   # overhead constante por replay (load eventos, etc.)


def estimate_runtime(
    n_combos:           int,
    days:               int,
    decision_every_min: int = 30,
) -> dict:
    """
    Estima runtime total del grid antes de correrlo.

    Returns
    -------
    {
        "n_combos":            int,
        "decisions_per_combo": int,
        "ms_per_combo":        int,
        "total_seconds":       float,
        "total_str":           "1h 23m 12s",
    }
    """
    decisions = max(1, (days * 24 * 60) // decision_every_min)
    ms_per    = _RUNTIME_BASELINE_MS + decisions * _RUNTIME_PER_DECISION_MS
    total_s   = (n_combos * ms_per) / 1000.0

    h = int(total_s // 3600)
    m = int((total_s % 3600) // 60)
    s = int(total_s % 60)
    parts = []
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    parts.append(f"{s}s")

    return {
        "n_combos":            n_combos,
        "decisions_per_combo": decisions,
        "ms_per_combo":        int(ms_per),
        "total_seconds":       round(total_s, 1),
        "total_str":           " ".join(parts),
    }


# ── Specs pre-configuradas (baseline protocols) ─────────────────────────

@dataclass
class BaselineProtocol:
    """Spec pre-configurado, listo para `run_experiment(protocol.as_spec())`."""
    name:               str
    param_grid:         dict[str, list]
    days:               int
    decision_every_min: int
    rationale:          str

    def as_spec(self):
        from bench.tuning.grid_search import ExperimentSpec
        return ExperimentSpec(
            name=self.name,
            param_grid=self.param_grid,
            days=self.days,
            decision_every_min=self.decision_every_min,
        )

    def estimated_runtime(self) -> dict:
        n = 1
        for v in self.param_grid.values():
            n *= len(v)
        return estimate_runtime(n, self.days, self.decision_every_min)


PROTOCOLS: dict[str, BaselineProtocol] = {
    "baseline_v1": BaselineProtocol(
        name="baseline_v1",
        param_grid={
            "Q_G":         [1.0, 2.0, 4.0, 8.0],
            "R_CGM_BASE":  [3.0, 4.0, 6.0],
            "Q_SI":        [1e-7, 1e-6, 1e-5],
        },
        days=3,
        decision_every_min=30,
        rationale=(
            "Sweep amplio sobre los 3 params más sensibles del filter: "
            "process noise glucosa, ruido CGM base, y noise del estado S_I. "
            "Identifica el corner del espacio donde se concentra el mejor score."
        ),
    ),
    "q_focus_v1": BaselineProtocol(
        name="q_focus_v1",
        param_grid={
            "Q_G":       [1.0, 2.0, 3.0, 4.0, 6.0, 8.0],
            "INFLATION": [1.0, 1.25, 1.5],
        },
        days=5,
        decision_every_min=20,
        rationale=(
            "Refinamiento del process noise glucosa. Usar tras baseline_v1 "
            "cuando ya se identificó la magnitud aproximada de Q_G óptimo."
        ),
    ),
    "r_noise_v1": BaselineProtocol(
        name="r_noise_v1",
        param_grid={
            "R_CGM_BASE": [2.0, 3.0, 4.0, 5.0, 6.0, 8.0],
            "R_CGM_MARD": [0.06, 0.09, 0.12],
        },
        days=5,
        decision_every_min=20,
        rationale=(
            "Calibrar el modelo de ruido del sensor — afecta directamente "
            "IC50/IC90 coverage. Hacer después de q_focus."
        ),
    ),
    "si_dynamics_v1": BaselineProtocol(
        name="si_dynamics_v1",
        param_grid={
            "LAMBDA_SI": [1/(14*24*60), 1/(7*24*60), 1/(5*24*60), 1/(3*24*60)],
            "Q_SI":      [1e-8, 1e-7, 1e-6, 1e-5],
        },
        days=7,
        decision_every_min=30,
        rationale=(
            "Calibrar la dinámica del S_I latente. LAMBDA controla mean-"
            "reversion (cuán rápido vuelve al prior); Q_SI controla random walk."
        ),
    ),
    "insulin_pk_v1": BaselineProtocol(
        name="insulin_pk_v1",
        param_grid={
            "K_PI":  [0.015, 0.020, 0.025, 0.035, 0.045],
            "K_IE":  [0.012, 0.018, 0.025, 0.032],
            "K_ACT": [0.0125, 0.0167, 0.025],
        },
        days=5,
        decision_every_min=30,
        rationale=(
            "Calibrar farmacocinética de insulina (transporte plasma→intersticio "
            "y acción). Afecta predominantemente post-bolus."
        ),
    ),
    "carb_absorption_v1": BaselineProtocol(
        name="carb_absorption_v1",
        param_grid={
            "K_A_MED": [0.015, 0.025, 0.035],
            "K_G":     [0.030, 0.040, 0.050, 0.060],
            "Q_COB1":  [0.05, 0.1, 0.3],
            "Q_COB2":  [0.05, 0.1, 0.3],
        },
        days=7,
        decision_every_min=30,
        rationale=(
            "Calibrar absorción de carbohidratos. Afecta predominantemente "
            "post-meal regime. Esperar mejoras en regime breakdown."
        ),
    ),
}


def list_protocols() -> list[dict]:
    """Lista de todos los protocolos pre-configurados con su runtime estimado."""
    out = []
    for name, p in PROTOCOLS.items():
        rt = p.estimated_runtime()
        out.append({
            "name":           p.name,
            "rationale":      p.rationale,
            "param_grid":     {k: list(v) for k, v in p.param_grid.items()},
            "days":           p.days,
            "n_combos":       rt["n_combos"],
            "estimated_time": rt["total_str"],
        })
    return out
