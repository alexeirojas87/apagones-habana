#!/usr/bin/env python3
"""Genera la capa de geometrías de circuitos para el mapa.

La base es la capa pública de Info UNE (polígonos curados con bordes sobre
calles OSM). Los códigos que esa capa no cubre se completan con una forma
provisional derivada de las calles UNE ya geocodificadas del catálogo
(buffer + disolución + simplificación), siempre marcada como provisional.

Uso: python3 scripts/build_circuitos_geometrias.py
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from shapely.geometry import LineString, shape
from shapely.ops import unary_union

RAIZ = Path(__file__).resolve().parent.parent
URL_FUENTE = "https://infoune.duckdns.org/api/v1/geometrias/circuitos-lh"
USER_AGENT = "apagones-habana/0.1 (proyecto comunitario)"
DESTINO = RAIZ / "web" / "data" / "circuitos_geometrias.geojson"
CATALOGO = RAIZ / "web" / "data" / "circuitos.json"

DECIMALES = 6
BUFFER_GRADOS = 0.0012   # ~120 m alrededor de cada calle
SIMPLIFY_GRADOS = 0.00015

# Solo coma y punto y coma separan códigos: "2 203" es un único código.
_SEPARADOR_CODIGOS = re.compile(r"[,;]")

FUENTE_BASE = "Info UNE"
FUENTE_PROVISIONAL = "apagones-habana (calles UNE geocodificadas)"
NOTA = (
    "Delimitaciones de circuitos. La capa base proviene de Info UNE; "
    "las formas provisionales se derivan de calles UNE geocodificadas."
)


def descargar(url):
    peticion = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(peticion, timeout=60) as respuesta:
        return json.load(respuesta)


def normalizar_codigos(valor):
    """Divide solo por coma/punto y coma, mayúsculas y sin vacíos."""
    if valor is None:
        return []
    codigos = []
    for parte in _SEPARADOR_CODIGOS.split(str(valor)):
        codigo = parte.strip().upper()
        if codigo:
            codigos.append(codigo)
    return codigos


def redondear(coordenadas):
    """Redondea recursivamente cualquier anidamiento de coordenadas."""
    if isinstance(coordenadas, (int, float)):
        return round(float(coordenadas), DECIMALES)
    return [redondear(valor) for valor in coordenadas]


def anillos_exteriores_validos(geometria):
    """Polígono con anillo exterior de al menos 4 posiciones."""
    if geometria.geom_type == "Polygon":
        return len(geometria.exterior.coords) >= 4
    if geometria.geom_type == "MultiPolygon":
        return len(geometria.geoms) > 0 and all(
            len(poligono.exterior.coords) >= 4 for poligono in geometria.geoms
        )
    return False


def normalizar_feature(feature):
    """Normaliza una feature de Info UNE; None si no es un polígono usable."""
    propiedades = feature.get("properties") or {}
    codigos = normalizar_codigos(propiedades.get("circuito_id"))
    if not codigos:
        return None

    geometria = feature.get("geometry") or {}
    tipo = geometria.get("type")
    if tipo not in ("Polygon", "MultiPolygon"):
        return None

    coordenadas = redondear(geometria.get("coordinates") or [])
    forma = shape({"type": tipo, "coordinates": coordenadas})
    if not anillos_exteriores_validos(forma):
        return None
    if not forma.is_valid or forma.area <= 0:
        return None

    return {
        "type": "Feature",
        "geometry": {"type": tipo, "coordinates": coordenadas},
        "properties": {
            "codigos": codigos,
            "codigo": codigos[0],
            "municipio": propiedades.get("Municipio") or "",
            "fuente": FUENTE_BASE,
            "provisional": False,
        },
    }


def geometria_provisional(lineas):
    """Buffer por calle, disolución y simplificación -> Polygon/MultiPolygon."""
    buffers = []
    for linea in lineas or []:
        puntos = [
            (float(punto[0]), float(punto[1]))
            for punto in linea
            if len(punto) >= 2
        ]
        if len(puntos) < 2:
            continue
        buffers.append(LineString(puntos).buffer(BUFFER_GRADOS))
    if not buffers:
        return None

    forma = unary_union(buffers).simplify(SIMPLIFY_GRADOS)
    if forma.is_empty or forma.area <= 0:
        return None

    partes = list(forma.geoms) if forma.geom_type == "MultiPolygon" else [forma]
    partes = [
        parte for parte in partes
        if parte.geom_type == "Polygon" and not parte.is_empty and parte.area > 0
    ]
    if not partes:
        return None

    anillos = [
        redondear(
            [list(parte.exterior.coords)]
            + [list(interior.coords) for interior in parte.interiors]
        )
        for parte in partes
    ]
    if len(anillos) == 1:
        return {"type": "Polygon", "coordinates": anillos[0]}
    return {"type": "MultiPolygon", "coordinates": anillos}


def feature_provisional(circuito):
    """Forma provisional para un circuito del catálogo, o None."""
    codigo = str(circuito.get("codigo") or "").strip().upper()
    if not codigo:
        return None

    geometria = geometria_provisional(circuito.get("lineas"))
    if geometria is None:
        return None

    # Revalidar tras el redondeo: descarta formas colapsadas.
    forma = shape(geometria)
    if not forma.is_valid or forma.area <= 0:
        return None

    municipios = circuito.get("municipios") or []
    municipio = (municipios[0] if municipios else circuito.get("municipio")) or ""

    return {
        "type": "Feature",
        "geometry": geometria,
        "properties": {
            "codigos": [codigo],
            "codigo": codigo,
            "municipio": municipio,
            "fuente": FUENTE_PROVISIONAL,
            "provisional": True,
        },
    }


def primera_coordenada(geometria):
    """Primera posición [lon, lat] para desempatar el orden de features."""
    coordenadas = geometria["coordinates"]
    if geometria["type"] == "MultiPolygon":
        return coordenadas[0][0][0]
    return coordenadas[0][0]


def main():
    origen = descargar(URL_FUENTE)
    features = []
    cubiertos = set()

    for feature in origen.get("features") or []:
        normalizada = normalizar_feature(feature)
        if normalizada is None:
            continue
        features.append(normalizada)
        cubiertos.update(normalizada["properties"]["codigos"])

    catalogo = json.loads(CATALOGO.read_text(encoding="utf-8")).get("circuitos") or []
    for circuito in catalogo:
        codigo = str(circuito.get("codigo") or "").strip().upper()
        if not codigo or codigo in cubiertos:
            continue
        provisional = feature_provisional(circuito)
        if provisional is None:
            continue
        features.append(provisional)
        cubiertos.add(codigo)

    features.sort(
        key=lambda f: (
            f["properties"]["codigo"],
            primera_coordenada(f["geometry"])[0],
            primera_coordenada(f["geometry"])[1],
        )
    )

    coleccion = {
        "type": "FeatureCollection",
        "metadata": {
            "generado": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "base": FUENTE_BASE,
            "url": URL_FUENTE,
            "nota": NOTA,
        },
        "features": features,
    }

    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    contenido = json.dumps(coleccion, ensure_ascii=False, separators=(",", ":")) + "\n"
    DESTINO.write_text(contenido, encoding="utf-8")

    codigos_unicos = {c for f in features for c in f["properties"]["codigos"]}
    provisionales = sum(1 for f in features if f["properties"]["provisional"])
    tamano = DESTINO.stat().st_size

    print(f"features: {len(features)}")
    print(f"codigos unicos: {len(codigos_unicos)}")
    print(f"provisionales: {provisionales}")
    print(f"bytes: {tamano}")


if __name__ == "__main__":
    main()
