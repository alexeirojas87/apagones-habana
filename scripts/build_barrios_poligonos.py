"""Genera web/data/barrios_poligonos.json: límites reales (de OSM) de todos los
barrios/repartos de La Habana que estén mapeados como área — tanto place=* como
landuse con nombre. Lo usa el frontend para pintar zonas DAF, cortes de
emergencia y barrios protegidos como polígono en vez de punto, cuando existe.

Formato: { nombre_canonico: {"nombre": str, "anillo": [[lon, lat], ...]} }
"""

import json
import os
import re
import unicodedata
import urllib.parse
import urllib.request

RAIZ = os.path.join(os.path.dirname(__file__), "..")
UA = {"User-Agent": "apagones-habana/0.1 (proyecto comunitario)"}


def canon(t):
    t = "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")
    t = re.sub(r"\s*\(.*?\)", "", t)  # sin sufijos entre paréntesis
    t = re.sub(r"\b(reparto|rpto\.?|barrio|residencial)\b", " ", t)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", t)).strip()


def main():
    q = """[out:json][timeout:150];
area["name"="La Habana"]["admin_level"="4"]->.hab;
( way(area.hab)[place][name];
  way(area.hab)[landuse~"^(residential|retail|commercial|military|institutional)$"][name];
);
out geom;"""
    req = urllib.request.Request(
        "https://overpass-api.de/api/interpreter",
        data=urllib.parse.urlencode({"data": q}).encode(),
        headers=UA,
    )
    data = json.load(urllib.request.urlopen(req, timeout=180))

    salida = {}
    for el in data.get("elements", []):
        geom = el.get("geometry", [])
        if len(geom) < 4 or geom[0] != geom[-1]:
            continue
        nombre = el["tags"]["name"]
        clave = canon(nombre)
        if len(clave) < 4 or clave in salida:  # el primero gana (place antes que landuse)
            continue
        salida[clave] = {
            "nombre": nombre,
            "anillo": [[round(p["lon"], 5), round(p["lat"], 5)] for p in geom],
        }

    destino = os.path.join(RAIZ, "web", "data", "barrios_poligonos.json")
    json.dump(salida, open(destino, "w"), ensure_ascii=False)
    print(f"{len(salida)} barrios con polígono ->", destino)
    for prueba in ["camilo cienfuegos", "villa panamericana", "monte barreto", "cojimar"]:
        print(f"  {prueba}: {'✓' if prueba in salida else '—'}")


if __name__ == "__main__":
    main()
