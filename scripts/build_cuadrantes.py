"""Genera web/data/zonas_cuadrantes.geojson: polígono relleno para las zonas
descritas como 'cuadrante' — la envolvente convexa de sus calles frontera
(todo lo de adentro pertenece a la zona).

Con límites de sanidad: se descartan envolventes menores de 0.01 km² (calles
colineales) o mayores de 4 km² (alguna calle frontera matcheó de más).
Se corre después de build_lineas.py.
"""

import json
import os
import unicodedata

RAIZ = os.path.join(os.path.dirname(__file__), "..")


def hull(puntos):
    """Envolvente convexa (cadena monótona de Andrew). puntos: [(lon, lat)]"""
    pts = sorted(set(puntos))
    if len(pts) < 3:
        return None
    def cruz(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    inf, sup = [], []
    for p in pts:
        while len(inf) >= 2 and cruz(inf[-2], inf[-1], p) <= 0:
            inf.pop()
        inf.append(p)
    for p in reversed(pts):
        while len(sup) >= 2 and cruz(sup[-2], sup[-1], p) <= 0:
            sup.pop()
        sup.append(p)
    return inf[:-1] + sup[:-1]


def area_km2(anillo):
    """Área aproximada por fórmula del polígono (grados -> km en La Habana)."""
    a = 0.0
    for i in range(len(anillo)):
        x1, y1 = anillo[i]
        x2, y2 = anillo[(i + 1) % len(anillo)]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2 * 102 * 111  # deg² -> km²


def sin_acentos(t):
    return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")


def main():
    with open(os.path.join(RAIZ, "web", "data", "zonas_lineas.geojson")) as f:
        lineas = json.load(f)["features"]

    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import correcciones
    extras = correcciones.rellenar_como_area()

    def extra_de(p):
        for e in extras:
            if e["municipio"] == p["municipio"] and p["zona"].startswith(e["empieza"]):
                return e
        return None

    def merece_relleno(p):
        return "cuadrante" in sin_acentos(p["zona"]) or extra_de(p) is not None

    # agrupar todas las coordenadas por zona (cuadrantes + extras manuales)
    grupos = {}
    for feat in lineas:
        p = feat["properties"]
        if not merece_relleno(p):
            continue
        e = extra_de(p)
        if e and any(x.lower() in p.get("calle", "").lower() for x in e.get("excluir_calles", [])):
            continue  # calle frontera que distorsionaría la envolvente
        clave = (p["bloque"], p["municipio"], p["zona"])
        coords = feat["geometry"]["coordinates"]
        planos = [pt for linea in coords for pt in linea] if feat["geometry"]["type"] == "MultiLineString" else coords
        grupos.setdefault(clave, []).extend(tuple(pt) for pt in planos)

    features, descartados = [], 0
    for (bloque, muni, zona), puntos in grupos.items():
        anillo = hull(puntos)
        if not anillo:
            continue
        a = area_km2(anillo)
        if not (0.01 <= a <= 4):
            descartados += 1
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {"bloque": bloque, "municipio": muni, "zona": zona, "area_km2": round(a, 2)},
                "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in anillo + anillo[:1]]]},
            }
        )

    destino = os.path.join(RAIZ, "web", "data", "zonas_cuadrantes.geojson")
    json.dump({"type": "FeatureCollection", "features": features}, open(destino, "w"), ensure_ascii=False)
    print(f"{len(grupos)} zonas con 'cuadrante' -> {len(features)} rellenos ({descartados} descartados por tamaño)")


if __name__ == "__main__":
    main()
