"""Motor compartido de "evidencia de calles", SIN red.

Triangula dónde deberían estar las calles de un circuito usando solo lo que ya
está en caché en el repo:

  (a) hits cacheados 'circ|...' de OTROS circuitos que comparten >=2 nombres de
      calle distintivos con las suyas (los hermanos de un mismo reparto viven
      cerca);
  (b) el centroide de su geometría de líneas ya resuelta
      (data/geocache_circuitos_lineas.json);
  (c) los puntos de data/barrios_osm.json cuyo nombre aparece en sus calles.

El token que geocodificó el punto NUNCA vota (la clave propia se excluye): un
POI homónimo no puede validarse a sí mismo. La lectura es mayoritaria y robusta
a outliers, como _geocode_mediana_calles en estado.py: se agrupa por seeds y
gana el racimo con más puntos concordantes dentro de un radio pequeño.

Lo consumen verificar_datos.py (chequeo diario del punto pintado lejos de sus
propias calles, purga retroactiva de la caché) y estado.py (guardia del atajo
LUGARES_MANUAL de primer segmento: 'Comodoro' es una CALLE de Ciudad Popular,
no siempre el reparto de Vedado — si las calles hermanas convergen a >5 km del
punto manual, el atajo no puede saltarse la geocodificación validada).
"""

import json
import math
import os
import re
import statistics

from geocode_zonas import normalizar

RAIZ = os.path.join(os.path.dirname(__file__), "..")
CACHE_GEO = os.path.join(RAIZ, "data", "geocache_averias.json")
CACHE_LINEAS = os.path.join(RAIZ, "data", "geocache_circuitos_lineas.json")
BARRIOS_OSM = os.path.join(RAIZ, "data", "barrios_osm.json")

# Umbrales: el auditor diario exige racimos mayoritarios y claramente dominantes
# para purgar sin red de por medio (el manual confirmó los casos, el gate
# automático tiene que ser conservador). La guardia del atajo manual usa un
# mínimo menor porque su radio de daño es acotado (solo LUGARES_MANUAL) y su
# alternativa es peor: instalar sin validar un punto que contradice la ciudad.
DIST_MINIMA_M = 5000        # punto a >5 km del racimo de evidencia -> problema
RADIO_CONCUERDIA_M = 2500   # radio de concordancia intra-racimo
MIN_CONCORDANTES = 5        # auditor: puntos concordantes del racimo mayoritario
MIN_CONCORDANTES_GUARDIA = 3  # estado.py: mínimo para anular el atajo manual

# Recorte del espacio de claves propio de circuitos (idéntico al de estado.py).
def clave_cache(calles):
    return "circ|" + re.sub(r"\s+", " ", calles or "").strip(" .;,")


# Mismo troceo que estado.py::_geocode_mediana_calles, más el filtro de tokens
# DISTINTIVOS: 'calle 25', 'avenida 41', números sueltos y años no identifican
# un lugar (son el andén homónimo que engaña a Nominatim).
_RE_CORTE = re.compile(r"\s+(?:desde|hasta|entre|y|e|a)\s+|[;,]", re.I)
_RE_GENERICO = re.compile(
    r"^(?:calle|c\.?|avenida|av\.?|avda\.?|carretera|cta\.?)\s*\d", re.I)


def tokens(calles):
    partes = _RE_CORTE.split(calles or "")
    out, vistos = [], set()
    for p in partes:
        p = re.sub(r"\(.*?\)", "", p).strip()
        if len(p) < 1 or p.lower() in vistos or re.match(r"^\d{4,}$", p):
            continue
        vistos.add(p.lower())
        out.append(p)
    return out


def distintivo(t):
    letras = re.sub(r"[^a-záéíóúüñ]", "", t)
    return len(letras) >= 4 and not re.match(r"^\d+$", t) and not _RE_GENERICO.match(t)


def tokens_distintivos(calles):
    return {normalizar(t) for t in tokens(calles) if distintivo(normalizar(t))}


# Regla Alamar: "Zonas: 1, 2, 3..." se resuelve por coords OSM, no por calles.
ALAMAR = re.compile(r"\bzonas?\b[\s:]*\d", re.I)


def dist_m(a, b):
    """Distancia aproximada (m) en proyección plana, como en todo el repo."""
    return math.hypot((a[0] - b[0]) * 111000, (a[1] - b[1]) * 102000)


def entradas_hermanas(cache_geo):
    """[(tokens_distintivos, (lat, lon), clave)] de todos los hits 'circ|'."""
    out = []
    for k, v in cache_geo.items():
        if not k.startswith("circ|") or not v:
            continue
        out.append((tokens_distintivos(k[5:]), (v["lat"], v["lon"]), k))
    return out


_BARRIOS = None


