"""
pmm/ssm/ukf.py
───────────────
Unscented Kalman Filter — implementación genérica numpy/scipy.

Por qué UKF (y no EKF / Particle Filter)
----------------------------------------
- El sistema tiene no-linealidades suaves (productos S_I × IOB_eff × G,
  exponenciales de absorción) — bien capturadas por sigma points.
- 6-dim → 13 sigma points → ~1ms por step. Real-time viable.
- No requiere jacobianos (vs EKF) — robusto a cambios del modelo.
- Mucho más rápido que PF (que sería N=500 evaluaciones).

Cuando NO usar UKF
------------------
- Posteriors multi-modal (illness onset, missed meals): UKF fuerza
  unimodalidad. Fallback recomendado: Bootstrap Particle Filter cuando
  log-evidence cae > 3σ vs baseline (próximo hito).

Parámetros del UKF
------------------
- α ∈ (1e-4, 1]: spread de los sigma points alrededor del mean.
  Pequeño → menos non-linearity capture pero más estable numericamente.
  Defecto α=1e-3 (recomendación Wan & van der Merwe 2000).
- β = 2: optimal para gaussianas.
- κ = 3 - n usual; aquí n=6 → κ = -3. Para evitar weights negativos
  cuando hay subdimensionalidad real, dejamos κ=0.

Referencias
-----------
- Julier & Uhlmann (2004) "Unscented filtering and nonlinear estimation"
- Wan & van der Merwe (2000) "UKF for non-linear estimation"
- Särkkä (2013) "Bayesian Filtering and Smoothing", Cambridge.
"""
from __future__ import annotations

import math
from typing import Callable, Optional, Tuple

import numpy as np
from scipy.linalg import cholesky, LinAlgError


# Defaults conservadores (Wan & van der Merwe 2000)
_ALPHA   = 1e-3
_BETA    = 2.0
_KAPPA   = 0.0
_JITTER  = 1e-6      # estabilidad numérica del Cholesky


