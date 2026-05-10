"""Generadores de gráficas Plotly para la app de diabetes."""

import json
from datetime import datetime, timedelta
import plotly
import plotly.graph_objects as go
import plotly.express as px
from sqlalchemy import func


# Rangos objetivo (ADA / estándar T1D)
RANGO_BAJO = 70
RANGO_ALTO = 180
COLOR_HIPO = "#ef4444"
COLOR_RANGO = "#22c55e"
COLOR_HIPER = "#f97316"
COLOR_GLUCOSA = "#3b82f6"


def _to_json(fig) -> dict:
    """Serializa figura Plotly a dict JSON-safe."""
    return json.loads(plotly.io.to_json(fig))


def _get_readings(hours=168):
    """Obtiene lecturas de glucosa del período dado."""
    from models import GlucoseReading
    desde = datetime.utcnow() - timedelta(hours=hours)
    return (
        GlucoseReading.query
        .filter(GlucoseReading.timestamp >= desde)
        .order_by(GlucoseReading.timestamp.asc())
        .all()
    )


def chart_glucose_timeline(hours=24) -> dict:
    """Línea de tiempo de glucemia con bandas de rango objetivo."""
    readings = _get_readings(hours)

    if not readings:
        fig = go.Figure()
        fig.update_layout(
            title="Sin datos de glucemia",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        return _to_json(fig)

    tiempos = [r.timestamp for r in readings]
    valores = [r.value_mgdl for r in readings]
    fuentes = [r.source for r in readings]

    # Colores por estado
    colores = []
    for v in valores:
        if v < RANGO_BAJO:
            colores.append(COLOR_HIPO)
        elif v > RANGO_ALTO:
            colores.append(COLOR_HIPER)
        else:
            colores.append(COLOR_GLUCOSA)

    fig = go.Figure()

    # Banda zona objetivo
    fig.add_hrect(
        y0=RANGO_BAJO, y1=RANGO_ALTO,
        fillcolor="rgba(34,197,94,0.1)",
        line_width=0,
        annotation_text="Rango objetivo",
        annotation_position="top left",
        annotation_font_size=11,
        annotation_font_color=COLOR_RANGO,
    )

    # Líneas de límite
    fig.add_hline(y=RANGO_BAJO, line_dash="dot", line_color=COLOR_HIPO, line_width=1)
    fig.add_hline(y=RANGO_ALTO, line_dash="dot", line_color=COLOR_HIPER, line_width=1)

    # Línea principal
    fig.add_trace(go.Scatter(
        x=tiempos,
        y=valores,
        mode="lines+markers",
        name="Glucemia",
        line=dict(color=COLOR_GLUCOSA, width=2),
        marker=dict(
            color=colores,
            size=5,
            line=dict(width=0),
        ),
        hovertemplate="<b>%{y:.0f} mg/dL</b><br>%{x|%d/%m %H:%M}<extra></extra>",
    ))

    fig.update_layout(
        title=f"Glucemia — últimas {hours}h",
        xaxis_title="Fecha/Hora",
        yaxis_title="mg/dL",
        yaxis=dict(range=[max(0, min(valores) - 20), max(valores) + 20]),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(248,250,252,1)",
        font=dict(family="Inter, system-ui, sans-serif", size=12),
        hovermode="x unified",
        margin=dict(l=50, r=20, t=50, b=50),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )

    return _to_json(fig)


def chart_time_in_range(days=30) -> dict:
    """Gráfica de pastel: tiempo en rango, hipoglucemia e hiperglucemia."""
    readings = _get_readings(hours=days * 24)

    if not readings:
        return _to_json(go.Figure())

    valores = [r.value_mgdl for r in readings]
    n = len(valores)
    hipo = len([v for v in valores if v < RANGO_BAJO])
    rango = len([v for v in valores if RANGO_BAJO <= v <= RANGO_ALTO])
    hiper = len([v for v in valores if v > RANGO_ALTO])

    fig = go.Figure(go.Pie(
        labels=["En rango (70–180)", "Hipoglucemia (<70)", "Hiperglucemia (>180)"],
        values=[rango, hipo, hiper],
        marker_colors=[COLOR_RANGO, COLOR_HIPO, COLOR_HIPER],
        hole=0.5,
        textinfo="label+percent",
        hovertemplate="<b>%{label}</b><br>%{value} lecturas (%{percent})<extra></extra>",
    ))

    fig.update_layout(
        title=f"Tiempo en rango — últimos {days} días",
        annotations=[dict(
            text=f"{round(rango/n*100, 1)}%<br>en rango",
            x=0.5, y=0.5,
            font_size=16,
            showarrow=False,
        )],
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, system-ui, sans-serif", size=12),
        margin=dict(l=20, r=20, t=50, b=20),
    )

    return _to_json(fig)


def chart_glucose_by_hour(days=30) -> dict:
    """Glucemia promedio por hora del día (patrón circadiano)."""
    readings = _get_readings(hours=days * 24)

    if not readings:
        return _to_json(go.Figure())

    from collections import defaultdict
    por_hora = defaultdict(list)
    for r in readings:
        hora = r.timestamp.hour
        por_hora[hora].append(r.value_mgdl)

    horas = list(range(24))
    promedios = [
        round(sum(por_hora[h]) / len(por_hora[h]), 1) if por_hora[h] else None
        for h in horas
    ]

    # Filtrar solo horas con datos para la banda min-max (evita None en la banda)
    horas_con_datos = [h for h in horas if por_hora[h]]
    if not horas_con_datos:
        return _to_json(go.Figure())

    etiquetas_banda = [f"{h:02d}:00" for h in horas_con_datos]
    maximos_banda   = [max(por_hora[h]) for h in horas_con_datos]
    minimos_banda   = [min(por_hora[h]) for h in horas_con_datos]

    etiquetas = [f"{h:02d}:00" for h in horas]

    fig = go.Figure()

    # Banda min-max (solo horas con datos, sin None)
    fig.add_trace(go.Scatter(
        x=etiquetas_banda + etiquetas_banda[::-1],
        y=maximos_banda + minimos_banda[::-1],
        fill="toself",
        fillcolor="rgba(59,130,246,0.15)",
        line=dict(color="rgba(0,0,0,0)"),
        name="Rango min-max",
        hoverinfo="skip",
    ))

    # Banda rango objetivo
    fig.add_hrect(y0=RANGO_BAJO, y1=RANGO_ALTO,
                  fillcolor="rgba(34,197,94,0.1)", line_width=0)
    fig.add_hline(y=RANGO_BAJO, line_dash="dot", line_color=COLOR_HIPO, line_width=1)
    fig.add_hline(y=RANGO_ALTO, line_dash="dot", line_color=COLOR_HIPER, line_width=1)

    fig.add_trace(go.Scatter(
        x=etiquetas,
        y=promedios,
        mode="lines+markers",
        name="Promedio",
        line=dict(color=COLOR_GLUCOSA, width=2.5),
        marker=dict(size=6),
        hovertemplate="<b>%{x}</b><br>Promedio: %{y:.0f} mg/dL<extra></extra>",
    ))

    fig.update_layout(
        title=f"Patrón circadiano — últimos {days} días",
        xaxis_title="Hora del día",
        yaxis_title="mg/dL",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(248,250,252,1)",
        font=dict(family="Inter, system-ui, sans-serif", size=12),
        hovermode="x unified",
        margin=dict(l=50, r=20, t=50, b=50),
    )

    return _to_json(fig)


def chart_meal_impact(days=30) -> dict:
    """
    Muestra glucemia antes y después de cada comida registrada.
    Busca la lectura más cercana ±30 min antes y la máxima en las 2h post-comida.
    """
    from models import Meal, GlucoseReading

    desde = datetime.utcnow() - timedelta(days=days)
    comidas = Meal.query.filter(Meal.timestamp >= desde).order_by(Meal.timestamp).all()

    if not comidas:
        return _to_json(go.Figure())

    nombres = []
    carbs_vals = []
    pre_vals = []
    post_vals = []
    deltas = []
    timestamps = []

    for comida in comidas:
        ventana_pre = timedelta(minutes=30)
        ventana_post = timedelta(hours=2)

        pre = (
            GlucoseReading.query
            .filter(
                GlucoseReading.timestamp >= comida.timestamp - ventana_pre,
                GlucoseReading.timestamp <= comida.timestamp,
            )
            .order_by(GlucoseReading.timestamp.desc())
            .first()
        )

        post_lecturas = (
            GlucoseReading.query
            .filter(
                GlucoseReading.timestamp > comida.timestamp,
                GlucoseReading.timestamp <= comida.timestamp + ventana_post,
            )
            .all()
        )

        if not pre or not post_lecturas:
            continue

        post_max = max(r.value_mgdl for r in post_lecturas)

        nombres.append(comida.name[:30])
        carbs_vals.append(comida.carbs_g)
        pre_vals.append(pre.value_mgdl)
        post_vals.append(post_max)
        deltas.append(round(post_max - pre.value_mgdl, 1))
        timestamps.append(comida.timestamp.strftime("%d/%m %H:%M"))

    if not nombres:
        fig = go.Figure()
        fig.update_layout(title="Sin datos suficientes (necesita lecturas de glucemia cerca de las comidas)")
        return _to_json(fig)

    fig = go.Figure()

    fig.add_trace(go.Bar(
        name="Pre-comida",
        x=timestamps,
        y=pre_vals,
        marker_color="rgba(59,130,246,0.7)",
        hovertemplate="<b>Pre:</b> %{y:.0f} mg/dL<extra></extra>",
    ))

    fig.add_trace(go.Bar(
        name="Post-comida (pico)",
        x=timestamps,
        y=post_vals,
        marker_color="rgba(249,115,22,0.7)",
        hovertemplate="<b>Post:</b> %{y:.0f} mg/dL<extra></extra>",
    ))

    # Anotaciones con carbohidratos
    for i, (ts, c, d) in enumerate(zip(timestamps, carbs_vals, deltas)):
        if d > 0:
            color = COLOR_HIPER if d > 80 else COLOR_GLUCOSA
        else:
            color = COLOR_RANGO
        fig.add_annotation(
            x=ts, y=post_vals[i] + 5,
            text=f"{c}g CH<br>Δ{d:+.0f}",
            showarrow=False,
            font=dict(size=9, color=color),
            align="center",
        )

    fig.add_hline(y=RANGO_BAJO, line_dash="dot", line_color=COLOR_HIPO, line_width=1)
    fig.add_hline(y=RANGO_ALTO, line_dash="dot", line_color=COLOR_HIPER, line_width=1)

    fig.update_layout(
        title=f"Impacto de comidas en glucemia — últimos {days} días",
        xaxis_title="Comida",
        yaxis_title="mg/dL",
        barmode="group",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(248,250,252,1)",
        font=dict(family="Inter, system-ui, sans-serif", size=11),
        xaxis_tickangle=-35,
        margin=dict(l=50, r=20, t=50, b=80),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )

    return _to_json(fig)


def chart_timeline_eventos(hours=168) -> dict:
    """Timeline completa: glucosa + marcadores de comidas, insulina y ejercicio."""
    from models import GlucoseReading, Meal, InsulinDose, Activity

    desde = datetime.utcnow() - timedelta(hours=hours)
    readings = _get_readings(hours=hours)

    if not readings:
        return _to_json(go.Figure())

    tiempos = [r.timestamp for r in readings]
    valores = [r.value_mgdl for r in readings]
    colores = [
        COLOR_HIPO if v < RANGO_BAJO else (COLOR_HIPER if v > RANGO_ALTO else COLOR_GLUCOSA)
        for v in valores
    ]

    fig = go.Figure()

    fig.add_hrect(y0=RANGO_BAJO, y1=RANGO_ALTO,
                  fillcolor="rgba(34,197,94,0.1)", line_width=0)
    fig.add_hline(y=RANGO_BAJO, line_dash="dot", line_color=COLOR_HIPO, line_width=1)
    fig.add_hline(y=RANGO_ALTO, line_dash="dot", line_color=COLOR_HIPER, line_width=1)

    fig.add_trace(go.Scatter(
        x=tiempos, y=valores,
        mode="lines+markers",
        name="Glucemia",
        line=dict(color=COLOR_GLUCOSA, width=2),
        marker=dict(color=colores, size=4),
        hovertemplate="<b>%{y:.0f} mg/dL</b><br>%{x|%d/%m %H:%M}<extra></extra>",
    ))

    ymin = max(0, min(valores) - 50)
    ymax = max(valores) + 20

    # Marcadores de comidas (triángulo arriba)
    comidas = Meal.query.filter(Meal.timestamp >= desde).all()
    if comidas:
        fig.add_trace(go.Scatter(
            x=[c.timestamp for c in comidas],
            y=[ymin + 8] * len(comidas),
            mode="markers",
            name="Comida",
            marker=dict(symbol="triangle-up", size=12, color="#f59e0b",
                        line=dict(width=1, color="white")),
            customdata=[[c.name[:25], c.carbs_g] for c in comidas],
            hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}g CH<br>%{x|%d/%m %H:%M}<extra></extra>",
        ))

    # Marcadores de insulina (triángulo abajo)
    insulinas = InsulinDose.query.filter(InsulinDose.timestamp >= desde).all()
    if insulinas:
        fig.add_trace(go.Scatter(
            x=[i.timestamp for i in insulinas],
            y=[ymin + 20] * len(insulinas),
            mode="markers",
            name="Insulina",
            marker=dict(symbol="triangle-down", size=12, color="#8b5cf6",
                        line=dict(width=1, color="white")),
            customdata=[[i.type, i.units] for i in insulinas],
            hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}U<br>%{x|%d/%m %H:%M}<extra></extra>",
        ))

    # Marcadores de actividad (diamante, color por intensidad)
    _act_colors = {"baja": "#22c55e", "media": "#eab308", "alta": "#ef4444"}
    actividades = Activity.query.filter(Activity.timestamp >= desde).all()
    if actividades:
        fig.add_trace(go.Scatter(
            x=[a.timestamp for a in actividades],
            y=[ymin + 34] * len(actividades),
            mode="markers",
            name="Ejercicio",
            marker=dict(
                symbol="diamond",
                size=12,
                color=[_act_colors.get(a.intensity, "#6b7280") for a in actividades],
                line=dict(width=1, color="white"),
            ),
            customdata=[[a.activity_type[:20], a.duration_min or 0, a.intensity or ""] for a in actividades],
            hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]} min — %{customdata[2]}<br>%{x|%d/%m %H:%M}<extra></extra>",
        ))

    fig.update_layout(
        title=f"Línea de tiempo completa — últimas {hours}h",
        xaxis_title="Fecha/Hora",
        yaxis_title="mg/dL",
        yaxis=dict(range=[ymin, ymax]),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(248,250,252,1)",
        font=dict(family="Inter, system-ui, sans-serif", size=12),
        hovermode="x unified",
        margin=dict(l=50, r=20, t=50, b=50),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )

    return _to_json(fig)


