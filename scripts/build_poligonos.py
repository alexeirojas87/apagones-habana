"""Genera web/data/zonas_poligonos.geojson: polígonos reales de barrios/repartos
(de OpenStreetMap) para las zonas de bloque que corresponden a áreas con nombre
en vez de calles — Alamar (Zona N), Cojímar, repartos, etc.

El frontend pinta estos polígonos rellenos con el estado del bloque; tienen
prioridad sobre líneas de calles y círculos para la misma zona.
"""

import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geocode_fallos2 import calles_de  # noqa: E402

RAIZ = os.path.join(os.path.dirname(__file__), "..")
UA = {"User-Agent": "apagones-habana/0.1 (proyecto comunitario)"}


def norm(t):
    t = "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")
    t = re.sub(r"\b(reparto|rpto\.?|barrio|residencial|el|la|los|las|de|del)\b", " ", t)
    t = re.sub(r"\balturas\b", "altura", t)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", t)).strip()


def poligonos_osm():
    """Todos los barrios de La Habana que están mapeados como áreas (ways cerrados)."""
    q = """[out:json][timeout:120];
area["name"="La Habana"]["admin_level"="4"]->.hab;
way(area.hab)[place~"^(suburb|neighbourhood|quarter)$"][name];
out geom;"""
    req = urllib.request.Request(
        "https://overpass-api.de/api/interpreter",
        data=urllib.parse.urlencode({"data": q}).encode(),
        headers=UA,
    )
    data = json.load(urllib.request.urlopen(req, timeout=150))
    polys = {}
    for el in data.get("elements", []):
        geom = el.get("geometry", [])
        if len(geom) < 4 or geom[0] != geom[-1]:  # solo ways cerrados
            continue
        nombre = el["tags"]["name"]
        coords = [[round(p["lon"], 5), round(p["lat"], 5)] for p in geom]
        polys.setdefault(norm(nombre), []).append({"nombre": nombre, "coords": coords})
    return polys


def es_alamar_numerada(municipio, zona):
    return municipio == "Habana del Este" and (
        re.search(r"\bzonas?\b[\s:]*\(?\d", zona, re.IGNORECASE) or "alamar" in zona.lower()
    )


def zonas_alamar(zona):
    """Números de zona de una descripción de Alamar -> ['Zona 12', ...]."""
    return [f"Zona {n}" for n in
            re.findall(r"\b(\d{1,2})\b", re.sub(r"micro\s*\w+", "", zona, flags=re.IGNORECASE))]


def nombres_de_zona(municipio, zona):
    """Nombres de área candidatos para una zona (no-Alamar)."""
    return calles_de(zona)