def barrio_puntos():
    """{nombre_normalizado: (lat, lon)} de data/barrios_osm.json (best effort)."""
    global _BARRIOS
    if _BARRIOS is None:
        try:
            with open(BARRIOS_OSM) as f:
                _BARRIOS = {normalizar(b["nombre"]): (b["lat"], b["lon"])
                            for b in json.load(f)}
        except Exception:
            _BARRIOS = {}
    return _BARRIOS


# Palabras demasiado genéricas para identificar un reparto por sí solas.
GEN_BARRIO = {"santa", "santo", "santas", "alturas", "altura", "reparto",
              "centro", "barrio", "zona", "norte", "sur", "este", "oeste"}


def puntos_barrio(calles):
    """Evidencia local sin red: tokens de las calles que son nombre de barrio."""
    bp = barrio_puntos()
    out = []
    for t in tokens(calles):
        tn = normalizar(t)
        if tn in GEN_BARRIO or _RE_GENERICO.match(t):
            continue
        # un solo término corto ('plaza', 'san') es ambiguo: exigimos dos
        # palabras o nombre largo, como en la auditoría original.
        if tn in bp and (len(tn.split()) >= 2 or len(tn) >= 7):
            out.append(bp[tn])
    return out


def centro_lineas(cache_lineas, codigo, minpts=2):
    """Centroide de la geometría en caché, o None. Es evidencia DESECHABLE por
    su cuenta (puede estar envenenada ella también): solo vota como un punto."""
    pts = [p for l in (cache_lineas.get(codigo) or []) for p in l]
    if len(pts) < minpts:
        return None
    return (statistics.median([p[1] for p in pts]),
            statistics.median([p[0] for p in pts]))


def mejor_cluster(pts, radio=RADIO_CONCUERDIA_M):
    """(centro, n) del racimo con más puntos concordantes; (None, 0) si vacío.
    Seeds sobre los propios puntos: robusto a outliers sin caída a la media de
    TODO (un pool bimodal tiene que quedar debajo del gate, no fundirse)."""
    mejor, mejor_n = None, 0
    for seed in pts:
        cerca = [p for p in pts if dist_m(p, seed) < radio]
        if len(cerca) > mejor_n:
            mejor, mejor_n = cerca, len(cerca)
    if not mejor:
        return None, 0
    return ((statistics.median([p[0] for p in mejor]),
             statistics.median([p[1] for p in mejor])), mejor_n)


def evidencia_de_calles(codigo, calles, punto, entradas, cache_lineas):
    """Resumen de evidencia triangulada (sin red) para un circuito ubicado.

    Devuelve dict con:
      centro, n        racimo mayoritario de TODO el pool (hermanos + líneas
                       en caché + barrios), o (None, 0);
      n_coincide       tamaño del racimo mayoritario entre los puntos que SÍ
                       caen cerca del punto pintado (su escaño de defensa);
      n_hermanos, tiene_lineas, puntos_barrio  contadores para el informe.
    El punto pintado nunca vota: la clave 'circ|' propia se excluye del pool."""
    ck = clave_cache(calles)
    ctoks = tokens_distintivos(calles)
    pool, n_hermanos = [], 0
    for toks, pt, k in entradas:
        if k == ck or len(ctoks & toks) < 2:
            continue
        pool.append(pt)
        n_hermanos += 1
    lc = centro_lineas(cache_lineas, codigo)
    if lc:
        pool.append(lc)
    bpts = puntos_barrio(calles)
    pool += bpts
    centro, n = mejor_cluster(pool)
    cerca = [p for p in pool if punto and dist_m(p, punto) < RADIO_CONCUERDIA_M]
    _, n_coincide = mejor_cluster(cerca)
    return {"centro": centro, "n": n, "n_coincide": n_coincide,
            "n_hermanos": n_hermanos, "tiene_lineas": lc is not None,
            "puntos_barrio": bpts}


def contradice_evidencia(punto, calles, entradas, cache_lineas,
                         minimo=MIN_CONCORDANTES_GUARDIA):
    """True si un racimo concordante de >=`minimo` puntos de evidencia cae a
    mas de 5 km de `punto` (y ningún racimo tan grande o mayor lo respalda).
    Es la pregunta que hace estado.py antes de tragar el atajo LUGARES_MANUAL."""
    ev = evidencia_de_calles(None, calles, punto, entradas, cache_lineas)
    if not ev["centro"] or ev["n"] < minimo:
        return False
    return dist_m(punto, ev["centro"]) > DIST_MINIMA_M and ev["n_coincide"] < ev["n"]


def cargar_caches():
    """(cache_geo, cache_lineas) del repo, best effort: archivo roto o ausente
    = pool vacío y el chequeo queda silencioso por debajo del gate."""
    try:
        with open(CACHE_GEO) as f:
            g = json.load(f)
    except Exception:
        g = {}
    try:
        with open(CACHE_LINEAS) as f:
            l = json.load(f)
    except Exception:
        l = {}
    return g, l
