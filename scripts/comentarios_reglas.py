"""Procesamiento DETERMINISTA de comentarios (sin LLM, sin cuota). Es el FALLBACK:
lo usa comentarios_llm.py cuando el LLM falla (429/error) para no perder la señal.
Extrae lo que se puede con reglas — sin/con corriente, bloque, horas, y el lugar por
coincidencia con el catálogo de barrios OSM.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "extractor"))
from extract import SIN_SERVICIO, CON_SERVICIO, bloques_en  # noqa: E402
from geocode_zonas import normalizar, resolver_zonas_numeradas  # noqa: E402
import correcciones  # noqa: E402

RE_HORAS = re.compile(
    r"llev(?:amos|o|an?|a)\s+(?:ya\s+|m[aá]s de\s+|casi\s+)?(\d{1,2})\s*(horas?|h\b|d[ií]as?)",
    re.IGNORECASE,
)


def catalogo_barrios():
    """{nombre_normalizado: barrio} de OSM, para ubicar por coincidencia de nombre."""
    import json
    ruta = os.path.join(os.path.dirname(__file__), "..", "data", "barrios_osm.json")
    d = {}
    for b in json.load(open(ruta)):
        d.setdefault(normalizar(b["nombre"]), b)
    return d


def hallar_lugar(texto, catalogo):
    """Lugar del comentario, por orden de confianza:
    1) corrección MANUAL (verdad local: Guiteras, Comodoro...);
    2) barrio/reparto del catálogo OSM (el nombre más largo presente);
    3) regla Alamar ('zona 12', 'zonas 13 y 15')."""
    t = normalizar(texto)
    for nombre, c in correcciones.lugares_manual().items():
        if len(nombre) >= 4 and re.search(rf"\b{re.escape(nombre)}\b", t):
            return {"nombre": nombre, "lat": c["lat"], "lon": c["lon"]}
    mejor = None
    for n, b in catalogo.items():
        if len(n) >= 4 and re.search(rf"\b{re.escape(n)}\b", t):
            if mejor is None or len(n) > len(normalizar(mejor["nombre"])):
                mejor = b
    if mejor:
        return mejor
    m = re.search(r"zonas?\s*[:\s]\s*\d", texto, re.IGNORECASE)
    if m:
        alamar = resolver_zonas_numeradas(texto[m.start():m.start() + 60])
        if alamar:
            return {"nombre": "Alamar (zonas)", "lat": alamar["lat"], "lon": alamar["lon"]}
    return None


def fila_determinista(m, catalogo):
    """Fila para comentarios_llm a partir de un comentario, solo con reglas.
    Devuelve None si el comentario no reporta sin/con corriente de forma clara."""
    texto = m["texto"] or ""
    sin, con = bool(SIN_SERVICIO.search(texto)), bool(CON_SERVICIO.search(texto))
    if sin == con:  # ni claro sin, ni claro con (o ambiguo)
        return None
    lugar = hallar_lugar(texto, catalogo)
    bl = bloques_en(texto)
    mh = RE_HORAS.search(texto)
    horas = int(mh.group(1)) * (24 if mh.group(2).lower().startswith("d") else 1) if mh else None
    return {
        "message_id": m["message_id"], "fecha": m["fecha"],
        "reporta": "sin_corriente" if sin else "con_corriente",
        "lugar": lugar["nombre"] if lugar else None,
        "bloque": bl[0] if len(bl) == 1 else None,
        "horas": horas if horas and 0 < horas <= 96 else None,
        "lat": lugar["lat"] if lugar else None,
        "lon": lugar["lon"] if lugar else None,
    }