class UKF:
    """
    Unscented Kalman Filter para state space x ∈ R^n, observation z ∈ R^m.

    Uso típico:
        ukf = UKF(dim_x=6, dim_z=1)
        ukf.x = mu_init                 # (n,)
        ukf.P = cov_init                # (n, n)
        ukf.Q = Q                       # process noise (n, n)
        ukf.R = R                       # obs noise (m, m)

        # cada nuevo dt:
        ukf.predict(fx=lambda x: dynamics_step(x, u, dt))
        # cuando llega obs:
        ll = ukf.update(z=y_obs, hx=h_function, R=R_for_this_obs)
    """

    def __init__(self, dim_x: int, dim_z: int,
                 alpha: float = _ALPHA,
                 beta:  float = _BETA,
                 kappa: float = _KAPPA):
        self.n = dim_x
        self.m = dim_z
        self.alpha = alpha
        self.beta  = beta
        self.kappa = kappa

        self.lam   = alpha**2 * (self.n + kappa) - self.n
        self.gamma = math.sqrt(self.n + self.lam)

        # Pesos de mean y cov
        self.Wm = np.full(2 * self.n + 1, 1.0 / (2.0 * (self.n + self.lam)))
        self.Wc = self.Wm.copy()
        self.Wm[0] = self.lam / (self.n + self.lam)
        self.Wc[0] = self.lam / (self.n + self.lam) + (1.0 - alpha**2 + beta)

        # Estado / matrices — se inicializan externamente
        self.x: np.ndarray = np.zeros(self.n)
        self.P: np.ndarray = np.eye(self.n)
        self.Q: np.ndarray = np.eye(self.n) * 1e-3
        self.R: np.ndarray = np.eye(self.m) * 1.0

    # ── Generación de sigma points ────────────────────────────────────

    def _sigma_points(self, x: np.ndarray, P: np.ndarray) -> np.ndarray:
        """
        2n+1 sigma points alrededor de x usando Cholesky de (n+λ)P.
        Si Cholesky falla, agregamos jitter creciente hasta que pase
        (siempre converge para matrices PSD con jitter suficiente).
        """
        P_sym = 0.5 * (P + P.T)  # forzar simetría
        scaled = (self.n + self.lam) * P_sym

        for jitter in (0.0, _JITTER, _JITTER * 100, _JITTER * 1e4):
            try:
                M = scaled + jitter * np.eye(self.n)
                L = cholesky(M, lower=True)
                break
            except LinAlgError:
                continue
        else:
            # Última opción: usar SVD para recovery
            U, s, _ = np.linalg.svd(scaled)
            s_clip  = np.maximum(s, _JITTER)
            L = U @ np.diag(np.sqrt(s_clip))

        pts = np.zeros((2 * self.n + 1, self.n))
        pts[0] = x
        for i in range(self.n):
            pts[i + 1]          = x + L[:, i]
            pts[self.n + i + 1] = x - L[:, i]
        return pts

    # ── Recomposición mean/cov desde puntos propagados ──────────────────

    def _reconstruct(self, points: np.ndarray, noise: Optional[np.ndarray] = None
                     ) -> Tuple[np.ndarray, np.ndarray]:
        mean = np.sum(self.Wm[:, None] * points, axis=0)
        diffs = points - mean
        cov   = (self.Wc[:, None, None] * diffs[:, :, None] * diffs[:, None, :]).sum(axis=0)
        if noise is not None:
            cov = cov + noise
        return mean, cov

    # ── Predict ─────────────────────────────────────────────────────────

    def predict(self, fx: Callable[[np.ndarray], np.ndarray],
                Q: Optional[np.ndarray] = None) -> None:
        """
        Propaga el posterior un step adelante aplicando fx a cada sigma point.

        fx debe ser: (state n,) → (state n,)
        Q opcional override del process noise (sino usa self.Q).
        """
        Q_use = Q if Q is not None else self.Q
        sigmas = self._sigma_points(self.x, self.P)
        propagated = np.array([fx(s) for s in sigmas])
        self.x, self.P = self._reconstruct(propagated, noise=Q_use)

    # ── Update con observación ──────────────────────────────────────────

    def update(self, z: np.ndarray,
               hx: Callable[[np.ndarray], np.ndarray],
               R: Optional[np.ndarray] = None) -> float:
        """
        Actualiza el posterior con observación z aplicando hx a cada sigma point.

        Returns
        -------
        log_likelihood : float
            log p(z | x_pred, P_pred + R) — útil para anomaly score.
        """
        R_use = R if R is not None else self.R
        sigmas = self._sigma_points(self.x, self.P)
        # Mapeo a observación
        z_sigmas = np.array([hx(s) for s in sigmas])

        # Mean y cov en espacio de observación
        z_mean = np.sum(self.Wm[:, None] * z_sigmas, axis=0)
        z_diff = z_sigmas - z_mean
        S = (self.Wc[:, None, None] * z_diff[:, :, None] * z_diff[:, None, :]).sum(axis=0)
        S = S + R_use

        # Cross-covarianza
        x_diff = sigmas - self.x
        Pxz = (self.Wc[:, None, None] * x_diff[:, :, None] * z_diff[:, None, :]).sum(axis=0)

        # Kalman gain (solve en lugar de invertir)
        try:
            K = np.linalg.solve(S.T, Pxz.T).T
        except np.linalg.LinAlgError:
            # S casi singular — invertir con pseudo-inverse
            K = Pxz @ np.linalg.pinv(S)

        innov = z - z_mean

        # State update
        self.x = self.x + K @ innov
        self.P = self.P - K @ S @ K.T

        # Re-simetrizar para evitar drift numérico
        self.P = 0.5 * (self.P + self.P.T)

        # Log-likelihood de la observación bajo el predictivo (gaussiano):
        #   log p(z) = -0.5 [m log(2π) + log|S| + innov.T S^-1 innov]
        try:
            sign, logdet = np.linalg.slogdet(S)
            if sign <= 0:
                return -np.inf
            mahal = innov @ np.linalg.solve(S, innov)
            log_lik = -0.5 * (self.m * math.log(2 * math.pi) + logdet + mahal)
        except np.linalg.LinAlgError:
            log_lik = -np.inf

        return float(log_lik)

    # ── Convenience: copy del estado ────────────────────────────────────

    def snapshot(self) -> Tuple[np.ndarray, np.ndarray]:
        return self.x.copy(), self.P.copy()

    def restore(self, x: np.ndarray, P: np.ndarray) -> None:
        self.x = x.copy()
        self.P = P.copy()
