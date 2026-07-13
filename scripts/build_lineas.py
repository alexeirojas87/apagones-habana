"""Genera web/data/zonas_lineas.geojson: geometrías reales (polilíneas) de las
calles mencionadas en cada zona de bloque, obtenidas de OpenStreetMap vía Overpass.

Estrategia: por municipio, una sola consulta Overpass con todos los nombres de
calle de sus zonas; luego se asigna cada vía a su(s) zona(s) localmente por
nombre normalizado. Las zonas sin calles encontradas siguen representándose
con su círculo (el frontend usa este archivo para decidir).

Se corre a mano cuando cambie el PDF de bloques. Respeta al servidor Overpass
(1 consulta por municipio, con pausa).
"""

import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geocode_fallos2 import calles_de  # noqa: E402

RAIZ = os.path.join(os.path.dirname(__file__), "..")
UA = {"User-Agent": "apagones-habana/0.1 (proyecto comunitario)"}


def norm(t):
    t = "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")
    t = re.sub(r"\b(calle|calles|avenida|ave\.?|calzada)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip(" .")


def bboxes():
    with open(os.path.join(RAIZ, "web", "data", "municipios.geojson")) as f:
        gj = json.load(f)
    cajas = {}
    for feat in gj["features"]:
        xs, ys = [], []

        def walk(c):
            if isinstance(c[0], (int, float)):
                xs.append(c[0]); ys.append(c[1])
            else:
                for x in c:
                    walk(x)

        walk(feat["geometry"]["coordinates"])
        cajas[feat["properties"]["municipio"]] = (min(ys), min(xs), max(ys), max(xs))  # s,w,n,e
    return cajas


def overpass(nombres, caja):
    """Vías con highway y nombre en la lista, dentro del bbox del municipio."""
    partes = []
    for n in nombres:
        esc = re.escape(n).replace("\\ ", " ")
        partes.append(f"^(calle |avenida |ave\\.? |calzada de |calzada del )?{esc}$")
    regex = "|".join(partes)
    s, w, nn, e = caja
    q = f'[out:json][timeout:90];way[highway][name~"{regex}",i]({s},{w},{nn},{e});out geom;'
    req = urllib.request.Request(
        "https://overpass-api.de/api/interpreter",
        data=urllib.parse.urlencode({"data": q}).encode(),
        headers=UA,
    )
    return json.load(urllib.request.urlopen(req, timeout=120)).get("elements", [])


def main():
    with open(os.path.join(RAIZ, "data", "bloques.json")) as f:
        bloques = json.load(f)["bloques"]
    cajas = bboxes()

    # nombre normalizado -> [(bloque, municipio, zona)] por municipio
    por_muni = {}
    for b in bloques:
        for g in b["municipios"]:
            for zona in g["zonas"]:
                # SALTA_ALAMAR: las zonas numeradas de Alamar (Habana del Este) NO
                # son calles — sus números chocan con nombres de calle. Las maneja
                # build_poligonos por su Zona N. Aquí se ignoran.
                if g["municipio"] == "Habana del Este" and (
                    re.search(r"\bzonas?\b[\s:]*\(?\d", zona, re.IGNORECASE) or "alamar" in zona.lower()
                ):
                    continue
                for calle in calles_de(zona):
                    n = norm(calle)
                    if len(n) < 2:
                        continue
                    por_muni.setdefault(g["municipio"], {}).setdefault(n, []).append(
                        (b["bloque"], zona)
                    )

    features = []
    zonas_con_linea = set()
    for muni, nombres in por_muni.items():
        lote = sorted(nombres)
        print(f"{muni}: {len(lote)} nombres de calle", flush=True)
        vias = []
        for i in range(0, len(lote), 60):
            try:
                vias += overpass(lote[i : i + 60], cajas[muni])
            except Exception as ex:
                print(f"  ERROR Overpass: {ex}", flush=True)
            time.sleep(4)
        # fusiona: un MultiLineString por (bloque, zona, calle) para que el
        # frontend cree pocas capas (miles de tramos sueltos congelan el navegador)
        grupos = {}
        for via in vias:
            n = norm(via.get("tags", {}).get("name", ""))
            for bloque, zona in nombres.get(n, []):
                coords = [[round(p["lon"], 5), round(p["lat"], 5)] for p in via["geometry"]]
                grupos.setdefault((bloque, zona[:160], via["tags"]["name"]), []).append(coords)
                zonas_con_linea.add(f"{muni}|{zona[:160]}")
        for (bloque, zona, calle), lineas in grupos.items():
            features.append(
                {
                    "type": "Feature",
                    "properties": {"bloque": bloque, "municipio": muni, "zona": zona, "calle": calle},
                    "geometry": {"type": "MultiLineString", "coordinates": lineas},
                }
            )
        print(f"  {len(vias)} vías -> {len(grupos)} features", flush=True)

    destino = os.path.join(RAIZ, "web", "data", "zonas_lineas.geojson")
    json.dump({"type": "FeatureCollection", "features": features}, open(destino, "w"), ensure_ascii=False)
    total_zonas = sum(len(g["zonas"]) for b in bloques for g in b["municipios"])
    print(f"{len(features)} tramos de calle | {len(zonas_con_linea)}/{total_zonas} zonas con geometría real")


if __name__ == "__main__":
    main()
