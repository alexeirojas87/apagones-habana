"""Prueba de concepto: ¿un LLM (Cloudflare Workers AI, gratis) clasifica y
extrae los posts oficiales mejor que las reglas? Compara la salida del LLM
contra el extractor por reglas sobre una muestra de posts históricos.

Uso:
    CLOUDFLARE_ACCOUNT_ID=... CLOUDFLARE_AI_TOKEN=... python scripts/poc_llm.py

El token debe tener permiso 'Workers AI: Read' (distinto del de Pages).
"""

import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "extractor"))
from extract import extraer  # noqa: E402

MODELO = os.environ.get("MODELO", "@cf/meta/llama-3.1-8b-instruct")
MUESTRA = os.environ.get(
    "MUESTRA", os.path.join(os.path.dirname(__file__), "..", "data", "muestra_posts_eval.json")
)

PROMPT_SISTEMA = (
    "Eres un extractor de datos de avisos de la Empresa Eléctrica de La Habana. "
    "Dado un aviso, devuelve SOLO un objeto JSON válido, sin texto adicional, con: "
    '{"accion": uno de ["afecta","restablece","restablece_parcial","averia","informativo"], '
    '"bloques": [enteros 1-6 mencionados], '
    '"circuitos_emergencia": booleano (si menciona "circuitos de emergencia"), '
    '"causa": uno de ["DAF","deficit","averia","mantenimiento",null], '
    '"zonas": [lista de nombres de calles/repartos/zonas afectados o restablecidos]}. '
    "'restablece_parcial' = se restablecen ALGUNOS circuitos pero el bloque sigue afectado "
    "(frases como 'se continúa trabajando', 'inicia de forma gradual', 'los siguientes circuitos'). "
    "'informativo' = no describe afectación ni restablecimiento concreto. "
    "REGLA CRÍTICA sobre 'bloques': incluye SOLO números precedidos por la palabra "
    "'bloque'/'bloques' (p. ej. 'Bloque No. 4'). Los números de calles, direcciones, "
    "zonas o megavatios NO son bloques. Si el aviso no dice 'bloque', devuelve bloques: []."
)


def llm(texto, account, token):
    body = json.dumps({
        "messages": [
            {"role": "system", "content": PROMPT_SISTEMA},
            {"role": "user", "content": texto[:2000]},
        ],
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{MODELO}",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    r = json.load(urllib.request.urlopen(req, timeout=60))
    res = r.get("result", {})
    # Workers AI devuelve {response: ...} o formato OpenAI {choices:[{message:{content}}]}
    salida = res.get("response")
    if not isinstance(salida, str):
        try:
            salida = res["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None
    if not isinstance(salida, str):
        return None
    salida = salida.replace("```json", "").replace("```", "")  # quita vallas de código
    m = re.search(r"\{.*\}", salida, re.DOTALL)
    return json.loads(m.group(0)) if m else None


def reglas(texto):
    """Resumen comparable de lo que producen las reglas actuales."""
    evs = extraer("canal", texto)
    if not evs:
        return {"accion": "informativo", "bloques": [], "zonas": 0}
    tipos = {e["tipo"] for e in evs}
    accion = ("restablece_parcial" if "restablecimiento_parcial" in tipos
              else "restablece" if "restablecimiento" in tipos
              else "afecta")
    bloques = sorted({e["bloque"] for e in evs if e["bloque"]})
    zonas = max(len(e["zonas"]) for e in evs)
    return {"accion": accion, "bloques": bloques, "zonas": zonas}


def main():
    account = os.environ["CLOUDFLARE_ACCOUNT_ID"]
    token = os.environ["CLOUDFLARE_AI_TOKEN"]
    muestra = json.load(open(MUESTRA))

    acuerdos, total, fallos_json = 0, 0, 0
    for p in muestra:
        texto = p["texto"]
        r = reglas(texto)
        total += 1
        try:
            l = llm(texto, account, token)
        except Exception as e:
            print(f"  LLM error: {str(e)[:60]}")
            l = None
        if not isinstance(l, dict):
            fallos_json += 1
            print(f"JSON_INVALIDO | {texto[:70]!r}")
            continue
        bloques_llm = [b for b in (l.get("bloques") or []) if isinstance(b, int)]
        acc_ok = l.get("accion") == r["accion"]
        blq_ok = sorted(bloques_llm) == r["bloques"]
        if acc_ok and blq_ok:
            acuerdos += 1
        else:
            marca = []
            if not acc_ok: marca.append("acción")
            if not blq_ok: marca.append("bloques")
            print(f"DIVERGE({','.join(marca)}) | reglas={r['accion']}{r['bloques']} vs llm={l.get('accion')}{bloques_llm}")
            print(f"         {texto[:75]!r}")

    print(f"\nAcuerdo acción+bloques: {acuerdos}/{total} | JSON inválido: {fallos_json}")


if __name__ == "__main__":
    main()
