"""Reintento de las zonas del PDF que no se pudieron geocodificar, asistido por
LLM. El LLM NO da coordenadas (las inventaría): solo desenreda la descripción y
devuelve términos buscables limpios (un punto de interés, o una calle + reparto
de contexto). Esos términos van a Nominatim, que sí tiene coordenadas reales.

Actualiza data/geocache.json con los aciertos; luego correr geocode_zonas.py
para regenerar web/data/zonas.geojson (usa la caché, no re-consulta).

Env: CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_AI_TOKEN
"""

import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geocode_zonas import nominatim, bbox_municipios, MUNICIPIOS_NORM  # noqa: E402

RAIZ = os.path.join(os.path.dirname(__file__), "..")
CACHE = os.path.join(RAIZ, "data", "geocache.json")
FALLOS = os.path.join(RAIZ, "data", "geocode_fallos.txt")
MODELO = os.environ.get("MODELO", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")

PROMPT = (
    "Eres un asistente de geocodificación para La Habana, Cuba. Recibes la "
    "descripción de una zona eléctrica y devuelves SOLO un JSON, sin texto extra:\n"
    '{"tipo": "punto"|"calle", '
    '"buscar": [términos buscables en OpenStreetMap, del más específico al más general], '
    '"reparto": nombre del reparto/barrio de contexto o null}\n'
    "'punto' = un lugar con nombre propio (hospital, acueducto, fábrica, bombeo, zona franca, "
    "edificio). 'calle' = tramos de calles. En 'buscar' pon nombres LIMPIOS sin 'desde/hasta/"
    "cuadrante' (p. ej. de 'Calle 70 desde 3ra hasta 11 (Almendares)' -> buscar:['Calle 70'], "
    "reparto:'Almendares'). NO inventes coordenadas ni lugares que no estén en el texto."
)


def llm(texto, account, token):
    body = json.dumps({
        "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": texto[:600]}],
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{MODELO}",
        data=body, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    r = json.load(urllib.request.urlopen(req, timeout=60)).get("result", {})
    salida = r.get("response")
    if not isinstance(salida, str):
        try:
            salida = r["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None
    salida = salida.replace("```json", "").replace("```", "")
    m = re.search(r"\{.*\}", salida, re.DOTALL)
    try:
        return json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None


def main():
    account, token = os.environ["CLOUDFLARE_ACCOUNT_ID"], os.environ["CLOUDFLARE_AI_TOKEN"]
    cajas = bbox_municipios()
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}

    fallos = [l.strip() for l in open(FALLOS) if l.strip()]
    recuperadas = 0
    for linea in fallos:
        _, municipio, zona = [x.strip() for x in linea.split("|", 2)]
        clave = f"{municipio}|{zona}"
        if cache.get(clave):  # ya resuelto en una corrida previa
            continue
        hints = llm(zona, account, token)
        if not hints or not hints.get("buscar"):
            continue
        caja = cajas.get(municipio)
        reparto = hints.get("reparto")
        hit = None
        for termino in hints["buscar"][:3]:
            consultas = []
            if reparto:
                consultas.append(f"{termino}, {reparto}, {municipio}, La Habana, Cuba")
            consultas.append(f"{termino}, {municipio}, La Habana, Cuba")
            for q in consultas:
                hit = nominatim(q, caja)
                time.sleep(1.1)
                if hit:
                    break
            if hit:
                break
        if hit:
            hit["candidato"] = "LLM+Nominatim"
            cache[clave] = hit
            recuperadas += 1
            print(f"  ✓ {zona[:45]:45} -> {hit['match'][:30]}")
        json.dump(cache, open(CACHE, "w"), ensure_ascii=False)

    print(f"\nRecuperadas {recuperadas}/{len(fallos)} zonas con LLM+Nominatim")


if __name__ == "__main__":
    main()
