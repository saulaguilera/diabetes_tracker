"""
Generador de reporte clínico PDF con ReportLab.
Layout 100% vertical — sin columnas side-by-side que puedan solaparse.
"""
import io, base64
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, Image, KeepTogether, PageBreak,
)

PAGE_W, PAGE_H = A4          # 595.27 x 841.89 pt
MARGIN_L = MARGIN_R = 1.5 * cm
MARGIN_T = 2.2 * cm          # deja espacio al header manual
MARGIN_B = 1.6 * cm
USABLE_W = PAGE_W - MARGIN_L - MARGIN_R   # ≈ 510 pt / 18 cm

# ── Paleta ────────────────────────────────────────────────────────────────────
DARK   = colors.HexColor("#1e293b")
GRAY   = colors.HexColor("#64748b")
LGRAY  = colors.HexColor("#94a3b8")
BORDER = colors.HexColor("#e2e8f0")
BG     = colors.HexColor("#f1f5f9")
WHITE  = colors.white
GREEN  = colors.HexColor("#16a34a")
YELLOW = colors.HexColor("#d97706")
RED    = colors.HexColor("#dc2626")
BLUE   = colors.HexColor("#2563eb")
PURPLE = colors.HexColor("#7c3aed")
AMBER  = colors.HexColor("#f59e0b")


# ── Estilos ───────────────────────────────────────────────────────────────────
def S(name):
    _cache = getattr(S, "_cache", None)
    if _cache is None:
        S._cache = {
            "title":  ParagraphStyle("title",  fontName="Helvetica-Bold", fontSize=14, textColor=DARK, spaceAfter=2),
            "sub":    ParagraphStyle("sub",    fontName="Helvetica",      fontSize=8,  textColor=GRAY, spaceAfter=0),
            "sec":    ParagraphStyle("sec",    fontName="Helvetica-Bold", fontSize=7,  textColor=GRAY, spaceBefore=10, spaceAfter=4),
            "body":   ParagraphStyle("body",   fontName="Helvetica",      fontSize=8.5,textColor=DARK, leading=12),
            "small":  ParagraphStyle("small",  fontName="Helvetica",      fontSize=7.5,textColor=GRAY, leading=10),
            "note":   ParagraphStyle("note",   fontName="Helvetica",      fontSize=7,  textColor=LGRAY,leading=9),
            "th":     ParagraphStyle("th",     fontName="Helvetica-Bold", fontSize=7,  textColor=GRAY),
            "td":     ParagraphStyle("td",     fontName="Helvetica",      fontSize=8,  textColor=DARK, leading=10),
            "td_b":   ParagraphStyle("td_b",   fontName="Helvetica-Bold", fontSize=8,  textColor=DARK, leading=10),
            "center": ParagraphStyle("center", fontName="Helvetica",      fontSize=8,  textColor=DARK, leading=10, alignment=1),
        }
    return S._cache[name]


def P(text, style="td", **kw):
    """Shortcut para Paragraph con manejo seguro de tags."""
    return Paragraph(str(text), S(style))


def colored(text, color, bold=False):
    tag = "b" if bold else "span"
    return f'<font color="{color.hexval()}"><{tag}>{text}</{tag}></font>'


def section(text):
    return KeepTogether([
        HRFlowable(width=USABLE_W, thickness=0.5, color=BORDER, spaceAfter=3),
        Paragraph(text.upper(), S("sec")),
    ])


