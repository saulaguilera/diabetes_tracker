"""
pmm/core/parameter_store.py
────────────────────────────
Gestión de parámetros metabólicos aprendidos con incertidumbre Bayesiana.

Cada parámetro (ISF, ICR) se representa como una distribución Gaussiana:
    param ~ N(μ, σ²)

La actualización usa el modelo conjugado Gaussiano-Gaussiano:
    posterior_prec = prior_prec + obs_prec
    μ_post = (μ_prior × prior_prec + x_obs × obs_prec) / posterior_prec
    σ_post = 1 / sqrt(posterior_prec)

El forgetting factor (λ < 1) infla σ gradualmente para que datos antiguos
pesen menos a medida que llegan nuevas observaciones.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# ── Priors por defecto (amplios, no informados) ────────────────────────────────
# Se usan cuando no hay ningún dato del usuario.
_PRIORS = {
    "ISF":          {"mu": 45.0, "sigma": 20.0},   # mg/dL·U — rango clínico típico 20-100
    "ICR":          {"mu": 12.0, "sigma": 6.0},    # g CH / U  — rango clínico típico 5-25
    "KASPEED_FAST": {"mu": 1.0,  "sigma": 0.25},   # speed factor — 1.0 = igual al modelo poblacional
    "KASPEED_MED":  {"mu": 1.0,  "sigma": 0.25},
    "KASPEED_SLOW": {"mu": 1.0,  "sigma": 0.25},
}

# Mínimo de sigma admisible (nunca podemos saber el ISF exacto)
_SIGMA_FLOOR = {
    "ISF":          3.0,
    "ICR":          1.0,
    "KASPEED_FAST": 0.08,
    "KASPEED_MED":  0.08,
    "KASPEED_SLOW": 0.08,
}

# Forgetting factor por observación: λ = 0.995
# ⟹ half-life ≈ 138 observaciones
# ⟹ con ~1 obs limpia/día, los datos de hace 4-5 meses pesan la mitad
_LAMBDA = 0.995

# Ruido de observación base (incertidumbre de medición física)
# Incluso una observación perfecta tiene ruido por sensor, timing, etc.
_OBS_SIGMA_BASE = {
    "ISF":          8.0,   # mg/dL·U
    "ICR":          2.5,   # g CH / U
    "KASPEED_FAST": 0.20,  # speed factor — ruido base de la observación
    "KASPEED_MED":  0.20,
    "KASPEED_SLOW": 0.20,
}


@dataclass
class ParameterState:
    """
    Estado Bayesiano de un parámetro escalar.

    Atributos
    ---------
    mu      : estimación puntual (media posterior)
    sigma   : incertidumbre (std posterior)
    n_obs   : número de observaciones incorporadas
    """
    mu: float
    sigma: float
    n_obs: int = 0

    # ── Propiedades derivadas ──────────────────────────────────────────────────

    @property
    def variance(self) -> float:
        return self.sigma ** 2

    @property
    def precision(self) -> float:
        return 1.0 / self.variance if self.sigma > 0 else float("inf")

    @property
    def ci_95(self) -> tuple[float, float]:
        """Intervalo de confianza 95% (±1.96σ)."""
        z = 1.96 * self.sigma
        return (round(self.mu - z, 1), round(self.mu + z, 1))

    @property
    def confidence(self) -> float:
        """
        Score de confianza 0-1.
        Aumenta con n_obs y disminuye con sigma relativa.
        """
        if self.n_obs == 0:
            return 0.0
        # Confianza basada en cuánto se redujo sigma respecto al prior
        return min(1.0, round(self.n_obs / (self.n_obs + 10), 2))

    # ── Update Bayesiano ───────────────────────────────────────────────────────

    def bayesian_update(
        self,
        obs_value: float,
        obs_sigma: float,
        param_name: str = "ISF",
        forgetting: float = _LAMBDA,
    ) -> "ParameterState":
        """
        Actualiza el prior con una nueva observación usando conjugado Gaussiano.

        Parámetros
        ----------
        obs_value  : valor observado del parámetro (ej. ISF_obs = ΔG / U)
        obs_sigma  : incertidumbre de esa observación (< obs → más peso)
        forgetting : factor de olvido λ ∈ (0,1]; 1.0 = sin olvido

        Retorna un nuevo ParameterState (inmutable).
        """
        # 1. Aplicar forgetting factor: inflar σ para dar más peso a datos recientes
        sigma_decayed = self.sigma / math.sqrt(forgetting)

        # 2. Calcular precisiones (inversa de varianza)
        prior_prec = 1.0 / (sigma_decayed ** 2)
        obs_prec   = 1.0 / (obs_sigma ** 2)

        # 3. Update conjugado
        posterior_prec  = prior_prec + obs_prec
        posterior_mu    = (self.mu * prior_prec + obs_value * obs_prec) / posterior_prec
        posterior_sigma = 1.0 / math.sqrt(posterior_prec)

        # 4. Aplicar floor de sigma
        floor = _SIGMA_FLOOR.get(param_name, 2.0)
        posterior_sigma = max(floor, posterior_sigma)

        return ParameterState(
            mu=round(posterior_mu, 2),
            sigma=round(posterior_sigma, 2),
            n_obs=self.n_obs + 1,
        )

    def to_dict(self) -> dict:
        lo, hi = self.ci_95
        return {
            "mu":         self.mu,
            "sigma":      self.sigma,
            "n_obs":      self.n_obs,
            "ci_95_lo":   lo,
            "ci_95_hi":   hi,
            "confidence": self.confidence,
        }


# ── Funciones de acceso a DB ───────────────────────────────────────────────────

def _obs_sigma_for_quality(param_name: str, quality: float) -> float:
    """
    Convierte quality score (0-1) a sigma de observación.

    Cuanto mayor la calidad → menor sigma → la obs tiene más peso.
    Cuanto menor la calidad → mayor sigma → la obs tiene menos peso.
    """
    base = _OBS_SIGMA_BASE.get(param_name, 8.0)
    # quality=1.0 → obs_sigma = base/2
    # quality=0.5 → obs_sigma = base
    # quality=0.3 → obs_sigma = base×1.67
    obs_sigma = base / (quality * 2.0)
    # Floor: incluso obs perfectas tienen ruido
    return max(base / 3.0, obs_sigma)


def load_parameter(param_name: str, context_block: int = -1) -> ParameterState:
    """
    Carga el parámetro desde la DB.
    Si no existe, devuelve el prior por defecto.
    """
    from models import PMMParameter

    row = PMMParameter.query.filter_by(
        param_name=param_name,
        context_block=context_block,
    ).first()

    if row:
        return ParameterState(mu=row.mu, sigma=row.sigma, n_obs=row.n_obs)

    # Prior no informado
    prior = _PRIORS.get(param_name, {"mu": 50.0, "sigma": 20.0})
    return ParameterState(mu=prior["mu"], sigma=prior["sigma"], n_obs=0)


def save_parameter(
    param_name: str,
    context_block: int,
    state: ParameterState,
) -> None:
    """Persiste el parámetro en DB (upsert)."""
    from models import PMMParameter, db

    row = PMMParameter.query.filter_by(
        param_name=param_name,
        context_block=context_block,
    ).first()

    if row:
        row.mu           = state.mu
        row.sigma        = state.sigma
        row.n_obs        = state.n_obs
        row.last_updated = datetime.utcnow()
    else:
        row = PMMParameter(
            param_name=param_name,
            context_block=context_block,
            mu=state.mu,
            sigma=state.sigma,
            n_obs=state.n_obs,
        )
        db.session.add(row)

    db.session.commit()


def get_isf_now(hora: Optional[int] = None) -> dict:
    """
    Devuelve el ISF del PMM para la hora actual (o la hora especificada).

    Jerarquía de fallback:
        1. ISF del bloque horario (si n_obs >= 3)
        2. ISF global (si n_obs >= 5)
        3. Prior no informado

    Retorna dict con: mu, sigma, ci_95, confidence, source, block_label
    """
    from datetime import datetime as dt

    if hora is None:
        hora = dt.now().hour

    block = (hora // 4) * 4
    block_labels = {
        0: "00–04h", 4: "04–08h", 8: "08–12h",
        12: "12–16h", 16: "16–20h", 20: "20–24h",
    }

    # Intentar bloque específico
    state_block = load_parameter("ISF", context_block=block)
    if state_block.n_obs >= 3:
        result = state_block.to_dict()
        result["source"]      = "circadiano"
        result["block"]       = block
        result["block_label"] = block_labels.get(block, f"{block}h")
        return result

    # Fallback: global
    state_global = load_parameter("ISF", context_block=-1)
    if state_global.n_obs >= 5:
        result = state_global.to_dict()
        result["source"]      = "global"
        result["block"]       = -1
        result["block_label"] = "global"
        return result

    # Fallback: prior (sin datos suficientes)
    result = state_global.to_dict()
    result["source"]      = "prior"
    result["block"]       = -1
    result["block_label"] = "prior"
    return result


def get_icr_now(hora: Optional[int] = None) -> dict:
    """
    Devuelve el ICR del PMM para la hora actual (mismo esquema que get_isf_now).
    """
    from datetime import datetime as dt

    if hora is None:
        hora = dt.now().hour

    block = (hora // 4) * 4
    block_labels = {
        0: "00–04h", 4: "04–08h", 8: "08–12h",
        12: "12–16h", 16: "16–20h", 20: "20–24h",
    }

    state_block = load_parameter("ICR", context_block=block)
    if state_block.n_obs >= 3:
        result = state_block.to_dict()
        result["source"]      = "circadiano"
        result["block"]       = block
        result["block_label"] = block_labels.get(block, f"{block}h")
        return result

    state_global = load_parameter("ICR", context_block=-1)
    if state_global.n_obs >= 3:
        result = state_global.to_dict()
        result["source"]      = "global"
        result["block"]       = -1
        result["block_label"] = "global"
        return result

    result = state_global.to_dict()
    result["source"]      = "prior"
    result["block"]       = -1
    result["block_label"] = "prior"
    return result


def get_all_blocks(param_name: str) -> dict:
    """
    Devuelve todos los bloques horarios para un parámetro.
    Útil para el dashboard de aprendizaje circadiano.
    """
    blocks = {}
    block_labels = {
        0: "00–04h", 4: "04–08h", 8: "08–12h",
        12: "12–16h", 16: "16–20h", 20: "20–24h",
    }
    for block in [0, 4, 8, 12, 16, 20]:
        state = load_parameter(param_name, context_block=block)
        d = state.to_dict()
        d["block_label"] = block_labels[block]
        d["has_data"] = state.n_obs >= 2
        blocks[block] = d

    global_state = load_parameter(param_name, context_block=-1)
    d = global_state.to_dict()
    d["block_label"] = "global"
    d["has_data"] = global_state.n_obs >= 3
    blocks[-1] = d

    return blocks