def chart_activity_glucose_impact(days=30) -> dict:
    """
    Glucemia antes y después del ejercicio.
    Líneas individuales tenues + línea de promedio gruesa por intensidad.
    """
    from models import Activity, GlucoseReading
    from collections import defaultdict

    desde = datetime.utcnow() - timedelta(days=days)
    actividades = Activity.query.filter(Activity.timestamp >= desde).all()

    if not actividades:
        return _to_json(go.Figure())

    # Colores por intensidad: tenue para individuales, sólido para promedio
    _cfg = {
        "baja":  {"rgba": "rgba(34,197,94,0.18)",  "solid": "#22c55e", "label": "Baja intensidad"},
        "media": {"rgba": "rgba(234,179,8,0.20)",   "solid": "#d97706", "label": "Media intensidad"},
        "alta":  {"rgba": "rgba(239,68,68,0.18)",   "solid": "#dc2626", "label": "Alta intensidad"},
    }

    # Buckets de 15 min de -60 a +240
    BUCKETS = list(range(-60, 241, 15))

    sessions_by_intensity: dict = {"baja": [], "media": [], "alta": []}

    for act in actividades:
        lecturas = (
            GlucoseReading.query
            .filter(
                GlucoseReading.timestamp >= act.timestamp - timedelta(hours=1),
                GlucoseReading.timestamp <= act.timestamp + timedelta(hours=4),
            )
            .order_by(GlucoseReading.timestamp)
            .all()
        )
        if len(lecturas) < 3:
            continue
        minutos = [(r.timestamp - act.timestamp).total_seconds() / 60 for r in lecturas]
        valores = [r.value_mgdl for r in lecturas]
        intensity = act.intensity or "media"
        sessions_by_intensity[intensity].append({
            "minutos": minutos,
            "valores": valores,
            "act": act,
        })

    total = sum(len(v) for v in sessions_by_intensity.values())
    if total == 0:
        fig = go.Figure()
        fig.update_layout(
            title="Sin datos suficientes (necesita lecturas de glucemia cerca de las actividades)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        return _to_json(fig)

    fig = go.Figure()

    fig.add_hrect(y0=RANGO_BAJO, y1=RANGO_ALTO,
                  fillcolor="rgba(34,197,94,0.08)", line_width=0)
    fig.add_hline(y=RANGO_BAJO, line_dash="dot", line_color=COLOR_HIPO, line_width=1)
    fig.add_hline(y=RANGO_ALTO, line_dash="dot", line_color=COLOR_HIPER, line_width=1)
    fig.add_vline(x=0, line_dash="dash", line_color="rgba(80,80,80,0.4)",
                  annotation_text="Inicio", annotation_position="top right",
                  annotation_font_size=10)

    for intensity, sessions in sessions_by_intensity.items():
        if not sessions:
            continue
        cfg = _cfg[intensity]

        # --- Líneas individuales tenues (sin leyenda) ---
        for s in sessions:
            act = s["act"]
            fig.add_trace(go.Scatter(
                x=s["minutos"],
                y=s["valores"],
                mode="lines",
                name=cfg["label"],
                legendgroup=intensity,
                showlegend=False,
                line=dict(color=cfg["rgba"], width=1),
                hovertemplate=(
                    f"<b>{act.activity_type[:22]}</b><br>"
                    f"{act.timestamp.strftime('%d/%m %H:%M')}<br>"
                    "%{x:.0f} min → %{y:.0f} mg/dL<extra></extra>"
                ),
            ))

        # --- Línea promedio gruesa (con leyenda) ---
        bucket_vals: dict = defaultdict(list)
        for s in sessions:
            for m, v in zip(s["minutos"], s["valores"]):
                # asignar al bucket más cercano
                closest = min(BUCKETS, key=lambda b: abs(b - m))
                bucket_vals[closest].append(v)

        avg_x = sorted(b for b in BUCKETS if bucket_vals[b])
        avg_y = [round(sum(bucket_vals[b]) / len(bucket_vals[b]), 1) for b in avg_x]

        if avg_x:
            n_sessions = len(sessions)
            fig.add_trace(go.Scatter(
                x=avg_x,
                y=avg_y,
                mode="lines+markers",
                name=f"{cfg['label']} (n={n_sessions})",
                legendgroup=intensity,
                showlegend=True,
                line=dict(color=cfg["solid"], width=3),
                marker=dict(size=6, color=cfg["solid"],
                            line=dict(width=1, color="white")),
                hovertemplate=(
                    f"<b>Promedio {intensity}</b><br>"
                    "%{x:.0f} min → %{y:.0f} mg/dL<extra></extra>"
                ),
            ))

    fig.update_layout(
        title=f"Glucemia antes/después del ejercicio — últimos {days} días",
        xaxis_title="Minutos respecto al inicio del ejercicio",
        yaxis_title="mg/dL",
        xaxis=dict(
            tickvals=[-60, -30, 0, 30, 60, 90, 120, 180, 240],
            ticktext=["-60", "-30", "0", "+30", "+60", "+90", "+120", "+180", "+240"],
            zeroline=False,
        ),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(248,250,252,1)",
        font=dict(family="Inter, system-ui, sans-serif", size=12),
        hovermode="x unified",
        margin=dict(l=50, r=20, t=50, b=60),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )

    return _to_json(fig)


def chart_glucose_vs_carbs(days=90) -> dict:
    """Scatter: carbohidratos vs alza de glucosa post-comida."""
    from models import Meal, GlucoseReading

    desde = datetime.utcnow() - timedelta(days=days)
    comidas = Meal.query.filter(Meal.timestamp >= desde).all()

    puntos_carbs = []
    puntos_delta = []
    puntos_nombre = []
    puntos_pre = []

    for comida in comidas:
        pre = (
            GlucoseReading.query
            .filter(
                GlucoseReading.timestamp >= comida.timestamp - timedelta(minutes=30),
                GlucoseReading.timestamp <= comida.timestamp,
            )
            .order_by(GlucoseReading.timestamp.desc())
            .first()
        )
        post_lecturas = (
            GlucoseReading.query
            .filter(
                GlucoseReading.timestamp > comida.timestamp,
                GlucoseReading.timestamp <= comida.timestamp + timedelta(hours=2),
            )
            .all()
        )
        if not pre or not post_lecturas:
            continue

        delta = max(r.value_mgdl for r in post_lecturas) - pre.value_mgdl
        puntos_carbs.append(comida.carbs_g)
        puntos_delta.append(round(delta, 1))
        puntos_nombre.append(comida.name[:30])
        puntos_pre.append(pre.value_mgdl)

    if not puntos_carbs:
        return _to_json(go.Figure())

    colores_scatter = [COLOR_HIPER if d > 80 else (COLOR_GLUCOSA if d >= 0 else COLOR_RANGO)
                       for d in puntos_delta]

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=puntos_carbs,
        y=puntos_delta,
        mode="markers",
        marker=dict(
            color=colores_scatter,
            size=10,
            opacity=0.75,
            line=dict(width=1, color="white"),
        ),
        text=puntos_nombre,
        customdata=puntos_pre,
        hovertemplate=(
            "<b>%{text}</b><br>"
            "Carbohidratos: %{x}g<br>"
            "Δ Glucosa: %{y:+.0f} mg/dL<br>"
            "Pre-comida: %{customdata:.0f} mg/dL"
            "<extra></extra>"
        ),
    ))

    # Línea de tendencia simple si hay suficientes puntos
    if len(puntos_carbs) >= 5:
        import statistics
        n = len(puntos_carbs)
        mx = statistics.mean(puntos_carbs)
        my = statistics.mean(puntos_delta)
        num = sum((x - mx) * (y - my) for x, y in zip(puntos_carbs, puntos_delta))
        den = sum((x - mx) ** 2 for x in puntos_carbs)
        if den != 0:
            slope = num / den
            intercept = my - slope * mx
            x_range = [min(puntos_carbs), max(puntos_carbs)]
            y_range = [slope * x + intercept for x in x_range]
            fig.add_trace(go.Scatter(
                x=x_range, y=y_range,
                mode="lines",
                name="Tendencia",
                line=dict(color="rgba(100,100,100,0.5)", dash="dash", width=1.5),
                hoverinfo="skip",
            ))

    fig.add_hline(y=0, line_color="gray", line_width=1)

    fig.update_layout(
        title=f"Carbohidratos vs alza de glucosa — últimos {days} días",
        xaxis_title="Carbohidratos ingeridos (g)",
        yaxis_title="Δ Glucosa post-comida (mg/dL)",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(248,250,252,1)",
        font=dict(family="Inter, system-ui, sans-serif", size=12),
        showlegend=False,
        margin=dict(l=60, r=20, t=50, b=50),
    )

    return _to_json(fig)


