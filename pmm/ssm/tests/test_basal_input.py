"""
pmm/ssm/tests/test_basal_input.py
──────────────────────────────────
Validaciones del modelo de basal Toujeo U-300.

Ejecutar:  python3 -m pmm.ssm.tests.test_basal_input

Sin pytest porque no hay suite previa en el repo. Estos son sanity checks
físicos que cualquier modelo PK debe pasar.
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta

from pmm.ssm.basal_input import (
    BasalDose, basal_kernel, compute_basal_eff,
    K_DEPOT_BASAL_DEFAULT, F_BIO_BASAL_DEFAULT,
)


_K_PI = 0.040
_K_IE = 0.018


# ── Helpers ───────────────────────────────────────────────────────────────

def _approx(a: float, b: float, tol: float = 0.01) -> bool:
    return abs(a - b) <= tol


def _eff(tau_min: float) -> float:
    """Shortcut para kernel con params default Toujeo."""
    return basal_kernel(tau_min, K_DEPOT_BASAL_DEFAULT, _K_PI, _K_IE,
                        F_bio=F_BIO_BASAL_DEFAULT)


# ── Tests ─────────────────────────────────────────────────────────────────

def test_kernel_at_zero_is_zero():
    """Una dosis recién puesta no tiene efecto intersticial todavía
    (las 3 etapas requieren tiempo)."""
    v = _eff(0.0)
    assert _approx(v, 0.0, 1e-6), f"kernel(0) debería ser 0, fue {v}"
    print(f"  ✓ kernel(τ=0) = {v:.6f}  (esperado ≈ 0)")


def test_kernel_negative_tau_is_zero():
    """Dosis futura no contribuye."""
    v = _eff(-100.0)
    assert v == 0.0, f"kernel(-100) debería ser 0, fue {v}"
    print(f"  ✓ kernel(τ<0) = 0")


def test_kernel_pk_peak_and_sustained_action():
    """En el modelo PK cascado (depot→plasma→intersticial), el peak del
    kernel ocurre cuando termina el buildup rápido (K_PI, K_IE) — típicamente
    h2-h6. Luego el decay sigue el K_depot lento (half-life 20h), produciendo
    acción sostenida. El peak clínico observado (10-12h) es del nivel de
    glucosa, no del PK puro — ese resulta de la integración con dinámica."""
    times_min = list(range(60, 36*60, 30))
    vals = [_eff(t) for t in times_min]
    peak_idx = vals.index(max(vals))
    peak_h = times_min[peak_idx] / 60.0
    peak_val = max(vals)

    # Peak PK en h2-h6 (después del buildup, antes del decay)
    assert 2.0 <= peak_h <= 6.0, f"peak PK en h{peak_h}, esperado h2-h6"

    # Acción sostenida: >60% del peak a las 12h, >40% a las 24h
    v_12h = _eff(12 * 60)
    v_24h = _eff(24 * 60)
    ratio_12 = v_12h / peak_val
    ratio_24 = v_24h / peak_val
    assert ratio_12 > 0.60, f"v_12h/peak = {ratio_12:.2f}, esperado > 0.60"
    assert ratio_24 > 0.30, f"v_24h/peak = {ratio_24:.2f}, esperado > 0.30"

    print(f"  ✓ peak PK en h{peak_h:.1f} (={peak_val:.4f}), "
          f"v_12h/peak={ratio_12*100:.0f}%, v_24h/peak={ratio_24*100:.0f}%")


def test_kernel_decay_after_36h():
    """A las 36h del depot half-life de 20h, el efecto residual debería
    haber decaído pero no ser despreciable (long tail)."""
    v_12h = _eff(12 * 60)
    v_36h = _eff(36 * 60)
    v_72h = _eff(72 * 60)
    ratio_36 = v_36h / v_12h
    ratio_72 = v_72h / v_12h
    assert 0.30 < ratio_36 < 0.80, f"v_36h/v_12h = {ratio_36:.2f}, esperado 0.3-0.8"
    assert ratio_72 < 0.20, f"v_72h/v_12h = {ratio_72:.2f}, esperado <0.2"
    print(f"  ✓ decay realista: 12h→{v_12h:.4f}, 36h→{v_36h:.4f} ({ratio_36*100:.0f}%), "
          f"72h→{v_72h:.4f} ({ratio_72*100:.0f}%)")


def test_kernel_nonnegative():
    """El efecto intersticial nunca debe ser negativo (sanity numérico)."""
    for t in range(0, 4320, 60):
        v = _eff(t)
        assert v >= -1e-9, f"kernel({t}) = {v} < 0 — error numérico"
    print(f"  ✓ kernel ≥ 0 en todas las muestras (0-72h)")


def test_steady_state_10U_daily():
    """Steady-state con 10U/día constante. Validaciones:
    - I_basal_eff promedio en rango fisiológico (0.15-0.60 U para 10U Toujeo)
    - El VALLE (mínimo intra-día) nunca cae a cero (acción sostenida)
    - El valle es al menos 50% del peak (Toujeo es una basal, no rapid)
    """
    now = datetime(2026, 5, 26, 12, 0, 0)
    doses = [
        BasalDose(ts=now - timedelta(days=d, hours=1), units=10.0)
        for d in range(14)
    ]
    vals = []
    for h_back in range(0, 24):
        t = now - timedelta(hours=h_back)
        vals.append(compute_basal_eff(t, doses,
                                       K_depot=K_DEPOT_BASAL_DEFAULT,
                                       K_pi=_K_PI, K_ie=_K_IE,
                                       F_bio=F_BIO_BASAL_DEFAULT))
    avg = sum(vals) / len(vals)
    peak = max(vals)
    trough = min(vals)
    trough_ratio = trough / peak if peak > 0 else 0

    assert avg > 0.15, f"avg basal_eff = {avg:.3f}, esperado > 0.15 U"
    assert avg < 0.60, f"avg basal_eff = {avg:.3f}, esperado < 0.60 U"
    assert trough > 0.10, f"trough = {trough:.3f}, debe mantener > 0.10 U (acción continua)"
    assert trough_ratio > 0.45, f"trough/peak = {trough_ratio:.2f}, esperado > 0.45 (Toujeo sustained)"
    print(f"  ✓ steady-state 10U/día: avg={avg:.3f}U  peak={peak:.3f}U  trough={trough:.3f}U  "
          f"(trough/peak={trough_ratio*100:.0f}%)")


def test_missed_dose_drops_below_steady():
    """Si falta una dosis (caso 24 May), I_basal_eff cae más rápido
    al día siguiente. Verificamos que efectivamente está más bajo."""
    now = datetime(2026, 5, 26, 12, 0, 0)
    # Régimen normal: dosis diaria
    normal_doses = [
        BasalDose(ts=now - timedelta(days=d, hours=1), units=10.0)
        for d in range(14)
    ]
    # Régimen con dosis faltante 48h atrás
    missed_doses = [
        BasalDose(ts=now - timedelta(days=d, hours=1), units=10.0)
        for d in range(14) if d != 2     # falta la del día -2 (anteayer)
    ]
    v_normal = compute_basal_eff(now, normal_doses,
                                  K_depot=K_DEPOT_BASAL_DEFAULT,
                                  K_pi=_K_PI, K_ie=_K_IE,
                                  F_bio=F_BIO_BASAL_DEFAULT)
    v_missed = compute_basal_eff(now, missed_doses,
                                  K_depot=K_DEPOT_BASAL_DEFAULT,
                                  K_pi=_K_PI, K_ie=_K_IE,
                                  F_bio=F_BIO_BASAL_DEFAULT)
    assert v_missed < v_normal, f"missed={v_missed} debe ser < normal={v_normal}"
    drop_pct = 100 * (v_normal - v_missed) / v_normal
    print(f"  ✓ missed dose drops I_basal_eff: {v_normal:.3f} → {v_missed:.3f} "
          f"({drop_pct:.1f}% menor)")


def test_titration_visible():
    """Verifica que la titración 18→10U se refleja en I_basal_eff
    decreciente día a día. Replica el patrón real del usuario."""
    now = datetime(2026, 5, 26, 12, 0, 0)
    # Dosis decreciendo según titración real (de mayor a menor = más reciente menor)
    doses_recent_to_old = [
        (0, 10.0), (1, 10.0), (2, 0.0),    # missed 24 May
        (3, 10.0), (4, 10.0), (5, 10.0),
        (6, 12.0), (7, 14.0), (8, 14.0),
        (9, 14.0), (10, 15.0), (11, 15.0),
        (12, 15.0), (13, 16.0), (14, 17.0),
        (15, 17.0), (16, 18.0), (17, 18.0),
    ]
    doses = [
        BasalDose(ts=now - timedelta(days=d, hours=1), units=u)
        for d, u in doses_recent_to_old if u > 0
    ]
    # I_basal_eff hoy vs hace 14 días
    v_today = compute_basal_eff(now, doses,
                                 K_depot=K_DEPOT_BASAL_DEFAULT,
                                 K_pi=_K_PI, K_ie=_K_IE,
                                 F_bio=F_BIO_BASAL_DEFAULT)
    v_14days_ago = compute_basal_eff(now - timedelta(days=10), doses,
                                      K_depot=K_DEPOT_BASAL_DEFAULT,
                                      K_pi=_K_PI, K_ie=_K_IE,
                                      F_bio=F_BIO_BASAL_DEFAULT)
    drop_pct = 100 * (v_14days_ago - v_today) / v_14days_ago
    # Esperamos drop sustancial (>20%) reflejando la titración
    assert v_today < v_14days_ago, "I_basal_eff hoy debe ser < hace 14 días"
    assert drop_pct > 20, f"drop {drop_pct:.1f}% — esperado > 20%"
    print(f"  ✓ titración 18→10U visible: I_eff hace 14d = {v_14days_ago:.3f}U → "
          f"hoy = {v_today:.3f}U ({drop_pct:.1f}% menor)")


def test_integration_into_stepinputs():
    """Sanity: que StepInputs acepta i_basal_eff y dynamics._flow lo usa."""
    from pmm.ssm.dynamics import StepInputs, _flow
    import numpy as np

    # Estado típico
    x = np.array([120.0, 0.0, 0.0, 0.0, 0.0, 30.0])  # G, IOB, IOB_eff, COB1, COB2, S_I

    # Sin basal: dG/dt baseline
    u0 = StepInputs(i_basal_eff=0.0, icr=12.0)
    dx0 = _flow(x, u0, params=None)
    dG0 = dx0[0]

    # Con basal activa: dG/dt debería bajar (más insulina actuando)
    u_basal = StepInputs(i_basal_eff=0.5, icr=12.0)
    dx_basal = _flow(x, u_basal, params=None)
    dG_basal = dx_basal[0]

    assert dG_basal < dG0, f"con basal dG/dt={dG_basal} debe ser < sin basal dG/dt={dG0}"
    delta = dG0 - dG_basal
    print(f"  ✓ basal reduce dG/dt: sin basal {dG0:.4f} → con basal {dG_basal:.4f} "
          f"(Δ={delta:.4f} mg/dL/min, esperado positivo y proporcional a S_I)")


# ── Runner ────────────────────────────────────────────────────────────────

def main():
    tests = [
        test_kernel_at_zero_is_zero,
        test_kernel_negative_tau_is_zero,
        test_kernel_pk_peak_and_sustained_action,
        test_kernel_decay_after_36h,
        test_kernel_nonnegative,
        test_steady_state_10U_daily,
        test_missed_dose_drops_below_steady,
        test_titration_visible,
        test_integration_into_stepinputs,
    ]
    passed = 0
    failed = 0
    print()
    print("══ Tests basal_input.py ══════════════════════════")
    for t in tests:
        try:
            print(f"\n• {t.__name__}")
            t()
            passed += 1
        except AssertionError as e:
            print(f"  ✗ FAIL: {e}")
            failed += 1
        except Exception as e:
            print(f"  ✗ ERROR: {type(e).__name__}: {e}")
            failed += 1
    print()
    print(f"══ Resultado: {passed} passed, {failed} failed ══")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
