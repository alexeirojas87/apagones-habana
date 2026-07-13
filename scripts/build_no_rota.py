"""Genera web/data/no_rota.geojson: el "espacio negativo" del mapa — celdas de
la ciudad que quedan lejos de toda zona de bloque conocida y de los circuitos
DAF. Es la visualización de la regla "sin registro de afectaciones =
probablemente no rota" (derivación por exclusión, no dato oficial).

Malla de ~270 m; una celda es azul si:
  - su centro cae dentro de algún municipio, y
  - está a más de DIST_BLOQUE de cualquier geometría de zona de bloque, y
  - está a más de DIST_DAF de un circuito DAF.
Las celdas contiguas de una misma fila se funden en rectángulos.
"""

import json
import math
import os

from shapely.geometry import shape, Point
from shapely.prepared import prep

RAIZ = os.path.join(os.path.dirname(__file__), "..")
PASO = 0.0025          # ~270 m
DIST_BLOQUE = 0.0028   # ~300 m
DIST_DAF = 0.0035      # ~380 m


def cargar(nombre):
    with open(os.path.join(RAIZ, "web", "data", nombre)) as f:
        return json.load(f)


def puntos_de_bloques():
    pts = []
    for f in cargar("zonas_lineas.geojson")["features"]:
        coords = f["geometry"]["coordinates"]
        lineas = coords if f["geometry"]["type"] == "MultiLineString" else [coords]
        for linea in lineas:
            pts.extend(linea[::3])
    for f in cargar("zonas_poligonos.geojson")["features"]:
        g = f["geometry"]
        pts.extend(g["coordinates"][0][::2] if g["type"] == "Polygon" else [g["coordinates"]])
    for f in cargar("zonas.geojson")["features"]:
        pts.append(f["geometry"]["coordinates"])
    return pts


def poligonos_de_bloques():
    """Zonas RELLENAS (cuadrantes y barrios con polígono): sus interiores están
    cubiertos, así que se excluyen del azul por contención, no por distancia."""
    polis = []
    for nombre in ("zonas_cuadrantes.geojson", "zonas_poligonos.geojson"):
        for f in cargar(nombre)["features"]:
            if f["geometry"]["type"] == "Polygon":
                polis.append(prep(shape(f["geometry"])))
    return polis


def indexar(pts, celda):
    idx = {}
    for x, y in pts:
        idx.setdefault((int(x / celda), int(y / celda)), []).append((x, y))
    return idx


def cerca(idx, celda, x, y, radio):
    cx, cy = int(x / celda), int(y / celda)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for px, py in idx.get((cx + dx, cy + dy), ()):
                if math.hypot(px - x, py - y) <= radio:
                    return True
    return False


def main():
    municipios = [prep(shape(f["geometry"])) for f in cargar("municipios.geojson")["features"]]
    bloques_idx = indexar(puntos_de_bloques(), DIST_BLOQUE)
    zonas_rellenas = poligonos_de_bloques()
    daf_pts = [(b["lon"], b["lat"]) for b in cargar("barrios.json") if b["cat"] == "daf"]
    daf_idx = indexar(daf_pts, DIST_DAF)

    x0, y0, x1, y1 = -82.70, 22.90, -81.90, 23.35
    filas = {}
    ny = int((y1 - y0) / PASO)
    nx = int((x1 - x0) / PASO)
    total_azules = 0
    for j in range(ny):
        y = y0 + (j + 0.5) * PASO
        for i in range(nx):
            x = x0 + (i + 0.5) * PASO
            if cerca(bloques_idx, DIST_BLOQUE, x, y, DIST_BLOQUE):
                continue
            if cerca(daf_idx, DIST_DAF, x, y, DIST_DAF):
                continue
            p = Point(x, y)
            # dentro de una zona rellena de bloque (cuadrante/barrio) -> no es azul
            if any(z.contains(p) for z in zonas_rellenas):
                continue
            if not any(m.contains(p) for m in municipios):
                continue
            filas.setdefault(j, []).append(i)
            total_azules += 1

    # fusionar celdas contiguas por fila en rectángulos
    features = []
    for j, cols in filas.items():
        cols.sort()
        ini = prev = cols[0]
        tramos = []
        for c in cols[1:]:
            if c == prev + 1:
                prev = c
            else:
                tramos.append((ini, prev)); ini = prev = c
        tramos.append((ini, prev))
        for a, b in tramos:
            features.append(
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [round(x0 + a * PASO, 5), round(y0 + j * PASO, 5)],
                            [round(x0 + (b + 1) * PASO, 5), round(y0 + j * PASO, 5)],
                            [round(x0 + (b + 1) * PASO, 5), round(y0 + (j + 1) * PASO, 5)],
                            [round(x0 + a * PASO, 5), round(y0 + (j + 1) * PASO, 5)],
                            [round(x0 + a * PASO, 5), round(y0 + j * PASO, 5)],
                        ]],
                    },
                }
            )

    destino = os.path.join(RAIZ, "web", "data", "no_rota.geojson")
    json.dump({"type": "FeatureCollection", "features": features}, open(destino, "w"), ensure_ascii=False)
    print(f"{total_azules} celdas azules -> {len(features)} rectángulos fusionados")


if __name__ == "__main__":
    main()