def chart_agp(days=14) -> dict:
    """
    Ambulatory Glucose Profile (AGP).
    Superpone N días de datos en un eje de 24h y muestra percentiles
    10/25/50/75/90 por intervalos de 30 minutos.
    """
    from collections import defaultdict
    from models import GlucoseReading
    from datetime import timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    desde = now - timedelta(days=days)

    lecturas = (GlucoseReading.query
                .filter(GlucoseReading.timestamp >= desde)
                .order_by(GlucoseReading.timestamp).all())

    if len(lecturas) < 12:
        fig = go.Figure()
        fig.update_layout(
            title="Sin datos suficientes para el AGP",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        return _to_json(fig)

    # ── Agrupar en bins de 30 minutos (0..47) ────────────────────────────────
    bins: dict = defaultdict(list)
    for r in lecturas:
        slot = r.timestamp.hour * 2 + (1 if r.timestamp.minute >= 30 else 0)
        bins[slot].append(r.value_mgdl)

    def pct(data, p):
        data = sorted(data)
        n = len(data)
        if n == 0:
            return None
        idx = p / 100 * (n - 1)
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        return round(data[lo] + (idx - lo) * (data[hi] - data[lo]), 1)

    # Etiquetas en formato "HH:MM" para cada bin, duplicando el punto 0 al final
    # para cerrar el ciclo de 24h visualmente
    SLOTS = list(range(48)) + [0]
    x_labels = [f"{s // 2:02d}:{'30' if s % 2 else '00'}" for s in range(48)] + ["24:00"]

    p10 = [pct(bins[s % 48], 10) for s in SLOTS]
    p25 = [pct(bins[s % 48], 25) for s in SLOTS]
    p50 = [pct(bins[s % 48], 50) for s in SLOTS]
    p75 = [pct(bins[s % 48], 75) for s in SLOTS]
    p90 = [pct(bins[s % 48], 90) for s in SLOTS]

    fig = go.Figure()

    # ── Banda objetivo (70-180) ───────────────────────────────────────────────
    fig.add_hrect(y0=70, y1=180,
                  fillcolor="rgba(34,197,94,0.07)",
                  line_width=0, layer="below")

    # ── Banda P10-P90 (más externa, muy transparente) ─────────────────────────
    fig.add_trace(go.Scatter(
        x=x_labels, y=p90,
        mode="lines", line=dict(width=0),
        showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=x_labels, y=p10,
        mode="lines", line=dict(width=0),
        fill="tonexty",
        fillcolor="rgba(99,102,241,0.12)",
        name="P10–P90 (80%)",
        hoverinfo="skip",
    ))

    # ── Banda P25-P75 (IQR, más visible) ─────────────────────────────────────
    fig.add_trace(go.Scatter(
        x=x_labels, y=p75,
        mode="lines", line=dict(width=0),
        showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=x_labels, y=p25,
        mode="lines", line=dict(width=0),
        fill="tonexty",
        fillcolor="rgba(99,102,241,0.28)",
        name="P25–P75 (50%)",
        hoverinfo="skip",
    ))

    # ── Mediana P50 ───────────────────────────────────────────────────────────
    fig.add_trace(go.Scatter(
        x=x_labels, y=p50,
        mode="lines",
        line=dict(color="#6366f1", width=3),
        name="Mediana",
        hovertemplate="<b>%{x}</b><br>Mediana: %{y} mg/dL<extra></extra>",
    ))

    # ── Líneas de referencia ──────────────────────────────────────────────────
    fig.add_hline(y=70,  line=dict(color=COLOR_HIPO,  width=1.5, dash="dot"),
                  annotation_text="70", annotation_position="left",
                  annotation_font=dict(color=COLOR_HIPO, size=11))
    fig.add_hline(y=180, line=dict(color=COLOR_HIPER, width=1.5, dash="dot"),
                  annotation_text="180", annotation_position="left",
                  annotation_font=dict(color=COLOR_HIPER, size=11))
    fig.add_hline(y=54,  line=dict(color="#7f1d1d", width=1, dash="dash"),
                  annotation_text="54", annotation_position="left",
                  annotation_font=dict(color="#7f1d1d", size=10))
    fig.add_hline(y=250, line=dict(color="#7c2d12", width=1, dash="dash"),
                  annotation_text="250", annotation_position="left",
                  annotation_font=dict(color="#7c2d12", size=10))

    # ── Layout ────────────────────────────────────────────────────────────────
    # Marcas cada 3 horas
    tickvals = [f"{h:02d}:00" for h in range(0, 25, 3)]
    tickvals[-1] = "24:00"

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(248,250,252,1)",
        font=dict(family="Inter, system-ui, sans-serif", size=12),
        margin=dict(l=55, r=20, t=20, b=50),
        height=320,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="left", x=0,
            font=dict(size=11),
        ),
        xaxis=dict(
            title="Hora del día",
            tickvals=tickvals,
            ticktext=tickvals,
            tickangle=0,
            gridcolor="#e2e8f0",
            showgrid=True,
        ),
        yaxis=dict(
            title="Glucosa (mg/dL)",
            range=[30, 350],
            gridcolor="#e2e8f0",
            showgrid=True,
            dtick=50,
        ),
        hovermode="x unified",
    )

    return _to_json(fig)