def _base_table_style(has_header=True):
    style = [
        ("BOX",           (0,0), (-1,-1), 0.5, BORDER),
        ("INNERGRID",     (0,0), (-1,-1), 0.4, BORDER),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("LEFTPADDING",   (0,0), (-1,-1), 5),
        ("RIGHTPADDING",  (0,0), (-1,-1), 5),
    ]
    if has_header:
        style += [
            ("BACKGROUND",  (0,0), (-1,0), BG),
            ("FONTNAME",    (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",    (0,0), (-1,0), 7),
            ("TEXTCOLOR",   (0,0), (-1,0), GRAY),
        ]
    return TableStyle(style)


def img_from_b64(b64_str, width, height=None):
    """Renderiza imagen respetando la proporción original si no se indica height."""
    if not b64_str:
        return Spacer(1, 0.5*cm)
    raw = base64.b64decode(b64_str.split(",", 1)[1])
    if height is None:
        from PIL import Image as PILImage
        buf = io.BytesIO(raw)
        w_px, h_px = PILImage.open(buf).size
        height = width * h_px / w_px
    return Image(io.BytesIO(raw), width=width, height=height)


# ── Encabezado / pie (dibujado en canvas) ─────────────────────────────────────
def _on_page(canvas, doc, ctx):
    canvas.saveState()
    w, h = PAGE_W, PAGE_H

    # Línea superior
    canvas.setStrokeColor(DARK)
    canvas.setLineWidth(1.0)
    canvas.line(MARGIN_L, h - 1.4*cm, w - MARGIN_R, h - 1.4*cm)

    # Título izquierda
    canvas.setFont("Helvetica-Bold", 11)
    canvas.setFillColor(DARK)
    canvas.drawString(MARGIN_L, h - 1.1*cm, "Reporte de Glucemia · Diabetes Tipo 1")

    # Período derecha
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(GRAY)
    periodo = (f"{ctx['desde'].strftime('%d/%m/%Y')} – "
               f"{ctx['hasta'].strftime('%d/%m/%Y')} ({ctx['dias']}d)")
    canvas.drawRightString(w - MARGIN_R, h - 1.1*cm, periodo)

    # Pie
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.4)
    canvas.line(MARGIN_L, 1.1*cm, w - MARGIN_R, 1.1*cm)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(LGRAY)
    canvas.drawString(MARGIN_L, 0.8*cm,
        "DiabetesTracker · Solo orientativo, no reemplaza evaluación clínica")
    canvas.drawRightString(w - MARGIN_R, 0.8*cm, f"Página {doc.page}")
    canvas.restoreState()


# ── Tabla de métricas principales (2 filas x 6 col) ──────────────────────────
def _metricas(stats):
    col_w = USABLE_W / 6

    def mk(val, label, meta, col):
        return [
            Paragraph(colored(val, col, bold=True), S("center")),
            Paragraph(label, S("note") if False else ParagraphStyle(
                "clabel", fontName="Helvetica", fontSize=7, textColor=GRAY,
                alignment=1, leading=9)),
            Paragraph(meta, ParagraphStyle(
                "cmeta", fontName="Helvetica", fontSize=6.5, textColor=LGRAY,
                alignment=1, leading=8)),
        ]

    tir_c  = GREEN if stats["tir"]  >= 70 else (YELLOW if stats["tir"]  >= 50 else RED)
    tbr_c  = GREEN if stats["tbr"]  <= 4  else RED
    tar_c  = GREEN if stats["tar"]  <= 25 else (YELLOW if stats["tar"]  <= 40 else RED)
    gmi_c  = GREEN if stats["gmi"]  <  7  else (YELLOW if stats["gmi"]  <  8  else RED)
    cv_c   = GREEN if stats["cv"]   <= 36 else YELLOW

    row1 = [
        mk(f'{stats["gmi"]}%',  "GMI ≈ HbA1c",    "Meta <7%",    gmi_c),
        mk(f'{stats["tir"]}%',  "Tiempo en rango", "70–180 ≥70%", tir_c),
        mk(f'{stats["tbr"]}%',  "Bajo rango",      "<70   ≤4%",   tbr_c),
        mk(f'{stats["tar"]}%',  "Alto rango",      ">180  ≤25%",  tar_c),
        mk(f'{stats["mean"]}',  "Glucemia media",  "mg/dL",       BLUE),
        mk(f'{stats["cv"]}%',   "C. variación",    "≤36%",        cv_c),
    ]

    tbr54_c  = RED   if stats["tbr54"]  > 1 else GREEN
    tar250_c = RED   if stats["tar250"] > 5 else YELLOW

    row2 = [
        mk(f'{stats["min"]}',     "Mínimo",       "mg/dL",   GRAY),
        mk(f'{stats["max"]}',     "Máximo",       "mg/dL",   GRAY),
        mk(f'{stats["tbr54"]}%',  "Hipo <54",     "≤1%",     tbr54_c),
        mk(f'{stats["tar250"]}%', "Hiper >250",   "≤5%",     tar250_c),
        mk(f'{stats["sd"]}',      "Desv. estándar","mg/dL",  GRAY),
        mk(f'{stats["n"]}',       "Lecturas",     "total",    GRAY),
    ]

    t1 = Table([row1], colWidths=[col_w]*6, rowHeights=[38])
    t1.setStyle(TableStyle([
        ("BOX",           (0,0),(-1,-1), 0.5, BORDER),
        ("INNERGRID",     (0,0),(-1,-1), 0.4, BORDER),
        ("BACKGROUND",    (0,0),(-1,-1), BG),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ("ALIGN",         (0,0),(-1,-1), "CENTER"),
        ("TOPPADDING",    (0,0),(-1,-1), 5),
        ("BOTTOMPADDING", (0,0),(-1,-1), 5),
    ]))

    t2 = Table([row2], colWidths=[col_w]*6, rowHeights=[32])
    t2.setStyle(TableStyle([
        ("BOX",           (0,0),(-1,-1), 0.5, BORDER),
        ("INNERGRID",     (0,0),(-1,-1), 0.4, BORDER),
        ("BACKGROUND",    (0,0),(-1,-1), WHITE),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ("ALIGN",         (0,0),(-1,-1), "CENTER"),
        ("TOPPADDING",    (0,0),(-1,-1), 4),
        ("BOTTOMPADDING", (0,0),(-1,-1), 4),
    ]))
    return [t1, Spacer(1, 2), t2]


# ── Tabla de franjas horarias ─────────────────────────────────────────────────
def _franjas(franjas):
    cols = [4.5*cm, 2.5*cm, 2*cm, 2*cm, 2.5*cm, 2*cm]   # sum = 15.5cm < 18cm ✓
    header = [P("Franja","th"), P("Promedio","th"), P("Mín","th"),
               P("Máx","th"), P("TIR","th"), P("N","th")]
    rows = [header]
    for f in franjas:
        if f.get("sin_datos"):
            rows.append([P(f"{f['nombre']} · {f['label']}"), P("—"), P("—"), P("—"), P("—"), P("—")])
        else:
            avg_c = RED if (f["avg"] > 180 or f["avg"] < 70) else GREEN
            tir_c = GREEN if f["tir"] >= 70 else (YELLOW if f["tir"] >= 50 else RED)
            rows.append([
                Paragraph(f'<b>{f["nombre"]}</b> · <font color="{LGRAY.hexval()}">{f["label"]}</font>', S("td")),
                Paragraph(colored(f'{f["avg"]} mg/dL', avg_c, bold=True), S("td")),
                P(str(f["min"])), P(str(f["max"])),
                Paragraph(colored(f'{f["tir"]}%', tir_c, bold=True), S("td")),
                P(str(f["n"])),
            ])
    t = Table(rows, colWidths=cols)
    t.setStyle(_base_table_style())
    return t


# ── Tabla de tratamiento ──────────────────────────────────────────────────────
def _tratamiento(ctx):
    cols = [7*cm, 4*cm, 4*cm]    # sum = 15cm < 18cm ✓
    dias = ctx["dias"]
    rows = [
        [P("Parámetro","th"), P("Total período","th"), P("Prom. diario","th")],
        [P("Insulina rápida (bolus)"),
         Paragraph(colored(f'{ctx["bolus_total"]}U', PURPLE, True), S("td")),
         P(f'{ctx["bolus_diario"]}U/día')],
        [P("Insulina basal"),
         Paragraph(colored(f'{ctx["basal_total"]}U', BLUE, True), S("td")),
         P(f'{ctx["basal_diario"]}U/día')],
        [P("Comidas registradas"),
         P(str(ctx["n_comidas"])),
         P(f'{round(ctx["n_comidas"]/dias,1)}/día')],
        [P("Carbohidratos"),
         P("—"),
         P(f'{int(ctx["carbs_diario"])}g/día')],
        [P("Actividad física"),
         Paragraph(colored(f'{ctx["min_actividad"]} min', GREEN, True), S("td")),
         P(f'{ctx["n_actividades"]} sesiones')],
    ]
    t = Table(rows, colWidths=cols)
    t.setStyle(_base_table_style())
    return t


# ── Tabla de hipoglucemias ────────────────────────────────────────────────────
def _hipos(hipos):
    cols = [4.5*cm, 3*cm, 3*cm, 3*cm]   # 13.5cm < 18cm ✓
    header = [P("Fecha y hora","th"), P("Valor","th"), P("Gravedad","th"), P("Fuente","th")]
    rows = [header]
    for r in hipos:
        gc = RED if r.value_mgdl < 54 else YELLOW
        gt = "Grave (<54 mg/dL)" if r.value_mgdl < 54 else "Leve"
        fu = {"cgm_historic":"CGM","cgm_scan":"Escáner"}.get(r.source,"Manual")
        rows.append([
            P(r.timestamp.strftime("%d/%m/%Y %H:%M")),
            Paragraph(colored(f'{int(r.value_mgdl)} mg/dL', RED, True), S("td")),
            Paragraph(colored(gt, gc, True), S("td")),
            P(fu),
        ])
    t = Table(rows, colWidths=cols)
    t.setStyle(_base_table_style())
    return t


# ── Tabla detalle comidas ─────────────────────────────────────────────────────
def _tabla_comidas(filas):
    # 18cm total
    cols = [2.3*cm, 4.8*cm, 1.4*cm, 3*cm, 1.5*cm, 1.5*cm, 1.5*cm]  # = 16cm ✓
    header = [P("Fecha","th"), P("Alimento","th"), P("CH","th"),
               P("Insulina","th"), P("Pre","th"), P("Pico","th"), P("Δ","th")]
    rows = [header]
    for f in filas:
        pico_c  = RED   if f["pico"]  > 180 else GREEN
        delta_c = RED   if f["delta"] > 80  else (YELLOW if f["delta"] > 40 else GREEN)
        sign    = "+" if f["delta"] > 0 else ""
        if f["insulina"]:
            ins = "\n".join(
                f'{d["units"]}U {d["timing"]}' for d in f["insulina"]
            )
            ins_p = Paragraph(
                "<br/>".join(
                    f'{colored(str(d["units"])+"U", PURPLE, True)} '
                    f'<font color="{LGRAY.hexval()}">{d["timing"]}</font>'
                    for d in f["insulina"]
                ), S("td")
            )
        else:
            ins_p = P("—")
        rows.append([
            Paragraph(f'<font color="{LGRAY.hexval()}">{f["fecha"]}</font>', S("td")),
            P(f["nombre"][:26]),
            Paragraph(colored(f'{f["carbs"]}g', AMBER, True), S("td")),
            ins_p,
            P(str(f["pre"])),
            Paragraph(colored(str(f["pico"]), pico_c, True), S("td")),
            Paragraph(colored(f'{sign}{f["delta"]}', delta_c, True), S("td")),
        ])
    t = Table(rows, colWidths=cols)
    t.setStyle(_base_table_style())
    return t


# ── Tabla resumen por alimento ────────────────────────────────────────────────
def _tabla_resumen(resumen):
    cols = [7*cm, 2.5*cm, 3*cm, 3.5*cm]   # 16cm ✓
    header = [P("Alimento","th"), P("Veces","th"), P("Δ promedio","th"), P("Pico promedio","th")]
    rows = [header]
    for r in resumen[:15]:
        d_c = RED if r["avg_delta"] > 80 else (YELLOW if r["avg_delta"] > 40 else GREEN)
        p_c = RED if r["avg_pico"]  > 180 else GREEN
        sign = "+" if r["avg_delta"] > 0 else ""
        rows.append([
            P(r["nombre"][:30]),
            P(str(r.get("veces", r.get("n","—")))),
            Paragraph(colored(f'{sign}{r["avg_delta"]} mg/dL', d_c, True), S("td")),
            Paragraph(colored(f'{r["avg_pico"]} mg/dL',        p_c, True), S("td")),
        ])
    t = Table(rows, colWidths=cols)
    t.setStyle(_base_table_style())
    return t


# ── Función principal ─────────────────────────────────────────────────────────
def generar_pdf(ctx: dict) -> bytes:
    buf   = io.BytesIO()
    stats = ctx.get("stats", {})

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=MARGIN_L, rightMargin=MARGIN_R,
        topMargin=MARGIN_T,  bottomMargin=MARGIN_B,
        title="Reporte de Glucemia",
    )

    on_page = lambda c, d: _on_page(c, d, ctx)

    story = []

    # ── Subtítulo del período ──
    story.append(Paragraph(
        f'Generado el {ctx["hasta"].strftime("%d/%m/%Y a las %H:%M")}  ·  '
        f'{ctx["dias"]} días analizados',
        S("small")
    ))
    story.append(Spacer(1, 0.4*cm))

    if not stats:
        story.append(Paragraph(
            "Sin lecturas de glucemia registradas en el período seleccionado.",
            S("body")))
        doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
        return buf.getvalue()

    # ── Métricas ──
    story.append(section(f"Métricas principales — {stats['n']} lecturas registradas"))
    story.append(Spacer(1, 0.15*cm))
    for item in _metricas(stats):
        story.append(item)
    story.append(Spacer(1, 0.3*cm))

    # ── Gráficas ──
    img_tir      = ctx.get("img_tir", "")
    img_circ     = ctx.get("img_circ", "")
    img_timeline = ctx.get("img_timeline", "")

    if img_tir or img_circ or img_timeline:
        story.append(section("Distribución del tiempo en rango y patrón circadiano"))
        story.append(Spacer(1, 0.1*cm))

    if img_tir:
        # TIR centrado — alto calculado automáticamente desde el ratio (cuadrado)
        story.append(img_from_b64(img_tir, 6*cm))
        story.append(Spacer(1, 0.2*cm))

    if img_circ:
        # Circadiano ancho completo — alto preserva ratio original
        story.append(img_from_b64(img_circ, USABLE_W))
        story.append(Spacer(1, 0.2*cm))

    if img_timeline:
        # Timeline ancho completo — alto preserva ratio original
        story.append(img_from_b64(img_timeline, USABLE_W))

    story.append(Spacer(1, 0.3*cm))

    # ── Franjas horarias ──
    story.append(section("Glucemia por franja horaria"))
    story.append(Spacer(1, 0.1*cm))
    story.append(_franjas(ctx["franjas"]))
    story.append(Spacer(1, 0.3*cm))

    # ── Resumen de tratamiento ──
    story.append(section("Resumen de tratamiento"))
    story.append(Spacer(1, 0.1*cm))
    story.append(_tratamiento(ctx))
    story.append(Spacer(1, 0.3*cm))

    # ── Hipoglucemias ──
    hipos = ctx.get("hipos", [])
    if hipos:
        story.append(section(f"Episodios de hipoglucemia (<70 mg/dL) — {len(hipos)} en el período"))
        story.append(Spacer(1, 0.1*cm))
        story.append(_hipos(hipos))
        story.append(Spacer(1, 0.3*cm))

    # ── Comidas ──
    tabla_comidas   = ctx.get("tabla_comidas", [])
    resumen_comidas = ctx.get("resumen_comidas", [])

    if tabla_comidas or resumen_comidas:
        story.append(PageBreak())
        story.append(section("Impacto de comidas en glucemia"))
        story.append(Spacer(1, 0.1*cm))

        if tabla_comidas:
            story.append(_tabla_comidas(tabla_comidas))
            story.append(Spacer(1, 0.3*cm))

        if resumen_comidas:
            story.append(section("Alimentos por mayor impacto glucémico (promedio)"))
            story.append(Spacer(1, 0.1*cm))
            story.append(_tabla_resumen(resumen_comidas))
            story.append(Spacer(1, 0.15*cm))
            story.append(Paragraph(
                "Δ = alza de glucemia post-comida (mg/dL)  ·  "
                "Pico = valor máximo en las 2 horas posteriores",
                S("note")
            ))

    # ── Nota metodológica ──
    story.append(Spacer(1, 0.5*cm))
    story.append(HRFlowable(width=USABLE_W, thickness=0.4, color=BORDER, spaceAfter=5))
    story.append(Paragraph(
        "<b>Nota metodológica:</b> GMI calculado con fórmula ADA/EASD "
        "(GMI = 3.31 + 0.02392 × glucemia media en mg/dL). "
        "Metas de referencia según estándares ADA 2024 / TIR Consensus 2019 para Diabetes Tipo 1.",
        S("note")
    ))

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()
