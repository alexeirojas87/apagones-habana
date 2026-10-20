#!/usr/bin/env python3
"""Alinea el catálogo de circuitos con la capa de geometrías.

Problema: `build_circuitos.py` geocodifica la descripción textual de cada
circuito con Nominatim. Cuando la descripción es ambigua (calles homónimas,
"Loma", "Tulipán", etc.) el punto cae en otro municipio y el mapa dibuja la
bolita de respaldo lejos del circuito real.

La capa `web/data/circuitos_geometrias.geojson` tiene el polígono curado de
cada circuito. Para los códigos que tienen polígono, este paso:

- mueve `lat`/`lon` al centro representativo del polígono (el punto que usa
  el buscador y el diagnóstico; el mapa ya dibuja el polígono);
- corrige `municipio`/`municipios` cuando el municipio del catálogo no es
  ninguno de los que toca el polígono;
- aplica overrides manuales de `data/correcciones.json` (`circuitos_ubicacion`)
  para códigos sin polígono, como P15.

No toca `calles`, `estado`, horas ni el resto del catálogo. Es idempotente y
se puede ejecutar sobre `web/data/circuitos.json` o sobre una copia.
"""

import json
import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CATALOGO = RAIZ / "web" / "data" / "circuitos.json"
GEOMETRIAS = RAIZ / "web" / "data" / "circuitos_geometrias.geojson"
MUNICIPIOS = RAIZ / "web" / "data" / "municipios.geojson"
CORRECCIONES = RAIZ / "data" / "correcciones.json"


def en_poly(lat, lon, ring):
    """Ray casting: ¿(lat, lon) dentro del anillo [[lon, lat], ...]?"""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def anillos(feature):
    """Anillos exteriores de un Polygon/MultiPolygon."""
    geom = feature.get("geometry") or {}
    if geom.get("type") == "Polygon":
        coords = [geom.get("coordinates") or []]
    elif geom.get("type") == "MultiPolygon":
        coords = geom.get("coordinates") or []
    else:
        return []
    return [p[0] for p in coords if p and len(p[0]) >= 4]


def area(ring):
    s = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def centro(ring):
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return sum(ys) / len(ys), sum(xs) / len(xs)  # lat, lon


def punto_representativo(features):
    """Punto dentro del polígono más grande del código."""
    mejor = None
    for f in features:
        for ring in anillos(f):
            if mejor is None or area(ring) > area(mejor):
                mejor = ring
    if not mejor:
        return None
    lat, lon = centro(mejor)
    if en_poly(lat, lon, mejor):
        return lat, lon
    # Polígono cóncavo: probar vértices hasta encontrar uno interior.
    for lon, lat in mejor[:-1]:
        if en_poly(lat, lon, mejor):
            return lat, lon
    return lat, lon


def municipios_de(features, municipios):
    """Municipios que tocan los vértices del polígono, ordenados por frecuencia."""
    cuenta = Counter()
    for f in features:
        for ring in anillos(f):
            for lon, lat in ring[:-1]:
                for nombre, mring in municipios:
                    if en_poly(lat, lon, mring):
                        cuenta[nombre] += 1
                        break
    return [n for n, _ in cuenta.most_common()]


def cargar_municipios():
    with open(MUNICIPIOS, encoding="utf-8") as f:
        gj = json.load(f)
    return [
        (ft["properties"]["municipio"], ft["geometry"]["coordinates"][0])
        for ft in gj["features"]
    ]


def overrides():
    try:
        with open(CORRECCIONES, encoding="utf-8") as f:
            return json.load(f).get("circuitos_ubicacion", {}) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def alias_map():
    """Código truncado/erróneo -> canónico (data/correcciones.json)."""
    try:
        with open(CORRECCIONES, encoding="utf-8") as f:
            crudo = json.load(f).get("circuitos_alias", {}) or {}
    except (OSError, json.JSONDecodeError):
        return {}
    salida = {}
    for k, v in crudo.items():
        destino = (v or {}).get("alias_de") if isinstance(v, dict) else v
        if destino:
            salida[str(k).strip().upper()] = str(destino).strip().upper()
    return salida


def aplicar_alias(circuitos, alias=None):
    """Elimina del catálogo los códigos que son alias de un canónico presente."""
    if alias is None:
        alias = alias_map()
    presentes = {str(c.get("codigo") or "").upper() for c in circuitos}
    fuera = []
    for c in list(circuitos):
        cod = str(c.get("codigo") or "").upper()
        destino = alias.get(cod)
        if destino and destino != cod and destino in presentes:
            circuitos.remove(c)
            fuera.append(cod)
    return fuera


def alinear(circuitos, municipios=None, geometrias=None, manual=None):
    """Modifica `circuitos` in-place. Devuelve un resumen."""
    if municipios is None:
        municipios = cargar_municipios()
    if geometrias is None:
        with open(GEOMETRIAS, encoding="utf-8") as f:
            geometrias = json.load(f)["features"]
    if manual is None:
        manual = overrides()

    por_codigo = {}
    for f in geometrias:
        props = f.get("properties") or {}
        for cod in props.get("codigos") or []:
            por_codigo.setdefault(cod.upper(), []).append(f)

    stats = {"movidos": 0, "municipio_corregido": 0, "manual": 0,
             "sin_poligono": 0, "alias": 0}
    for c in circuitos:
        cod = str(c.get("codigo") or "").upper()
        if not cod:
            continue

        features = por_codigo.get(cod)
        if features:
            punto = punto_representativo(features)
            if punto:
                lat, lon = round(punto[0], 5), round(punto[1], 5)
                if c.get("lat") != lat or c.get("lon") != lon:
                    stats["movidos"] += 1
                c["lat"], c["lon"] = lat, lon

            toca = municipios_de(features, municipios)
            prop_muni = next(
                (f["properties"].get("municipio") for f in features
                 if (f.get("properties") or {}).get("municipio")),
                None,
            )
            actual = c.get("municipio")
            if toca and actual not in toca:
                nuevo = prop_muni if prop_muni in toca else toca[0]
                if actual != nuevo:
                    stats["municipio_corregido"] += 1
                c["municipio"] = nuevo
            if toca:
                c["municipios"] = toca
        else:
            stats["sin_poligono"] += 1

        if cod in manual:
            o = manual[cod]
            if o.get("lat") is not None and o.get("lon") is not None:
                c["lat"], c["lon"] = o["lat"], o["lon"]
            if o.get("municipio"):
                c["municipio"] = o["municipio"]
                c["municipios"] = [o["municipio"]]
            stats["manual"] += 1

    stats["alias"] = len(aplicar_alias(circuitos))
    return stats


def main():
    ruta = Path(sys.argv[1]) if len(sys.argv) > 1 else CATALOGO
    with open(ruta, encoding="utf-8") as f:
        datos = json.load(f)
    circuitos = datos.get("circuitos") or datos
    stats = alinear(circuitos)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False)
    print(
        f"{ruta}: {stats['movidos']} centroides alineados, "
        f"{stats['municipio_corregido']} municipios corregidos, "
        f"{stats['manual']} overrides manuales, "
        f"{stats['alias']} alias eliminados, "
        f"{stats['sin_poligono']} sin polígono"
    )


if __name__ == "__main__":
    main()