def main():
    with open(os.path.join(RAIZ, "data", "bloques.json")) as f:
        bloques = json.load(f)["bloques"]
    polys = poligonos_osm()
    print(f"{sum(len(v) for v in polys.values())} polígonos de barrio en OSM")

    # exclusiones manuales: barrios cuyo polígono OSM sobrepasa la zona real
    import correcciones
    excluidos = {(e["municipio"], norm(e["nombre"])) for e in correcciones.excluir_poligono_osm()}

    with open(os.path.join(RAIZ, "data", "barrios_osm.json")) as f:
        nodos = {}
        for x in json.load(f):
            nodos.setdefault(norm(x["nombre"]), []).append(x)

    # cajas de municipio para validar que un barrio homónimo sea el correcto
    with open(os.path.join(RAIZ, "web", "data", "municipios.geojson")) as f:
        cajas = {}
        for feat in json.load(f)["features"]:
            xs, ys = [], []

            def walk(c):
                if isinstance(c[0], (int, float)):
                    xs.append(c[0]); ys.append(c[1])
                else:
                    for x in c:
                        walk(x)

            walk(feat["geometry"]["coordinates"])
            cajas[feat["properties"]["municipio"]] = (min(xs) - 0.02, min(ys) - 0.02, max(xs) + 0.02, max(ys) + 0.02)

    def en_municipio(lon, lat, muni):
        if muni not in cajas:
            return True
        x0, y0, x1, y1 = cajas[muni]
        return x0 <= lon <= x1 and y0 <= lat <= y1

    # zonas que ya tienen calles pintadas (el punto de barrio solo complementa)
    con_lineas = set()
    ruta_lineas = os.path.join(RAIZ, "web", "data", "zonas_lineas.geojson")
    if os.path.exists(ruta_lineas):
        with open(ruta_lineas) as f:
            for feat in json.load(f)["features"]:
                p = feat["properties"]
                con_lineas.add(f"{p['municipio']}|{p['zona']}")

    features, zonas_cubiertas = [], set()
    for b in bloques:
        for g in b["municipios"]:
            for zona in g["zonas"]:
                clave_zona = f"{g['municipio']}|{zona[:160]}"
                # Alamar: los números de zona chocan con nombres de calle, así que
                # se resuelven SIEMPRE por su Zona N (polígono OSM o punto), sin pasar
                # por la lógica de calles ni el filtro con_lineas. Evita que se pierdan.
                if es_alamar_numerada(g["municipio"], zona):
                    for nombre in dict.fromkeys(zonas_alamar(zona)):
                        props = {"bloque": b["bloque"], "municipio": g["municipio"],
                                 "zona": zona[:160], "nombre": nombre}
                        polis = polys.get(norm(nombre))
                        if polis:
                            features.append({"type": "Feature", "properties": {**props, "nombre": polis[0]["nombre"]},
                                             "geometry": {"type": "Polygon", "coordinates": [polis[0]["coords"]]}})
                        else:
                            nd = next((x for x in nodos.get(norm(nombre), [])), None)
                            if nd:
                                features.append({"type": "Feature", "properties": props,
                                                 "geometry": {"type": "Point", "coordinates": [nd["lon"], nd["lat"]]}})
                        zonas_cubiertas.add(clave_zona)
                    continue
                for nombre in dict.fromkeys(nombres_de_zona(g["municipio"], zona)):
                    if (g["municipio"], norm(nombre)) in excluidos:
                        continue
                    props = {
                        "bloque": b["bloque"],
                        "municipio": g["municipio"],
                        "zona": zona[:160],
                        "nombre": nombre,
                    }
                    encontrados = [
                        p for p in polys.get(norm(nombre), [])
                        if en_municipio(p["coords"][0][0], p["coords"][0][1], g["municipio"])
                    ]
                    for p in encontrados:
                        features.append(
                            {
                                "type": "Feature",
                                "properties": {**props, "nombre": p["nombre"]},
                                "geometry": {"type": "Polygon", "coordinates": [p["coords"]]},
                            }
                        )
                        zonas_cubiertas.add(clave_zona)
                    # sin polígono y sin calles pintadas: punto del barrio (toda la ciudad)
                    if not encontrados and clave_zona not in con_lineas:
                        for nd in nodos.get(norm(nombre), []):
                            if en_municipio(nd["lon"], nd["lat"], g["municipio"]):
                                features.append(
                                    {
                                        "type": "Feature",
                                        "properties": {**props, "nombre": nd["nombre"]},
                                        "geometry": {"type": "Point", "coordinates": [nd["lon"], nd["lat"]]},
                                    }
                                )
                                zonas_cubiertas.add(clave_zona)
                                break

    # Barrios cuya membresía de bloque aprendimos del canal (omitidos por el PDF):
    # se pintan con el estado de su bloque, con polígono OSM si existe, si no punto.
    ruta_apr = os.path.join(RAIZ, "data", "bloques_aprendidos.json")
    aprendidos = json.load(open(ruta_apr)) if os.path.exists(ruta_apr) else {}
    # texto del PDF por bloque: un barrio ya presente ahí NO se añade como aprendido
    # (evita re-colorear con ruido barrios que el PDF ya ubica, p. ej. Cojímar).
    texto_pdf = norm(" ".join(z for b in bloques for g in b["municipios"] for z in g["zonas"]))
    nodos_por_nombre = {}
    with open(os.path.join(RAIZ, "data", "barrios_osm.json")) as f:
        for x in json.load(f):
            nodos_por_nombre.setdefault(norm(x["nombre"]), x)
    añadidos = 0
    for nombre, bloque in aprendidos.items():
        nn = norm(nombre)
        if len(nn) < 5 or re.search(rf"\b{re.escape(nn)}\b", texto_pdf):
            continue  # el PDF ya lo cubre: no es una omisión
        añadidos += 1
        props = {"bloque": bloque, "municipio": "", "zona": f"{nombre} (aprendido del canal)", "nombre": nombre}
        polis = polys.get(nn)
        if polis:
            features.append({"type": "Feature", "properties": props,
                             "geometry": {"type": "Polygon", "coordinates": [polis[0]["coords"]]}})
        elif nn in nodos_por_nombre:
            nd = nodos_por_nombre[nn]
            features.append({"type": "Feature", "properties": props,
                             "geometry": {"type": "Point", "coordinates": [nd["lon"], nd["lat"]]}})

    # Polígonos manuales (barrios que el matching automático cubre mal, p. ej.
    # Cojímar recortado para no invadir la Villa Panamericana): relleno sólido.
    for m in correcciones.poligonos_manuales():
        features.append({
            "type": "Feature",
            "properties": {"bloque": m["bloque"], "municipio": m["municipio"],
                           "zona": f"{m['nombre']} (barrio completo)", "nombre": m["nombre"]},
            "geometry": {"type": "Polygon", "coordinates": [m["anillo"]]},
        })

    destino = os.path.join(RAIZ, "web", "data", "zonas_poligonos.geojson")
    json.dump({"type": "FeatureCollection", "features": features}, open(destino, "w"), ensure_ascii=False)
    print(f"{len(features)} polígonos asignados | {len(zonas_cubiertas)} zonas con polígono | {len(aprendidos)} aprendidos")


if __name__ == "__main__":
    main()
