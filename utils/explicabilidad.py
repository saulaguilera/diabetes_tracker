"""
utils/explicabilidad.py — Capa explicativa del modelo predictivo.

Convierte los componentes numéricos de cada predicción en texto
legible que el usuario puede entender sin conocer el modelo.

Funciones públicas:
    explicar_prediccion(g_actual, g_pred, componentes, iob, cob,
                        roc, ex_factor, isf, icr, delta_min)
        → dict con: titulo, factores[], alerta, emoji
"""

from __future__ import annotations
from typing import Optional


# ── Umbrales clínicos ────────────────────────────────────────────────────────
_HIPO_UMBRAL    = 70    # mg/dL
_HIPER_UMBRAL   = 180   # mg/dL
_ROC_RAPIDO_SUB = -2.0  # mg/dL/min — caída rápida
_ROC_RAPIDO_BAJ =  2.0  # mg/dL/min — subida rápida
_ROC_MODERADO   =  0.8  # mg/dL/min — cambio moderado


def _signo(val: float) -> str:
    return "+" if val >= 0 else "−"


def _mg(val: float) -> str:
    """Formatea mg/dL con signo."""
    return f"{_signo(val)}{abs(round(val))} mg/dL"


def explicar_prediccion(
    g_actual:    float,
    g_pred:      float,
    componentes: dict,
    iob:         float,
    cob:         float,
    roc:         Optional[float],
    ex_factor:   float,
    isf:         Optional[float],
    icr:         Optional[float],
    delta_min:   int,
) -> dict:
    """
    Genera una explicación legible de por qué el modelo predice g_pred
    partiendo de g_actual en delta_min minutos.

    Retorna:
        titulo   : str  — resumen de una línea con emoji
        factores : list — lista de factores con signo e impacto
        alerta   : str|None — mensaje de alerta si aplica
        tendencia: str  — "sube" | "baja" | "estable"
    """
    delta_g = g_pred - g_actual
    roc_ef  = componentes.get("roc_effect", 0)
    ins_ef  = componentes.get("insulin_effect", 0)   # negativo = baja glucosa
    carb_ef = componentes.get("carb_effect", 0)
    dawn_ef = componentes.get("dawn_effect", 0)

    factores = []

    # ── Tendencia del CGM (ROC) ──────────────────────────────────────────────
    if roc is not None and abs(roc_ef) >= 2:
        if roc > _ROC_RAPIDO_BAJ:
            factores.append({
                "icono": "📈",
                "texto": f"Tendencia del sensor: subiendo rápido ({roc:+.1f} mg/dL/min)",
                "impacto": _mg(roc_ef),
                "signo": "sube",
            })
        elif roc < _ROC_RAPIDO_SUB:
            factores.append({
                "icono": "📉",
                "texto": f"Tendencia del sensor: cayendo rápido ({roc:+.1f} mg/dL/min)",
                "impacto": _mg(roc_ef),
                "signo": "baja",
            })
        elif roc > _ROC_MODERADO:
            factores.append({
                "icono": "↗",
                "texto": f"Tendencia moderada al alza ({roc:+.1f} mg/dL/min)",
                "impacto": _mg(roc_ef),
                "signo": "sube",
            })
        elif roc < -_ROC_MODERADO:
            factores.append({
                "icono": "↘",
                "texto": f"Tendencia moderada a la baja ({roc:+.1f} mg/dL/min)",
                "impacto": _mg(roc_ef),
                "signo": "baja",
            })

    # ── Insulina activa (IOB) ────────────────────────────────────────────────
    if ins_ef and abs(ins_ef) >= 2:
        iob_label = f"{iob:.1f} U" if iob else "—"
        factores.append({
            "icono": "💉",
            "texto": f"Insulina activa: {iob_label} IOB",
            "impacto": _mg(-ins_ef),   # ins_ef positivo = baja glucosa → mostrar como negativo
            "signo": "baja",
        })

    # ── Carbohidratos activos (COB) ──────────────────────────────────────────
    if carb_ef and carb_ef >= 2:
        cob_label = f"{cob:.0f}g" if cob else "—"
        factores.append({
            "icono": "🍽",
            "texto": f"Carbohidratos activos: {cob_label} CH absorbiendo",
            "impacto": _mg(carb_ef),
            "signo": "sube",
        })
    elif cob < 2:
        factores.append({
            "icono": "✓",
            "texto": "Sin carbohidratos activos",
            "impacto": "0 mg/dL",
            "signo": "neutro",
        })

    # ── Ejercicio ────────────────────────────────────────────────────────────
    if ex_factor and abs(ex_factor - 1.0) >= 0.05:
        pct = round((ex_factor - 1.0) * 100)
        if ex_factor > 1.0:
            factores.append({
                "icono": "🏃",
                "texto": f"Ejercicio reciente: +{pct}% sensibilidad a la insulina",
                "impacto": "baja más",
                "signo": "baja",
            })
        else:
            factores.append({
                "icono": "⚡",
                "texto": f"Ejercicio anaeróbico agudo: {pct}% menos sensibilidad",
                "impacto": "baja menos",
                "signo": "sube",
            })

    # ── Fenómeno del alba ────────────────────────────────────────────────────
    if dawn_ef and dawn_ef >= 2:
        factores.append({
            "icono": "🌅",
            "texto": f"Fenómeno del alba (cortisol/GH nocturno)",
            "impacto": _mg(dawn_ef),
            "signo": "sube",
        })

    # ── Título y tendencia ───────────────────────────────────────────────────
    abs_delta = abs(delta_g)

    if abs_delta < 5:
        tendencia = "estable"
        emoji     = "→"
        titulo    = f"Glucosa estable en {delta_min} min (~{round(g_pred)} mg/dL)"
    elif delta_g > 0:
        tendencia = "sube"
        if delta_g >= 30:
            emoji = "⬆"
        else:
            emoji = "↗"
        titulo = f"Subirá ~{round(abs_delta)} mg/dL en {delta_min} min → {round(g_pred)} mg/dL"
    else:
        tendencia = "baja"
        if abs_delta >= 30:
            emoji = "⬇"
        else:
            emoji = "↘"
        titulo = f"Bajará ~{round(abs_delta)} mg/dL en {delta_min} min → {round(g_pred)} mg/dL"

    # ── Alertas clínicas ─────────────────────────────────────────────────────
    alerta = None

    if g_pred < _HIPO_UMBRAL:
        if roc is not None and roc < _ROC_RAPIDO_SUB:
            alerta = f"⚠️ Hipoglucemia inminente — caída rápida ({roc:.1f} mg/dL/min) y glucosa proyectada {round(g_pred)} mg/dL. Considerá tomar carbohidratos ahora."
        else:
            alerta = f"⚠️ Glucosa proyectada por debajo de rango ({round(g_pred)} mg/dL). Revisá en {delta_min} minutos."
    elif g_pred < 80 and roc is not None and roc < -1.0:
        alerta = f"⚠️ Tendencia a la baja con glucosa cercana al límite — monitoreá de cerca."
    elif g_pred > _HIPER_UMBRAL and cob > 10 and iob < 0.5:
        alerta = f"⚠️ {round(cob)}g de carbohidratos activos con poco IOB — considerá corrección."
    elif roc is not None and roc < _ROC_RAPIDO_SUB and iob > 2:
        alerta = f"⚠️ Caída rápida con {iob:.1f} U de insulina activa — riesgo de hipoglucemia."

    # Si no hay factores relevantes
    if not factores:
        factores.append({
            "icono": "≈",
            "texto": "Glucosa estable sin factores activos significativos",
            "impacto": "0 mg/dL",
            "signo": "neutro",
        })

    return {
        "titulo":    titulo,
        "emoji":     emoji,
        "tendencia": tendencia,
        "factores":  factores,
        "alerta":    alerta,
        "delta_g":   round(delta_g, 1),
        "g_pred":    round(g_pred),
    }
