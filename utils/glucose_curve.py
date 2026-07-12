"""
utils/glucose_curve.py — segmentación de la serie de glucosa en tramos.

Convierte la serie CGM (lecturas cada ~5 min, con ruido) en los tramos que
una persona ve al mirar la curva: "subió de 92 a 166 entre 07:12 y 08:05,
después bajó a 101 para las 09:40". El copiloto los recibe dentro de su
línea de tiempo y así razona causa→efecto sobre la FORMA de la serie
temporal, no solo sobre mínimos/máximos/episodios extremos.

Algoritmo en dos pasos:
 1. Zigzag por pivotes — un giro se confirma cuando la curva retrocede
    >= `umbral` mg/dL desde el extremo del tramo en curso; las oscilaciones
    de ruido (< umbral) no cortan un tramo real. Mientras la dirección
    inicial no está confirmada, el pivote de arranque se ancla al ÚLTIMO
    extremo visto (no al primer punto): una noche plana antes de una subida
    no debe quedar dentro de la subida.
 2. Recorte de colas — cada tramo se recorta a la parte donde de verdad se
    movió: el arranque avanza mientras el valor siga pegado al inicial y el
    final retrocede mientras siga pegado al final (mesetas afuera).

Los huecos del sensor (> `gap_min` sin lecturas) parten la serie: un salto
sin datos no debe fabricar una "subida de 4 horas" que nunca se vio.
"""

from __future__ import annotations

from datetime import timedelta

# giro confirmado del zigzag (mg/dL); < esto se considera ruido/meseta
UMBRAL_MGDL = 25.0
# hueco de sensor que parte la serie (minutos sin lecturas)
GAP_MIN = 45
# velocidad bajo la cual un tramo largo se narra como deriva, no movimiento
DERIVA_MGDL_MIN = 0.35


def _runs(pts, gap_min=GAP_MIN):
    """Parte la serie en corridas contiguas (sin huecos de sensor)."""
    if not pts:
        return []
    runs, cur = [], [pts[0]]
    for prev, p in zip(pts, pts[1:]):
        if (p[0] - prev[0]) > timedelta(minutes=gap_min):
            runs.append(cur)
            cur = [p]
        else:
            cur.append(p)
    runs.append(cur)
    return runs


def _pivote_indices(pts, umbral=UMBRAL_MGDL):
    """Índices de los puntos de giro (zigzag) de una corrida contigua."""
    n = len(pts)
    if n < 2:
        return list(range(n))
    piv = [0]
    trend = 0          # +1 subiendo, -1 bajando, 0 aún indefinido
    ext_i = 0          # extremo del tramo en curso
    min_i = max_i = 0  # extremos vistos mientras la dirección es indefinida
    for i in range(1, n):
        v = pts[i][1]
        if trend == 0:
            if v <= pts[min_i][1]:
                min_i = i
            if v >= pts[max_i][1]:
                max_i = i
            if v - pts[min_i][1] >= umbral:      # arranca subiendo
                piv[0] = min_i
                trend, ext_i = 1, i
            elif pts[max_i][1] - v >= umbral:    # arranca bajando
                piv[0] = max_i
                trend, ext_i = -1, i
        elif trend == 1:
            if v >= pts[ext_i][1]:
                ext_i = i
            elif pts[ext_i][1] - v >= umbral:    # giro: pico → baja
                piv.append(ext_i)
                trend, ext_i = -1, i
        else:
            if v <= pts[ext_i][1]:
                ext_i = i
            elif v - pts[ext_i][1] >= umbral:    # giro: valle → sube
                piv.append(ext_i)
                trend, ext_i = 1, i
    piv.append(ext_i)
    return piv


def pivotes(pts, umbral=UMBRAL_MGDL):
    """Puntos de giro [(t, v)] de una corrida contigua ordenada."""
    return [pts[i] for i in _pivote_indices(pts, umbral)]


def _recortar(pts, a, b):
    """Recorta las colas planas del tramo pts[a..b]: el arranque avanza
    mientras el valor siga pegado al inicial, el final retrocede mientras
    siga pegado al final. Devuelve (i0, i1) con a <= i0 < i1 <= b."""
    v0, v1 = pts[a][1], pts[b][1]
    delta = v1 - v0
    if delta == 0:
        return a, b
    tol = max(5.0, abs(delta) * 0.12)
    sube = delta > 0
    i0 = a
    for i in range(a, b + 1):
        cerca_inicio = (pts[i][1] <= v0 + tol) if sube else (pts[i][1] >= v0 - tol)
        if cerca_inicio:
            i0 = i
        else:
            break
    i1 = b
    for i in range(b, a - 1, -1):
        cerca_final = (pts[i][1] >= v1 - tol) if sube else (pts[i][1] <= v1 + tol)
        if cerca_final:
            i1 = i
        else:
            break
    return (i0, i1) if i1 > i0 else (a, b)


def segmentos(pts, umbral=UMBRAL_MGDL, gap_min=GAP_MIN):
    """[(t, v)] ordenado → tramos de la curva, listos para narrar.

    Devuelve dicts {t0, v0, t1, v1, delta, minutos, tipo} con
    tipo ∈ {subida, bajada, deriva} (deriva = movimiento largo y lento,
    p. ej. la subida suave de la madrugada)."""
    out = []
    for run in _runs(pts, gap_min):
        idx = _pivote_indices(run, umbral)
        for a, b in zip(idx, idx[1:]):
            if abs(run[b][1] - run[a][1]) < umbral:   # colita sin giro
                continue
            i0, i1 = _recortar(run, a, b)
            (t0, v0), (t1, v1) = run[i0], run[i1]
            delta = v1 - v0
            minutos = max(1, int(round((t1 - t0).total_seconds() / 60)))
            tipo = "subida" if delta > 0 else "bajada"
            if abs(delta) / minutos < DERIVA_MGDL_MIN and minutos >= 90:
                tipo = "deriva"
            out.append({"t0": t0, "v0": v0, "t1": t1, "v1": v1,
                        "delta": delta, "minutos": minutos, "tipo": tipo})
    return out


def huecos(pts, gap_min=60):
    """Huecos largos del sensor [(desde, hasta)] — datos que NO existen."""
    out = []
    for prev, p in zip(pts, pts[1:]):
        if (p[0] - prev[0]) > timedelta(minutes=gap_min):
            out.append((prev[0], p[0]))
    return out


def duracion_txt(minutos: int) -> str:
    """95 → '95 min' · 150 → '2 h 30 min' · 120 → '2 h'."""
    if minutos < 100:
        return f"{minutos} min"
    h, m = divmod(minutos, 60)
    return f"{h} h {m} min" if m else f"{h} h"
