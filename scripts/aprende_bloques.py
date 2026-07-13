"""Aprende la membresía barrio->bloque desde los posts oficiales del canal.

El PDF de bloques está incompleto (omite zonas como Aldabó o la Zona 1 de Alamar).
Pero cada aviso oficial que menciona un circuito junto a su bloque es evidencia
autoritativa de a qué bloque pertenece. Este script acumula esa evidencia:
recorre los eventos del canal con bloque conocido, cruza los nombres de barrio
del catálogo OSM contra el texto de sus zonas, y emite data/bloques_aprendidos.json
{barrio: bloque}. Lo usan build_barrios (para no marcarlos como protegida/DAF) y
build_poligonos (para pintarlos con el estado de su bloque).

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY
"""

import json
import os
import re
import unicodedata
from collections import defaultdict

from supabase import create_client

RAIZ = os.path.join(os.path.dirname(__file__), "..")
MIN_LEN = 5  # nombres muy cortos generan falsos positivos por subcadena


def norm(t):
    t = "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")
    t = re.sub(r"\b(reparto|rpto\.?|barrio|residencial)\b", " ", t)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", t)).strip()


def todos(sb, tabla, cols, filtro):
    filas, desde = [], 0
    while True:
        q = sb.table(tabla).select(cols)
        for k, v in filtro.items():
            q = q.eq(k, v)
        lote = q.range(desde, desde + 999).execute().data
        filas += lote
        if len(lote) < 1000:
            return filas
        desde += 1000


def main():
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

    # texto de zonas agregado por bloque (solo eventos oficiales con bloque 1-6)
    texto_bloque = defaultdict(list)
    for e in todos(sb, "eventos", "bloque,zonas", {"chat": "canal"}):
        if e["bloque"] and 1 <= e["bloque"] <= 6:
            texto_bloque[e["bloque"]].append(" ".join(e["zonas"] or []))
    texto_bloque = {b: norm(" ".join(t)) for b, t in texto_bloque.items()}

    with open(os.path.join(RAIZ, "data", "barrios_osm.json")) as f:
        barrios = json.load(f)

    # cuenta menciones barrio->bloque; el barrio se asigna al bloque que más lo cita
    conteo = defaultdict(lambda: defaultdict(int))
    for barrio in barrios:
        n = norm(barrio["nombre"])
        if len(n) < MIN_LEN:
            continue
        for b, texto in texto_bloque.items():
            if re.search(rf"\b{re.escape(n)}\b", texto):
                conteo[barrio["nombre"]][b] += 1

    aprendidos = {
        nombre: max(bloques.items(), key=lambda kv: kv[1])[0]
        for nombre, bloques in conteo.items()
    }
    destino = os.path.join(RAIZ, "data", "bloques_aprendidos.json")
    json.dump(aprendidos, open(destino, "w"), ensure_ascii=False, indent=0)
    print(f"{len(aprendidos)} barrios con bloque aprendido del canal")
    for n, b in list(aprendidos.items())[:12]:
        print(f"  B{b}: {n}")


if __name__ == "__main__":
    main()
