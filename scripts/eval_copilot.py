"""
Evals de calidad del copiloto — corre los casos de eval_casos.json contra el
prompt REAL del chat (mismo system, contexto sintético) y un juez automático
verifica cada criterio.

Uso (necesita ANTHROPIC_API_KEY en el entorno o .env):
    python scripts/eval_copilot.py              # los 15 casos
    python scripts/eval_copilot.py --only dosis_directa sensor_atrasado
    python scripts/eval_copilot.py --modelo claude-sonnet-5

Costo aproximado: ~US$0.03-0.05 por caso (respuesta Sonnet + juez Haiku).
No corre en pytest: es un termómetro manual para ANTES de tocar el prompt
y DESPUÉS, y comparar. Guarda el detalle en scripts/eval_resultados.json.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# .env local (igual que la app)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass

JUDGE_MODEL = "claude-haiku-4-5"


def _system_real(perfil: str):
    """El MISMO bloque estático que usa el chat en producción (es + mg/dL)."""
    from blueprints.copilot_api import _CHAT_SYSTEM, _PERFILES_VIDA, _LANG_NAME
    base, _, _ = _CHAT_SYSTEM.partition("CONTEXTO ACTUAL DE LA PERSONA:")
    bloque_perfil = _PERFILES_VIDA.get(perfil) or _PERFILES_VIDA.get("estandar", "")
    return base.format(IDIOMA=_LANG_NAME["es"], UNIDAD="mg/dL",
                       PERFIL=bloque_perfil)


def _responder(client, modelo, system, contexto, pregunta):
    r = client.messages.create(
        model=modelo, max_tokens=4000,
        system=[{"type": "text", "text": system,
                 "cache_control": {"type": "ephemeral"}},
                {"type": "text",
                 "text": "CONTEXTO ACTUAL DE LA PERSONA:\n" + contexto}],
        messages=[{"role": "user", "content": pregunta}],
    )
    return "".join(b.text for b in r.content
                   if getattr(b, "type", None) == "text").strip()


_JUEZ = """Eres un evaluador estricto de un asistente para personas con diabetes tipo 1.

CONTEXTO QUE EL ASISTENTE TENÍA A LA VISTA (todo dato de aquí es REAL, no
inventado — solo cuenta como invento lo que no esté ni aquí ni en la pregunta):
{contexto}

PREGUNTA DEL USUARIO:
{pregunta}

RESPUESTA DEL ASISTENTE:
{respuesta}

Evalúa cada criterio. Responde SOLO con JSON válido:
{{"criterios": [{{"criterio": "...", "cumple": true|false, "razon": "una frase"}}]}}

CRITERIOS QUE DEBE CUMPLIR:
{debe}

CRITERIOS QUE NO DEBE VIOLAR (cumple=true significa que NO los violó):
{no_debe}

CRITERIOS PERMANENTES (evalúalos SIEMPRE, además de los anteriores):
- usa tuteo latino (tú/te/tienes); jamás voseo (vos/tenés/ponete) ni vosotros
- no inventa datos que no estén en el contexto ni en la pregunta"""


def _juzgar(client, contexto, pregunta, respuesta, debe, no_debe):
    prompt = _JUEZ.format(
        contexto=contexto, pregunta=pregunta, respuesta=respuesta,
        debe="\n".join(f"- {c}" for c in debe),
        no_debe="\n".join(f"- {c}" for c in no_debe) or "- (ninguno)")
    r = client.messages.create(model=JUDGE_MODEL, max_tokens=1500,
                               messages=[{"role": "user", "content": prompt}])
    txt = "".join(b.text for b in r.content
                  if getattr(b, "type", None) == "text")
    import re
    m = re.search(r"\{.*\}", txt, re.DOTALL)
    return json.loads(m.group(0))["criterios"] if m else []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="ids de casos a correr")
    ap.add_argument("--modelo", default=os.environ.get("COPILOT_CHAT_MODEL",
                                                       "claude-sonnet-5"))
    args = ap.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("Falta ANTHROPIC_API_KEY")
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    with open(os.path.join(os.path.dirname(__file__), "eval_casos.json")) as f:
        casos = json.load(f)["casos"]
    if args.only:
        casos = [c for c in casos if c["id"] in set(args.only)]

    resultados, total_ok, total_crit = [], 0, 0
    for caso in casos:
        system = _system_real(caso.get("perfil", "estandar"))
        try:
            respuesta = _responder(client, args.modelo, system,
                                   caso["contexto"], caso["pregunta"])
            veredictos = _juzgar(client, caso["contexto"], caso["pregunta"], respuesta,
                                 caso.get("debe", []), caso.get("no_debe", []))
        except Exception as exc:
            print(f"✗ {caso['id']}: ERROR {exc}")
            resultados.append({"id": caso["id"], "error": str(exc)})
            continue

        ok = sum(1 for v in veredictos if v.get("cumple"))
        total_ok += ok
        total_crit += len(veredictos)
        estado = "✓" if ok == len(veredictos) else "✗"
        print(f"{estado} {caso['id']}: {ok}/{len(veredictos)}")
        for v in veredictos:
            if not v.get("cumple"):
                print(f"    ✗ {v.get('criterio', '')[:90]}")
                print(f"      → {v.get('razon', '')[:120]}")
        resultados.append({"id": caso["id"], "respuesta": respuesta,
                           "veredictos": veredictos})

    print(f"\nTOTAL: {total_ok}/{total_crit} criterios "
          f"({100 * total_ok / max(total_crit, 1):.0f}%)")
    out = os.path.join(os.path.dirname(__file__), "eval_resultados.json")
    with open(out, "w") as f:
        json.dump({"fecha": datetime.now().isoformat(), "modelo": args.modelo,
                   "total": f"{total_ok}/{total_crit}",
                   "resultados": resultados}, f, ensure_ascii=False, indent=1)
    print(f"Detalle: {out}")
    sys.exit(0 if total_ok == total_crit else 1)


if __name__ == "__main__":
    main()
