"""Cargador de la 'verdad local' (data/correcciones.json): correcciones manuales
sobre el PDF base. Un solo lugar para todo lo que descubrimos y ajustamos a mano.

El PDF (data/bloques.json) es la semilla; estas correcciones y lo aprendido del
canal (bloques_aprendidos.json) tienen prioridad. Cada función devuelve una sección.
"""

import json
import os

_RUTA = os.path.join(os.path.dirname(__file__), "..", "data", "correcciones.json")


def _cargar():
    with open(_RUTA) as f:
        return json.load(f)


def clasificacion_barrios():
    """nombre OSM -> categoría (bloque | daf | candidata_protegida)."""
    return _cargar().get("clasificacion_barrios", {})


def protegidas_confirmadas():
    """Puntos sin apagón confirmados por vecinos: [{nombre, lat, lon}]."""
    return _cargar().get("protegidas_confirmadas", [])


def excluir_poligono_osm():
    """(municipio, nombre) cuyo polígono OSM sobrepasa la zona real."""
    return _cargar().get("excluir_poligono_osm", [])


def rellenar_como_area():
    """Zonas que se rellenan como área aunque no digan 'cuadrante'."""
    return _cargar().get("rellenar_como_area", [])


def bbox_localidad():
    """Cajas [sur, oeste, norte, este] para acotar la búsqueda de calles."""
    return {k: tuple(v) for k, v in _cargar().get("bbox_localidad", {}).items()}


def poligonos_manuales():
    """Polígonos dibujados/derivados a mano: [{nombre, municipio, bloque, anillo}]."""
    return _cargar().get("poligonos_manuales", [])


def lugares_manual():
    """Lugares con coords corregidas a mano (Nominatim los ubica mal):
    nombre_normalizado -> {lat, lon, nota}. P. ej. 'guiteras', 'comodoro'."""
    return _cargar().get("lugares_manual", {})


def circuitos_municipio():
    """Municipio corregido a mano por código de circuito (cuando no se puede ubicar
    o la ubicación cae en el municipio equivocado): CÓDIGO -> municipio."""
    return _cargar().get("circuitos_municipio", {})


def circuitos_falsos():
    """Códigos que NO son circuitos aunque parezcan (verdad local): p. ej. 'L2'
    es la CALLE L del Vedado leída como código. Se excluyen del catálogo."""
    return _cargar().get("circuitos_falsos", [])
