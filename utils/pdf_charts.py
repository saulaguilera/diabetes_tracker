"""
Gráficas estáticas en matplotlib para el PDF del reporte clínico.
Devuelven cadenas base64 para incrustar directamente en HTML.
"""
import io, base64
from datetime import datetime, timedelta
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")          # sin pantalla
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

RANGO_BAJO = 70
RANGO_ALTO = 180
C_HIPO  = "#ef4444"
C_RANGO = "#22c55e"
C_HIPER = "#f97316"
C_AZUL  = "#3b82f6"


def _b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    buf.seek(0)
    data = base64.b64encode(buf.read()).decode()
    plt.close(fig)
    return f"data:image/png;base64,{data}"


def chart_pdf_tir(valores: list) -> str:
    """Gráfica de dona con TIR / TBR / TAR."""
    if not valores:
        return ""
    n = len(valores)
    hipo  = len([v for v in valores if v < RANGO_BAJO])
    rango = len([v for v in valores if RANGO_BAJO <= v <= RANGO_ALTO])
    hiper = len([v for v in valores if v > RANGO_ALTO])

    sizes  = [rango, hipo, hiper]
    colors = [C_RANGO, C_HIPO, C_HIPER]
    labels = [
        f"En rango\n{round(rango/n*100,1)}%",
        f"Hipo\n{round(hipo/n*100,1)}%",
        f"Hiper\n{round(hiper/n*100,1)}%",
    ]

    fig, ax = plt.subplots(figsize=(4.2, 4.2), facecolor="white")
    wedges, texts = ax.pie(
        sizes, colors=colors, startangle=90,
        wedgeprops=dict(width=0.52, edgecolor="white", linewidth=2),
    )
    # Etiquetas
    for i, (wedge, label) in enumerate(zip(wedges, labels)):
        angle = (wedge.theta2 + wedge.theta1) / 2
        import math
        x = 0.75 * math.cos(math.radians(angle))
        y = 0.75 * math.sin(math.radians(angle))
        ax.annotate(label, xy=(x, y), ha="center", va="center",
                    fontsize=8, color="white", fontweight="bold")

    # Centro
    ax.text(0, 0, f"{round(rango/n*100,1)}%\nen rango",
            ha="center", va="center", fontsize=11, fontweight="bold", color="#1e293b")
    ax.set_title("Tiempo en rango", fontsize=10, fontweight="bold", pad=8, color="#1e293b")
    return _b64(fig)


def chart_pdf_circadiano(lecturas: list) -> str:
    """Patrón circadiano: promedio de glucemia por hora del día."""
    if not lecturas:
        return ""

    por_hora = defaultdict(list)
    for r in lecturas:
        por_hora[r.timestamp.hour].append(r.value_mgdl)

    horas = list(range(24))
    promedios = [
        sum(por_hora[h]) / len(por_hora[h]) if por_hora[h] else None
        for h in horas
    ]
    maximos = [max(por_hora[h]) if por_hora[h] else None for h in horas]
    minimos = [min(por_hora[h]) if por_hora[h] else None for h in horas]

    # Solo horas con datos
    horas_ok = [h for h in horas if promedios[h] is not None]
    prom_ok  = [promedios[h] for h in horas_ok]
    max_ok   = [maximos[h]   for h in horas_ok]
    min_ok   = [minimos[h]   for h in horas_ok]

    if not horas_ok:
        return ""

    fig, ax = plt.subplots(figsize=(7.5, 2.8), facecolor="white")

    # Banda rango
    ax.axhspan(RANGO_BAJO, RANGO_ALTO, color="#22c55e", alpha=0.08, zorder=0)
    ax.axhline(RANGO_BAJO, color=C_HIPO,  lw=1, ls="--", alpha=0.6)
    ax.axhline(RANGO_ALTO, color=C_HIPER, lw=1, ls="--", alpha=0.6)

    # Banda min-max
    ax.fill_between(horas_ok, min_ok, max_ok, alpha=0.15, color=C_AZUL, zorder=1)

    # Línea promedio
    ax.plot(horas_ok, prom_ok, color=C_AZUL, lw=2, marker="o",
            markersize=3, zorder=2, label="Promedio")

    ax.set_xticks(range(0, 24, 3))
    ax.set_xticklabels([f"{h:02d}h" for h in range(0, 24, 3)], fontsize=8)
    ax.set_ylabel("mg/dL", fontsize=8)
    ax.set_title("Patrón circadiano (promedio por hora)", fontsize=10,
                 fontweight="bold", color="#1e293b")
    ax.tick_params(axis="both", labelsize=8)
    ax.set_facecolor("#f8fafc")
    ax.spines[["top", "right"]].set_visible(False)

    # Etiquetas límites
    ax.text(23.5, RANGO_BAJO + 3, "70", fontsize=7, color=C_HIPO, ha="right")
    ax.text(23.5, RANGO_ALTO + 3, "180", fontsize=7, color=C_HIPER, ha="right")

    fig.tight_layout()
    return _b64(fig)


def chart_pdf_timeline(lecturas: list) -> str:
    """Línea de tiempo de glucemia con colores por estado."""
    if not lecturas:
        return ""

    tiempos = [r.timestamp for r in lecturas]
    valores = [r.value_mgdl for r in lecturas]
    colores = [
        C_HIPO if v < RANGO_BAJO else (C_HIPER if v > RANGO_ALTO else C_AZUL)
        for v in valores
    ]

    fig, ax = plt.subplots(figsize=(7.5, 2.5), facecolor="white")

    ax.axhspan(RANGO_BAJO, RANGO_ALTO, color="#22c55e", alpha=0.08)
    ax.axhline(RANGO_BAJO, color=C_HIPO,  lw=1, ls="--", alpha=0.5)
    ax.axhline(RANGO_ALTO, color=C_HIPER, lw=1, ls="--", alpha=0.5)

    # Línea base azul
    ax.plot(tiempos, valores, color=C_AZUL, lw=1.2, alpha=0.6, zorder=1)

    # Puntos coloreados por estado
    for t, v, c in zip(tiempos, valores, colores):
        ax.scatter(t, v, color=c, s=6, zorder=2, linewidths=0)

    ax.set_ylabel("mg/dL", fontsize=8)
    ax.set_title("Glucemia — período analizado", fontsize=10,
                 fontweight="bold", color="#1e293b")
    ax.tick_params(axis="both", labelsize=8)
    ax.set_facecolor("#f8fafc")
    ax.spines[["top", "right"]].set_visible(False)

    # Leyenda compacta
    patches = [
        mpatches.Patch(color=C_RANGO, label="En rango"),
        mpatches.Patch(color=C_HIPO,  label="Hipo <70"),
        mpatches.Patch(color=C_HIPER, label="Hiper >180"),
    ]
    ax.legend(handles=patches, fontsize=7, loc="upper right",
              framealpha=0.8, ncol=3)

    fig.autofmt_xdate(rotation=30, ha="right")
    fig.tight_layout()
    return _b64(fig)
