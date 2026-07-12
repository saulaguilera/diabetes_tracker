"""
utils/claude_analisis.py — Capa 3: Análisis narrativo con Claude API.

Envía a Claude un contexto completo del usuario:
  · Serie glucémica de 48h (bins 15 min, anotada con eventos)
  · Detalle de comidas 48h: hora, macros (CH/grasa/proteína/fibra/GL)
  · Dosis de insulina 48h: tipo, unidades, marca, timing
  · Ejercicio de los últimos 7 días
  · Parámetros personales: ISF, ICR, basal, DIA
  · Snapshot cinético actual: IOB, COB
  · Patrones fisiológicos detectados (Capa 2, 30 días)
  · Métricas globales: TIR, CV%, promedio, hipo/hiper %

Función pública:
    generar_analisis(days=2) → dict con:
        analisis, modelo, tokens, generado_en, error
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from statistics import mean

import anthropic

from utils.patrones_detector import analizar_patrones

# ── Configuración ─────────────────────────────────────────────────────────────
_MODELO       = "claude-haiku-4-5"   # óptimo para análisis de datos estructurados
_MAX_TOKENS   = 1500                  # respuesta más completa
_SERIE_PUNTOS = 192                   # 48h a bins de 15 min


# ── Helpers de formato ─────────────────────────────────────────────────────────

def _resumir_serie(serie: list[dict]) -> str:
    """Serie glucémica compacta: solo HH:MM, valor y eventos."""
    ultimos = serie[-_SERIE_PUNTOS:]
    lineas = []
    for p in ultimos:
        ts = p["ts"][5:16]          # MM-DD HH:MM (incluye fecha para orientar)
        linea = f"{ts}  {p['g']:>3} mg/dL"
        if p.get("comida"):
            linea += f"  🍽 {p['comida']}"
        if p.get("insulina"):
            linea += f"  💉 {p['insulina']}"
        lineas.append(linea)
    return "\n".join(lineas)


def _seccion_comidas(days: int) -> str:
    """Lista detallada de comidas con macros."""
    try:
        from models import Meal
        desde = datetime.now() - timedelta(days=days)
        comidas = (Meal.query
                   .filter(Meal.timestamp >= desde)
                   .order_by(Meal.timestamp)
                   .all())
        if not comidas:
            return "Sin comidas registradas en el período."

        lineas = []
        for c in comidas:
            ts    = c.timestamp.strftime("%d/%m %H:%M")
            ch    = round(c.carbs_g   or 0, 1)
            grasa = round(c.fat_g     or 0, 1)
            prot  = round(c.protein_g or 0, 1)
            fibra = round(c.fiber_g   or 0, 1) if hasattr(c, "fiber_g") else 0
            cal   = round(c.calories  or 0)    if hasattr(c, "calories") else None

            detalle = f"CH:{ch}g  G:{grasa}g  P:{prot}g"
            if fibra > 0:
                detalle += f"  Fib:{fibra}g"
            if cal:
                detalle += f"  ~{cal}kcal"

            # GL de componentes si están disponibles
            gl_total = None
            if c.components:
                try:
                    from utils.nutrition_db import get_gi, gl_from_gi
                    partes_gl = []
                    for comp in c.components:
                        gi = comp.glycemic_index or get_gi(comp.name)
                        gl = gl_from_gi(gi, comp.carbs_g or 0)
                        if gl is not None:
                            partes_gl.append(gl)
                    if partes_gl:
                        gl_total = round(sum(partes_gl), 1)
                except Exception:
                    pass

            if gl_total is not None:
                detalle += f"  GL:{gl_total}"

            lineas.append(f"{ts} | {c.name[:35]} | {detalle}")

        return "\n".join(lineas)
    except Exception as e:
        return f"(error cargando comidas: {e})"


def _seccion_insulina(days: int) -> str:
    """Lista exhaustiva de dosis de insulina del período (registro completo)."""
    try:
        from models import InsulinDose
        desde = datetime.now() - timedelta(days=days)
        dosis = (InsulinDose.query
                 .filter(InsulinDose.timestamp >= desde)
                 .order_by(InsulinDose.timestamp)
                 .all())
        if not dosis:
            return (f"⚠ SIN INYECCIONES en las últimas {days*24}h. "
                    "No asumir bolos ni basales no registrados.")

        n_bolus = sum(1 for d in dosis if d.type == "bolus")
        n_basal = sum(1 for d in dosis if d.type == "basal")
        header  = (f"Total: {len(dosis)} inyecciones registradas "
                   f"({n_bolus} RÁPIDA / bolus + {n_basal} BASAL). "
                   "ESTA LISTA ES COMPLETA — el usuario registra cada inyección.")

        lineas = [header, ""]
        for d in dosis:
            ts = d.timestamp.strftime("%d/%m %H:%M")
            # Tag explícito y diferenciado para evitar confusión
            tipo_label = ("RÁPIDA (bolus)" if d.type == "bolus"
                          else "BASAL"     if d.type == "basal"
                          else d.type.upper())
            marca = f" {d.brand}" if getattr(d, "brand", None) else ""
            purpose = ""
            if d.type == "bolus" and getattr(d, "purpose", None):
                purpose = f" · {d.purpose}"
                if getattr(d, "pre_meal_min", None):
                    purpose += f" ({d.pre_meal_min}min pre-comida)"
            lineas.append(f"{ts} | {tipo_label:<18} {d.units}U{marca}{purpose}")
        return "\n".join(lineas)
    except Exception as e:
        return f"(error cargando insulina: {e})"


def _seccion_ejercicio(days: int = 7) -> str:
    """Sesiones de ejercicio de los últimos N días."""
    try:
        from models import Activity
        desde = datetime.now() - timedelta(days=days)
        acts  = (Activity.query
                 .filter(Activity.timestamp >= desde)
                 .order_by(Activity.timestamp)
                 .all())
        if not acts:
            return "Sin actividad física registrada en los últimos 7 días."

        lineas = []
        for a in acts:
            ts   = a.timestamp.strftime("%d/%m %H:%M")
            name = getattr(a, "name",        "Ejercicio")
            dur  = getattr(a, "duration_min", None)
            inten= getattr(a, "intensity",   None)
            det  = ""
            if dur:
                det += f"  {dur}min"
            if inten:
                det += f"  intensidad:{inten}"
            lineas.append(f"{ts} | {name}{det}")
        return "\n".join(lineas)
    except Exception:
        return "Sin datos de actividad física."


def _seccion_predicciones() -> str:
    """
    Predicciones actuales + últimas resueltas con error real.
    Permite a Claude interpretar el estado proyectado y la precisión del modelo.
    """
    try:
        from models import GlucosePrediction

        # Última predicción registrada (la más reciente)
        ultima = (GlucosePrediction.query
                  .order_by(GlucosePrediction.predicted_at.desc())
                  .first())

        lineas = []

        if ultima:
            ts  = ultima.predicted_at.strftime("%d/%m %H:%M")
            g_a = round(ultima.g_actual) if ultima.g_actual else "?"
            p30 = round(ultima.g_pred_30) if ultima.g_pred_30 else "?"
            p60 = round(ultima.g_pred_60) if ultima.g_pred_60 else "?"
            iob = round(ultima.iob, 1) if ultima.iob else 0
            cob = round(ultima.cob, 1) if ultima.cob else 0
            roc = round(ultima.roc, 2) if ultima.roc else 0
            isf = round(ultima.isf_used, 0) if ultima.isf_used else "?"

            lineas.append(f"Predicción más reciente ({ts}):")
            lineas.append(f"  Glucosa base: {g_a} mg/dL  |  ROC: {roc:+.2f} mg/dL/min")
            lineas.append(f"  → +30 min: {p30} mg/dL")
            lineas.append(f"  → +60 min: {p60} mg/dL")
            lineas.append(f"  Contexto: IOB {iob}U  COB {cob}g  ISF {isf} mg/dL/U")

        # Últimas 10 predicciones resueltas (con error calculado)
        resueltas = (GlucosePrediction.query
                     .filter(GlucosePrediction.error_30 != None)  # noqa: E711
                     .order_by(GlucosePrediction.predicted_at.desc())
                     .limit(10)
                     .all())

        if resueltas:
            errores_30 = [abs(p.error_30) for p in resueltas if p.error_30 is not None]
            errores_60 = [abs(p.error_60) for p in resueltas if p.error_60 is not None]
            mae_30 = round(sum(errores_30) / len(errores_30), 1) if errores_30 else None
            mae_60 = round(sum(errores_60) / len(errores_60), 1) if errores_60 else None

            lineas.append("")
            lineas.append(f"Precisión reciente del modelo (últimas {len(resueltas)} predicciones resueltas):")
            if mae_30:
                cal_30 = "✓ buena" if mae_30 < 15 else "⚠️ moderada" if mae_30 < 25 else "⚠️ alta"
                lineas.append(f"  MAE +30min: ±{mae_30} mg/dL ({cal_30})")
            if mae_60:
                cal_60 = "✓ buena" if mae_60 < 20 else "⚠️ moderada" if mae_60 < 35 else "⚠️ alta"
                lineas.append(f"  MAE +60min: ±{mae_60} mg/dL ({cal_60})")

            lineas.append("")
            lineas.append("Últimas predicciones vs realidad:")
            for p in resueltas[:5]:
                ts = p.predicted_at.strftime("%d/%m %H:%M")
                e30 = f"{p.error_30:+.0f}" if p.error_30 is not None else "?"
                e60 = f"{p.error_60:+.0f}" if p.error_60 is not None else "?"
                lineas.append(
                    f"  {ts}  base:{round(p.g_actual) if p.g_actual else '?'}"
                    f"  pred30:{round(p.g_pred_30) if p.g_pred_30 else '?'}(err:{e30})"
                    f"  pred60:{round(p.g_pred_60) if p.g_pred_60 else '?'}(err:{e60})"
                )

        return "\n".join(lineas) if lineas else "Sin predicciones registradas aún."

    except Exception as e:
        return f"(predicciones no disponibles: {e})"


def _seccion_parametros() -> str:
    """ISF, ICR, basal e IOB/COB actuales."""
    lineas = []
    try:
        from helpers import _calcular_isf_personal, _calcular_icr_personal, _get_setting
        isf, isf_n = _calcular_isf_personal(days=90)
        if isf:
            lineas.append(f"- ISF personal (90d, {isf_n} correcciones): {isf} mg/dL/U")

        icr, icr_n = _calcular_icr_personal(days=90)
        if icr:
            lineas.append(f"- ICR personal (90d, {icr_n} comidas): 1U : {icr}g CH")

        basal = _get_setting("basal_dosis")
        basal_hora = _get_setting("basal_hora")
        basal_tipo = _get_setting("basal_tipo")
        if basal:
            info = f"{basal}U"
            if basal_tipo:
                info += f" {basal_tipo}"
            if basal_hora:
                info += f" a las {basal_hora}"
            lineas.append(f"- Basal configurada: {info}")

        # ISF circadiano activo en este momento
        try:
            from helpers import _calcular_isf_circadiano, _isf_para_hora, _calcular_isf_personal
            isf_circ = _calcular_isf_circadiano(days=90)
            isf_g, _ = _calcular_isf_personal(days=90)
            isf_ahora, bloque, fuente = _isf_para_hora(datetime.now().hour, isf_circ, isf_g)
            if isf_ahora and fuente == "circadiano":
                lineas.append(f"- ISF circadiano ahora ({bloque}): {isf_ahora} mg/dL/U")
        except Exception:
            pass

    except Exception as e:
        lineas.append(f"(parámetros no disponibles: {e})")

    # IOB / COB actual
    try:
        from utils.kinetics import get_kinetics_snapshot
        snap = get_kinetics_snapshot()
        if snap:
            iob = snap.get("iob", 0)
            cob = snap.get("cob", 0)
            ef  = snap.get("exercise_factor", 1.0)
            if iob > 0.05:
                lineas.append(f"- IOB actual: {iob:.1f} U insulina activa")
            if cob > 1:
                lineas.append(f"- COB actual: {cob:.0f}g carbohidratos absorbiendo")
            if ef and abs(ef - 1.0) >= 0.05:
                pct = round((ef - 1.0) * 100)
                signo = "+" if pct > 0 else ""
                lineas.append(f"- Factor ejercicio: {signo}{pct}% sensibilidad a insulina")
    except Exception:
        pass

    return "\n".join(lineas) if lineas else "Sin parámetros personales configurados."


def _formatear_patrones(patrones: list[dict]) -> str:
    if not patrones:
        return "No se detectaron patrones de riesgo recurrentes en los últimos 30 días."
    lineas = []
    for p in patrones:
        nivel_emoji = "🔴" if p["nivel"] == "danger" else "🟡"
        lineas.append(f"{nivel_emoji} **{p['titulo']}** — {p['detalle']}")
    return "\n".join(lineas)


def _seccion_metricas(resumen: dict, days: int) -> str:
    if not resumen.get("n_lecturas"):
        return "Sin datos de glucosa."
    partes = [
        f"- Lecturas: {resumen['n_lecturas']} (últimos {days} días)",
    ]
    if resumen.get("avg"):
        partes.append(f"- Promedio: {resumen['avg']} mg/dL")
    if resumen.get("sd"):
        partes.append(f"- DE: {resumen['sd']} mg/dL")
    if resumen.get("cv"):
        flag = "⚠️ elevado (meta <36%)" if resumen["cv"] > 36 else "✓ OK"
        partes.append(f"- CV%: {resumen['cv']}% {flag}")
    if resumen.get("tir") is not None:
        flag = "✓ meta" if resumen["tir"] >= 70 else "⚠️ bajo objetivo (≥70%)"
        partes.append(f"- TIR 70–180: {resumen['tir']}% {flag}")
    if resumen.get("hipo_pct") is not None:
        flag = "⚠️ alto" if resumen["hipo_pct"] > 4 else ""
        partes.append(f"- Tiempo <70: {resumen['hipo_pct']}% {flag}")
    if resumen.get("hiper_pct") is not None:
        partes.append(f"- Tiempo >180: {resumen['hiper_pct']}%")
    return "\n".join(partes)


# ── Prompt ─────────────────────────────────────────────────────────────────────

def _construir_prompt(datos: dict, days: int) -> tuple[str, str]:
    resumen  = datos["resumen"]
    patrones = datos["patrones"]
    serie    = datos["serie_glucose"]

    system = (
        "Sos un asistente especializado en análisis de datos de diabetes tipo 1. "
        "Tienes acceso a la serie glucémica completa, comidas con macros, dosis de insulina, "
        "ejercicio y parámetros personales del usuario. "
        "Tu objetivo es encontrar conexiones entre todas estas variables y explicarlas "
        "en lenguaje claro y empático.\n\n"
        "REGLA DE COMPLETITUD DE DATOS (la más importante):\n"
        "- Los datos provistos son COMPLETOS Y EXHAUSTIVOS dentro del período. "
        "El usuario registra TODAS sus inyecciones, TODAS sus comidas y TODAS sus actividades.\n"
        "- Si un bolo de insulina NO aparece listado en la sección 'Insulina' o anotado en la "
        "'Serie glucémica', ESE BOLO NO EXISTIÓ. No lo inventes. No digas frases como "
        "'tu bolo antes de comer cubrió bien' si no hay un bolo registrado pre-comida.\n"
        "- Si la glucosa cayó o se controló SIN un bolo registrado en la ventana, la causa "
        "es OTRA: IOB residual de bolos anteriores, sensibilidad post-ejercicio, basal con "
        "efecto, o absorción lenta de la comida. Nunca atribuyas el efecto a un bolo "
        "que no figura en los datos.\n"
        "- Distingue siempre RÁPIDA (bolus, acción 15min-4h) de BASAL (acción prolongada, "
        "12-42h sin pico) por el tag explícito en cada registro. NUNCA llames a una basal "
        "'bolo' ni viceversa.\n"
        "- Si encuentras un patrón sin causa obvia en los datos, di 'no es explicable solo "
        "con los datos disponibles' en lugar de inventar una causa.\n\n"
        "Reglas generales:\n"
        "- Responde siempre en español, segunda persona (vos/te).\n"
        "- Cruza activamente los datos: relaciona picos de glucosa con comidas, "
        "hipos con ejercicio o insulina, subidas nocturnas con la basal, etc.\n"
        "- Basate SOLO en los datos provistos. No inventes información.\n"
        "- Nunca indiques dosis concretas a cambiar; sugiere consultar al médico.\n"
        "- Usa markdown: ## encabezados, **negritas**, listas con -.\n"
        "- Máximo 500 palabras. Sé preciso y accionable.\n\n"
        "Reglas clínicas CRÍTICAS — nunca las violes:\n"
        "- HIPOGLUCEMIA se define como glucosa < 70 mg/dL. Valores entre 70–100 mg/dL son "
        "bajos-normales pero NO son hipoglucemia. No uses la palabra 'hipoglucemia' para "
        "valores ≥ 70 mg/dL.\n"
        "- TRATAMIENTO DE HIPO ACTIVA: únicamente carbohidratos de absorción rápida "
        "(glucosa, jugo, azúcar, caramelos). NUNCA sugieras agregar grasas, proteínas "
        "(queso, nueces, leche entera) durante una hipo activa — la grasa enlentece la "
        "absorción y prolonga la hipoglucemia. Las grasas/proteínas van DESPUÉS de que la "
        "glucosa ya superó los 100 mg/dL para estabilizar.\n"
        "- EJERCICIO AERÓBICO + HIPOGLUCEMIA: nunca sugieras bolos de insulina antes, "
        "durante o después del ejercicio cuando hay patrón de hipos. La solución es "
        "carbohidratos sin insulina antes del ejercicio, reducir el bolo de la comida previa, "
        "o ajustar la basal con el médico.\n"
        "- BASAL vs BOLUS: son cosas distintas. 'Basal' es la insulina de acción prolongada "
        "(Toujeo, Tresiba, Lantus). 'Bolus' es la insulina rápida para comidas o corrección. "
        "No uses 'bolus temporal' para referirte a ajustar la basal. Sé preciso con los términos.\n"
        "- IOB (insulina activa): si mencionas un valor de IOB, explica brevemente de dónde "
        "surge. No pongas números sin contexto.\n"
        "- Si la glucosa está cayendo (tendencia negativa), nunca sugieras insulina.\n"
        "- HIPOGLUCEMIA: glucosa < 70 mg/dL ES hipoglucemia. No uses términos como 'rozó', "
        "'casi', 'bordeó' para valores < 70. Si es < 70, di directamente 'hipoglucemia'.\n"
        "- CAUSALIDAD en hipos post-comida: si el usuario tuvo hipo después de una comida "
        "con ejercicio previo, la causa es EXCESO de insulina activa (IOB + sensibilidad "
        "post-ejercicio aumentada), NUNCA 'bolo insuficiente'. Nunca sugieras que faltó "
        "insulina cuando el resultado fue hipoglucemia.\n"
        "- FARMACOLOGÍA DE INSULINA BASAL: Toujeo, Tresiba, Lantus y Levemir tienen onset "
        "de 4–6 horas y perfil plano sin pico significativo. NO digas que una basal "
        "inyectada hará efecto en 2–3 horas. Las insulinas rápidas (Novorapid, Humalog, "
        "Fiasp) sí actúan en 15–30 min. Distinguilas siempre.\n"
        "- Ante la duda sobre cualquier recomendación relacionada con insulina, omitila "
        "y remitila a consulta médica."
    )

    user = f"""Analiza mis datos de las últimas {days*24}h y dime qué encuentras.

