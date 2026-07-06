"""
utils/agp.py
────────────
AGP — Ambulatory Glucose Profile (perfil ambulatorio de glucosa).

El formato ESTÁNDAR que los equipos médicos esperan ver (consenso
internacional ATTD 2019): curva de percentiles de glucosa por hora del día
(mediana + bandas 25-75 y 5-95), tiempo en rangos de 5 bandas, GMI y CV.

Genera los gráficos con matplotlib (PNG en memoria) para incrustar en el
reporte PDF del equipo médico. 100% descriptivo — el AGP ES descripción.
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta

# Bandas estándar de tiempo en rango (consenso internacional)
BANDS = [
    ("muy bajo (<54)",    0,   54,  "#8B1A1A"),
    ("bajo (54-69)",      54,  70,  "#D9534F"),
    ("en rango (70-180)", 70,  181, "#4CAF7D"),
    ("alto (181-250)",    181, 251, "#E8B04B"),
    ("muy alto (>250)",   251, 999, "#D07A2E"),
]

_BIN_MIN = 30          # bins de 30 min → 48 por día
_N_BINS = 24 * 60 // _BIN_MIN


# ─────────────────────── cómputo (testeable) ───────────────────────

def band_pcts(values: list) -> list[tuple[str, float, str]]:
    """% de lecturas en cada banda estándar → [(label, pct, color)]."""
    n = len(values)
    if not n:
        return [(label, 0.0, color) for label, _, _, color in BANDS]
    out = []
    for label, lo, hi, color in BANDS:
        pct = 100.0 * sum(1 for v in values if lo <= v < hi) / n
        out.append((label, round(pct, 1), color))
    return out


def agp_percentiles(times: list, values: list) -> dict | None:
    """
    Percentiles 5/25/50/75/95 por bin de 30 min del día (suavizados con
    ventana circular de 3 bins). None si no hay datos suficientes.
    """
    import numpy as np

    bins: list[list[float]] = [[] for _ in range(_N_BINS)]
    for t, v in zip(times, values):
        bins[(t.hour * 60 + t.minute) // _BIN_MIN].append(v)
    if sum(1 for b in bins if len(b) >= 3) < _N_BINS // 2:
        return None

    qs = (5, 25, 50, 75, 95)
    raw = {q: np.full(_N_BINS, np.nan) for q in qs}
    for i, b in enumerate(bins):
        if len(b) >= 3:
            pcts = np.percentile(b, qs)
            for q, val in zip(qs, pcts):
                raw[q][i] = val
    # rellenar bins vacíos por interpolación circular simple
    for q in qs:
        arr = raw[q]
        if np.isnan(arr).any():
            idx = np.arange(_N_BINS)
            good = ~np.isnan(arr)
            arr[~good] = np.interp(idx[~good], idx[good], arr[good], period=_N_BINS)
    # suavizado circular (media móvil de 3)
    smooth = {}
    for q in qs:
        arr = raw[q]
        smooth[q] = (np.roll(arr, 1) + arr + np.roll(arr, -1)) / 3.0
    smooth["hours"] = (np.arange(_N_BINS) * _BIN_MIN + _BIN_MIN / 2) / 60.0
    return smooth


def agp_metrics(times: list, values: list, days: int) -> dict:
    """Métricas estándar del encabezado AGP."""
    import numpy as np
    n = len(values)
    if not n:
        return {"n": 0}
    arr = np.asarray(values, dtype=float)
    mean = float(arr.mean())
    cv = float(arr.std() / mean * 100) if mean else None
    expected = days * (24 * 60 // 5)   # lecturas esperadas a cadencia 5 min
    dias_con_datos = len({t.date() for t in times})
    return {
        "n": n,
        "dias_con_datos": dias_con_datos,
        "promedio": round(mean),
        "gmi": round(3.31 + 0.02392 * mean, 1),
        "cv": round(cv, 1) if cv is not None else None,
        "sensor_activo_pct": round(min(100.0, 100.0 * n / expected), 1),
    }


# ─────────────────────── datos + gráficos ───────────────────────

def _load(days: int):
    from models import GlucoseReading
    since = datetime.now() - timedelta(days=days)
    reads = (GlucoseReading.query
             .filter(GlucoseReading.timestamp >= since,
                     GlucoseReading.is_artifact == False)  # noqa: E712
             .order_by(GlucoseReading.timestamp).all())
    return [r.timestamp for r in reads], [r.value_mgdl for r in reads]


def agp_chart_png(days: int = 14) -> bytes | None:
    """La curva AGP clásica: mediana + bandas 25-75 y 5-95 por hora del día."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    times, values = _load(days)
    p = agp_percentiles(times, values)
    if not p:
        return None

    fig, ax = plt.subplots(figsize=(7.4, 2.9), dpi=150)
    h = p["hours"]
    ax.fill_between(h, p[5], p[95], color="#BFD8EE", alpha=0.55, linewidth=0,
                    label="5-95%")
    ax.fill_between(h, p[25], p[75], color="#7FB3DC", alpha=0.75, linewidth=0,
                    label="25-75%")
    ax.plot(h, p[50], color="#1F5FA8", linewidth=2.0, label="mediana")
    # rango objetivo 70-180
    ax.axhline(70, color="#4CAF7D", linewidth=0.9, linestyle="--", alpha=0.9)
    ax.axhline(180, color="#4CAF7D", linewidth=0.9, linestyle="--", alpha=0.9)
    ax.axhspan(70, 180, color="#4CAF7D", alpha=0.06)

    ax.set_xlim(0, 24)
    ax.set_ylim(40, max(260, float(max(p[95])) + 20))
    ax.set_xticks(range(0, 25, 3))
    ax.set_xticklabels([f"{x:02d}" for x in range(0, 25, 3)], fontsize=7.5)
    ax.tick_params(axis="y", labelsize=7.5)
    ax.set_ylabel("mg/dL", fontsize=8)
    ax.set_xlabel("hora del día", fontsize=8)
    ax.legend(loc="upper right", fontsize=7, frameon=False, ncols=3)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    fig.tight_layout(pad=0.6)

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def tir_bar_png(days: int = 14) -> bytes | None:
    """Barra apilada horizontal de tiempo en rangos (5 bandas estándar)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _, values = _load(days)
    if len(values) < 50:
        return None
    bands = band_pcts(values)

    fig, ax = plt.subplots(figsize=(7.4, 0.92), dpi=150)
    left = 0.0
    for label, pct, color in bands:
        ax.barh(0, pct, left=left, color=color, height=0.6)
        if pct >= 4:
            ax.text(left + pct / 2, 0, f"{pct:.0f}%", ha="center", va="center",
                    fontsize=7.5, color="white", fontweight="bold")
        left += pct
    ax.set_xlim(0, 100)
    ax.axis("off")
    # leyenda compacta debajo
    fig.legend([plt.Rectangle((0, 0), 1, 1, color=c) for _, _, c in bands],
               [b[0] for b in bands], loc="lower center", ncols=5, fontsize=6.4,
               frameon=False, bbox_to_anchor=(0.5, -0.14))
    fig.tight_layout(pad=0.3)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def agp_summary(days: int = 14) -> dict:
    """Métricas del encabezado AGP para el PDF."""
    times, values = _load(days)
    return agp_metrics(times, values, days)
