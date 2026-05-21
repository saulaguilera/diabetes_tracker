"""
bench/tuning/attribution.py
────────────────────────────
Rule-based failure attribution: cuando un experimento NO pasa gates,
genera hipótesis causales ranked + suggested next sweeps.

Filosofía
---------
NO es ML. Es un knowledge graph declarativo derivado de:
  - Teoría de Kalman filtering (Brown & Hwang 1997, Särkkä 2013)
  - Patología fisiológica del PK/PD modeling (Hovorka 2004)
  - Patrones empíricos observados (vamos a refinarlas con runs reales)

Cada Rule especifica:
  - cuándo dispara (condition sobre metrics)
  - hipótesis explicativa
  - parámetros implicados
  - sweep sugerido (delta direction)
  - confidence (función de cuán severa es la violación)

Output: lista de Diagnosis ordenada por confidence descendente.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


# ── Tipos ──────────────────────────────────────────────────────────────

@dataclass
class Rule:
    name:        str
    condition:   Callable[[dict], bool]
    hypothesis:  str
    params:      list[str]
    sweep_hint:  dict          # {param: 'increase' | 'decrease' | 'try [v1, v2]'}
    severity:    Callable[[dict], float] = field(default=lambda m: 0.5)
    category:    str = "general"


@dataclass
class Diagnosis:
    rule:        str
    hypothesis:  str
    confidence:  float          # ∈ [0, 1]
    params:      list[str]
    sweep_hint:  dict
    category:    str
    metric_snapshot: dict       # las métricas que dispararon la regla

    def to_dict(self) -> dict:
        return {
            "rule":           self.rule,
            "hypothesis":     self.hypothesis,
            "confidence":     round(self.confidence, 3),
            "params":         self.params,
            "sweep_hint":     self.sweep_hint,
            "category":       self.category,
            "metric_snapshot": self.metric_snapshot,
        }


# ── Reglas declarativas ────────────────────────────────────────────────

def _safe(m: dict, key: str, default=None):
    v = m.get(key)
    return default if v is None else v


RULES: list[Rule] = [

    # ─── CALIBRATION ────────────────────────────────────────────────

    Rule(
        name="ic90_narrow",
        condition=lambda m: _safe(m, "ic90_coverage", 1.0) < 0.80,
        hypothesis=("IC90 < 80% — sobre-confiado. σ predictivo demasiado chico. "
                    "Causas típicas: Q_G subestimado, R_CGM_BASE bajo, "
                    "INFLATION = 1 cuando dynamics no captura toda variabilidad."),
        params=["Q_G", "R_CGM_BASE", "INFLATION"],
        sweep_hint={"Q_G": "increase_2x", "R_CGM_BASE": "increase",
                    "INFLATION": "try [1.25, 1.5, 2.0]"},
        severity=lambda m: min(1.0, (0.85 - _safe(m, "ic90_coverage", 0.85)) / 0.20),
        category="calibration",
    ),

    Rule(
        name="ic90_wide",
        condition=lambda m: _safe(m, "ic90_coverage", 0.0) > 0.97,
        hypothesis=("IC90 > 97% — sub-confiado. σ predictivo demasiado grande. "
                    "Causas: Q sobre-dimensionado, INFLATION > 1, "
                    "R_CGM_BASE artificialmente alto."),
        params=["Q_G", "Q_SI", "R_CGM_BASE", "INFLATION"],
        sweep_hint={"Q_G": "decrease_2x", "INFLATION": "try 1.0",
                    "R_CGM_BASE": "decrease"},
        severity=lambda m: min(1.0, (_safe(m, "ic90_coverage", 0.95) - 0.95) / 0.10),
        category="calibration",
    ),

    Rule(
        name="ic50_narrow",
        condition=lambda m: _safe(m, "ic50_coverage", 1.0) < 0.40,
        hypothesis=("IC50 < 40% — la mediana del error es sistemáticamente mayor que "
                    "σ⋅0.67. Sub-dispersión severa cerca del centro. "
                    "Probablemente process noise muy chico."),
        params=["Q_G", "Q_SI", "INFLATION"],
        sweep_hint={"Q_G": "increase_2x", "Q_SI": "increase_10x"},
        severity=lambda m: min(1.0, (0.45 - _safe(m, "ic50_coverage", 0.45)) / 0.20),
        category="calibration",
    ),

    Rule(
        name="ic_asymmetric",
        condition=lambda m: (
            _safe(m, "ic50_coverage") is not None and
            _safe(m, "ic90_coverage") is not None and
            abs(_safe(m, "ic50_coverage") - 0.5) < 0.05 and
            abs(_safe(m, "ic90_coverage") - 0.9) > 0.10
        ),
        hypothesis=("IC50 OK pero IC90 fuera — asimetría: el centro está bien "
                    "calibrado pero las colas no. Innovations probablemente "
                    "no-gaussianas (heavy tails). Considerar noise no-gaussiano "
                    "o regime-specific dynamics."),
        params=["R_CGM_MARD", "K_A_MED", "Q_COB1", "Q_COB2"],
        sweep_hint={"R_CGM_MARD": "increase", "Q_COB2": "increase"},
        severity=lambda m: 0.6,
        category="calibration",
    ),

    # ─── INNOVATION WHITENESS ───────────────────────────────────────

    Rule(
        name="var_z_high",
        condition=lambda m: _safe(m, "var_z", 1.0) > 1.5,
        hypothesis=("var(innovation_z) > 1.5 — σ predictivo sub-dimensionado. "
                    "El modelo subestima incertidumbre. Causas típicas: "
                    "Q insuficiente, R_CGM_BASE bajo, dynamics rígidos."),
        params=["Q_G", "Q_SI", "R_CGM_BASE", "INFLATION"],
        sweep_hint={"Q_G": "increase_2x", "INFLATION": "try 1.5",
                    "R_CGM_BASE": "increase"},
        severity=lambda m: min(1.0, (_safe(m, "var_z", 1.0) - 1.2) / 1.0),
        category="innovation",
    ),

    Rule(
        name="var_z_low",
        condition=lambda m: _safe(m, "var_z", 1.0) < 0.5,
        hypothesis=("var(innovation_z) < 0.5 — σ predictivo sobre-dimensionado. "
                    "El modelo es demasiado humilde, intervalos innecesariamente "
                    "anchos. Reducir Q o R."),
        params=["Q_G", "Q_SI", "R_CGM_BASE", "INFLATION"],
        sweep_hint={"Q_G": "decrease_2x", "INFLATION": "decrease",
                    "R_CGM_BASE": "decrease"},
        severity=lambda m: min(1.0, (0.8 - _safe(m, "var_z", 0.8)) / 0.6),
        category="innovation",
    ),

    Rule(
        name="mean_z_biased",
        condition=lambda m: _safe(m, "abs_mean_z", 0.0) > 0.3,
        hypothesis=("|mean(innovation_z)| > 0.3 — bias sistemático. "
                    "El modelo {direction} de forma consistente. "
                    "Causas: EGP_BASAL mal calibrado, K_PI/K_IE asimétricos, "
                    "o input no observado (ej. dawn o ejercicio constante)."),
        params=["EGP_BASAL", "K_PI", "K_IE", "K_ACT"],
        sweep_hint={"EGP_BASAL": "try [0.35, 0.55, 0.80]", "K_ACT": "explore"},
        severity=lambda m: min(1.0, (_safe(m, "abs_mean_z", 0.0) - 0.2) / 0.5),
        category="innovation",
    ),

    Rule(
        name="autocorrelation_residual",
        condition=lambda m: (
            _safe(m, "lb_pvalue") is not None and
            _safe(m, "lb_pvalue", 1.0) < 0.05
        ),
        hypothesis=("Ljung-Box rechaza whiteness — innovations autocorreladas. "
                    "Información estructural NO capturada por el modelo. "
                    "Suele indicar estado oculto faltante (dawn, stress) o "
                    "dynamics demasiado rígidos."),
        params=["Q_SI", "LAMBDA_SI", "K_ACT"],
        sweep_hint={"Q_SI": "increase_5x", "LAMBDA_SI": "explore"},
        severity=lambda m: min(1.0, (0.05 - _safe(m, "lb_pvalue", 0.05)) / 0.05 + 0.4),
        category="innovation",
    ),

    Rule(
        name="heavy_tails",
        condition=lambda m: _safe(m, "kurt_excess", 0.0) > 2.0,
        hypothesis=("Kurtosis excess > 2 — colas pesadas. "
                    "Innovations grandes más frecuentes que normal. "
                    "Considerar gating de outliers más agresivo o regime-"
                    "specific noise."),
        params=["OUTLIER_GATE_SIGMA", "R_CGM_BASE"],
        sweep_hint={"OUTLIER_GATE_SIGMA": "decrease (4.0)",
                    "R_CGM_BASE": "increase"},
        severity=lambda m: min(1.0, (_safe(m, "kurt_excess", 0.0) - 1.0) / 4.0),
        category="innovation",
    ),

    # ─── COVARIANCE / STABILITY ────────────────────────────────────

    Rule(
        name="non_psd_events",
        condition=lambda m: _safe(m, "n_non_psd", 0) > 0,
        hypothesis=("Eventos non-PSD detectados — covariance perdió positive-"
                    "definiteness. Cholesky falla numéricamente. "
                    "Aumentar PSD_JITTER y/o reducir UKF_ALPHA."),
        params=["PSD_JITTER", "UKF_ALPHA", "SIGMA_FLOOR_G"],
        sweep_hint={"PSD_JITTER": "increase_10x", "UKF_ALPHA": "try 1e-3"},
        severity=lambda m: min(1.0, _safe(m, "n_non_psd", 0) / 10),
        category="stability",
    ),

    Rule(
        name="covariance_explosion",
        condition=lambda m: _safe(m, "n_explosion", 0) > 0,
        hypothesis=("Covariance explosion — tr(P) > threshold. "
                    "Filter divergiendo. Causas: process noise descontrolado, "
                    "INFLATION demasiado alto, ausencia de observaciones por "
                    "ventana larga (gaps de CGM)."),
        params=["Q_G", "INFLATION", "Q_SI"],
        sweep_hint={"Q_G": "decrease", "INFLATION": "decrease (1.0)"},
        severity=lambda m: min(1.0, _safe(m, "n_explosion", 0) / 5),
        category="stability",
    ),

    Rule(
        name="covariance_collapse",
        condition=lambda m: _safe(m, "n_collapse", 0) > 0,
        hypothesis=("Covariance collapse — tr(P) → 0. Filter sobre-confiado. "
                    "Considerar SIGMA_FLOOR_G > 0, INFLATION > 1, o aumento "
                    "del process noise."),
        params=["Q_G", "INFLATION", "SIGMA_FLOOR_G"],
        sweep_hint={"Q_G": "increase", "INFLATION": "increase",
                    "SIGMA_FLOOR_G": "try 2.0"},
        severity=lambda m: min(1.0, _safe(m, "n_collapse", 0) / 5),
        category="stability",
    ),

    # ─── ACCURACY ──────────────────────────────────────────────────

    Rule(
        name="mae_high",
        condition=lambda m: _safe(m, "mae_30", 0.0) > 25,
        hypothesis=("MAE_30 > 25 mg/dL — accuracy pobre. Buscar bias en "
                    "regime breakdown (post_meal probablemente peor). "
                    "Considerar refinar K_A_MED, K_G, EGP_BASAL."),
        params=["K_A_MED", "K_G", "EGP_BASAL", "K_PI"],
        sweep_hint={"K_A_MED": "explore [0.015, 0.035]",
                    "K_G": "explore [0.030, 0.060]"},
        severity=lambda m: min(1.0, (_safe(m, "mae_30", 25) - 20) / 30),
        category="accuracy",
    ),

    Rule(
        name="trend_degrading",
        condition=lambda m: _safe(m, "mae_trend_is_none", 1) == 0,
        hypothesis=("Mann-Kendall detecta drift de performance — MAE no "
                    "estacionaria. El modelo degrada con el tiempo. "
                    "Probablemente metabolismo evolucionó y LAMBDA_SI no "
                    "captura el cambio."),
        params=["LAMBDA_SI", "Q_SI"],
        sweep_hint={"LAMBDA_SI": "explore (mean-reversion más rápida)",
                    "Q_SI": "increase"},
        severity=lambda m: 0.7,
        category="longitudinal",
    ),

    # ─── REGIME-SPECIFIC (heavy via deep diagnostics by_regime) ────

    Rule(
        name="bias_post_meal",
        condition=lambda m: (
            _safe(m, "regime_post_meal_mean") is not None and
            abs(_safe(m, "regime_post_meal_mean", 0.0)) > 0.4
        ),
        hypothesis=("Bias significativo en regime post_meal — "
                    "la absorción de carbohidratos no está bien modelada. "
                    "Considerar refinar K_A por bucket, Q_COB, K_G."),
        params=["K_A_MED", "K_A_FAST", "K_A_SLOW", "K_G", "Q_COB1", "Q_COB2"],
        sweep_hint={"K_A_MED": "explore", "K_G": "explore", "Q_COB2": "increase"},
        severity=lambda m: 0.7,
        category="regime",
    ),

    Rule(
        name="autocorr_overnight",
        condition=lambda m: (
            _safe(m, "regime_overnight_var") is not None and
            _safe(m, "regime_overnight_var", 1.0) > 1.5
        ),
        hypothesis=("Sub-dispersión nocturna — innovations overnight con "
                    "varianza > 1.5. Dawn phenomenon no capturado por el "
                    "input determinístico, o EGP nocturno mal calibrado."),
        params=["EGP_BASAL", "Q_G"],
        sweep_hint={"EGP_BASAL": "explore", "Q_G": "increase"},
        severity=lambda m: 0.6,
        category="regime",
    ),
]


# ── Evaluator ──────────────────────────────────────────────────────────

def diagnose(metrics: dict) -> list[dict]:
    """
    Ejecuta todas las reglas sobre el dict de métricas y retorna las que
    disparan, ordenadas por confidence descendente.

    Returns
    -------
    Lista de Diagnosis-dicts. Vacía = todo OK.
    """
    diagnoses = []
    for rule in RULES:
        try:
            if rule.condition(metrics):
                conf = max(0.0, min(1.0, rule.severity(metrics)))
                # Snapshot solo de las métricas referenciadas
                snapshot = {k: metrics.get(k) for k in metrics
                            if k in (rule.params + list(rule.sweep_hint.keys())
                                     + ["ic50_coverage", "ic90_coverage", "var_z",
                                        "abs_mean_z", "lb_pvalue", "n_non_psd",
                                        "n_explosion", "n_collapse", "mae_30"])}
                diagnoses.append(Diagnosis(
                    rule=rule.name,
                    hypothesis=rule.hypothesis,
                    confidence=conf,
                    params=rule.params,
                    sweep_hint=rule.sweep_hint,
                    category=rule.category,
                    metric_snapshot={k: v for k, v in snapshot.items() if v is not None},
                ).to_dict())
        except Exception:
            continue   # regla mal formada — no romper el pipeline
    diagnoses.sort(key=lambda d: -d["confidence"])
    return diagnoses


def suggested_next_sweep(diagnoses: list[dict],
                          base_params=None) -> dict:
    """
    Consolida las hipótesis del top-3 en un sweep concreto.

    Retorna un dict {param: list_of_values} listo para crear un ExperimentSpec.
    """
    if not diagnoses:
        return {}

    from pmm.ssm.parameters import SSMParameters
    from bench.tuning.protocol import suggest_range
    base = base_params or SSMParameters()

    sweep: dict[str, list] = {}
    seen: set[str] = set()
    for d in diagnoses[:3]:
        for param, action in d.get("sweep_hint", {}).items():
            if param in seen:
                continue
            seen.add(param)
            current = getattr(base, param, None)
            if current is None:
                continue
            # Convertir hint textual a valores concretos
            vals = _expand_hint(action, current, param)
            if vals:
                sweep[param] = vals
    return sweep


def _expand_hint(action: str, current_value, param_name: str) -> list:
    """Convierte un hint textual a una lista concreta de valores."""
    from bench.tuning.protocol import suggest_range
    action_l = action.lower()

    if "increase_10x" in action_l:
        return [current_value, current_value * 3, current_value * 10]
    if "increase_5x" in action_l:
        return [current_value, current_value * 2, current_value * 5]
    if "increase_2x" in action_l:
        return [current_value, current_value * 1.5, current_value * 2]
    if "increase" in action_l and "x" not in action_l:
        return [current_value, current_value * 1.3, current_value * 1.7]
    if "decrease_10x" in action_l:
        return [current_value, current_value / 3, current_value / 10]
    if "decrease_2x" in action_l:
        return [current_value / 2, current_value / 1.5, current_value]
    if "decrease" in action_l:
        return [current_value / 1.7, current_value / 1.3, current_value]
    if "try" in action_l or "explore" in action_l:
        suggested = suggest_range(param_name)
        return suggested or [current_value]
    # Si no se entiende el hint, devolver el range default
    return suggest_range(param_name) or [current_value]