## Métricas globales (últimos 30 días)
{_seccion_metricas(resumen, 30)}

## Mis parámetros personales
{_seccion_parametros()}

## Predicciones del modelo y su precisión
```
{_seccion_predicciones()}
```

## Patrones detectados (últimos 30 días)
{_formatear_patrones(patrones)}

## Comidas (últimas {days*24}h)
```
{_seccion_comidas(days)}
```

## Insulina (últimas {days*24}h)
```
{_seccion_insulina(days)}
```

## Ejercicio (últimos 7 días)
```
{_seccion_ejercicio(7)}
```

## Serie glucémica (últimas {days*24}h — MM-DD HH:MM · glucosa · eventos)
```
{_resumir_serie(serie)}
```

Por favor, estructurá tu respuesta así:
1. **Panorama general** — qué está pasando con mi glucosa ahora mismo.
2. **Conexiones clave** — cruza la serie con comidas, insulina y ejercicio: ¿qué causa los picos o caídas que ves?
3. **Interpretación de predicciones** — ¿el modelo está prediciendo bien? ¿qué dice sobre lo que viene? ¿hay algo para estar atento?
4. **Patrones importantes** — de los detectados, ¿cuáles son los que más me afectan?
5. **3 sugerencias concretas** para llevar a mi próxima consulta médica.
"""

    return system, user


# ── Función pública ───────────────────────────────────────────────────────────

def generar_analisis(days: int = 2) -> dict:
    """
    Genera un análisis narrativo completo con Claude API.

    Args:
        days: Días de serie glucémica, comidas e insulina a enviar (default 2 = 48h).
              Siempre usa 30 días para métricas globales y detección de patrones.

    Returns:
        dict: analisis (str Markdown), modelo, tokens, generado_en, error
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return _error("ANTHROPIC_API_KEY no configurada.")

    try:
        # Patrones y serie (30 días para patrones, `days` para serie)
        datos = analizar_patrones(days=30)

        # Reemplazar serie con la del período solicitado si es distinto
        if days != 30:
            from utils.patrones_detector import analizar_patrones as _ap
            datos["serie_glucose"] = _ap(days=days)["serie_glucose"]

        system_prompt, user_message = _construir_prompt(datos, days)

        cliente   = anthropic.Anthropic(api_key=api_key)
        respuesta = cliente.messages.create(
            model=_MODELO,
            max_tokens=_MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        texto  = respuesta.content[0].text if respuesta.content else ""
        tokens = {
            "input":  respuesta.usage.input_tokens,
            "output": respuesta.usage.output_tokens,
        }
        return {
            "analisis":    texto,
            "modelo":      respuesta.model,
            "tokens":      tokens,
            "generado_en": datetime.now().isoformat(),
            "error":       None,
        }

    except anthropic.AuthenticationError:
        return _error("API key inválida. Verifica ANTHROPIC_API_KEY.")
    except anthropic.RateLimitError:
        return _error("Límite de rate alcanzado. Intenta en unos minutos.")
    except Exception as e:
        return _error(str(e))


def _error(msg: str) -> dict:
    return {
        "analisis":    None,
        "modelo":      None,
        "tokens":      None,
        "generado_en": datetime.now().isoformat(),
        "error":       msg,
    }
