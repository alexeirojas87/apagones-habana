"""Motor compartido de "evidencia de calles", SIN red.

Triangula dónde deberían estar las calles de un circuito usando solo lo que ya
está en caché en el repo:

  (a) hits cacheados 'circ|...' de OTROS circuitos que comparten >=2 nombres de
      calle distintivos con las suyas (los hermanos de un mismo reparto viven
      cerca). N variantes del mismo parte geocodificadas por la MISMA consulta
      POI comparten el match y no son N evidencias: cuentan como UNA (la
      familia SG316: 12 claves sobre 'Centro Hispano' se auto-certificaban);
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
from build_lineas import norm as _norm

RAIZ = os.path.join(os.path.dirname(__file__), "..")
CACHE_GEO = os.path.join(RAIZ, "data", "geocache_averias.json")
CACHE_LINEAS = os.path.join(RAIZ, "data", "geocache_circuitos_lineas.json")
BARRIOS_OSM = os.path.join(RAIZ, "data", "barrios_osm.json")
BARRIOS_POLI = os.path.join(RAIZ, "web", "data", "barrios_poligonos.json")

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


# Preposiciones y genéricos que preceden al topónimo ("en Luyanó",
# "reparto Fontanar") y que hay que quitar para que el nombre case.
_STOP = r"^(?:en|el|la|los|las|de|del|reparto|rpto\.?|zona|zonas|barrio)\s+"


def _nombres_calles(calles):
    """Trocea la descripción del parte en topónimos individuales.

    Parte también por ',' y ' y ': antes solo se partía por ';', así que
    "D´Beche, Nalón" viajaba como un único topónimo inexistente.
    (Movido desde build_circuitos.main(), donde no era importable: lo
    consumen también el gazetteer de _lugar_gaceta.)"""
    nombres = []
    for seg in re.split(r"[;,]|\s+y\s+", calles):
        seg = re.sub(r"\(.*?\)", "", seg)
        primero = re.split(r"\s+(?:desde|entre|hasta)\s+", seg.strip(), flags=re.I)[0]
        primero = re.sub(_STOP, "", primero.strip(), flags=re.I)
        n = _norm(primero)
        if n and 1 <= len(n) <= 30 and n not in nombres:
            nombres.append(n)
    return nombres[:10]


def punto_interior(anillo):
    """Punto garantizado DENTRO de un anillo [[lon, lat], ...]: a la latitud media
    se cortan los cruces del polígono y se toma el centro del tramo más ancho.
    (El promedio de vértices cae fuera en polígonos cóncavos.)"""
    lat = sum(p[1] for p in anillo) / len(anillo)
    cortes = []
    n = len(anillo)
    for i in range(n):
        x1, y1 = anillo[i]
        x2, y2 = anillo[(i + 1) % n]
        if (y1 > lat) != (y2 > lat):
            cortes.append(x1 + (x2 - x1) * (lat - y1) / (y2 - y1))
    cortes.sort()
    if len(cortes) < 2:
        return None
    tramos = [(cortes[i + 1] - cortes[i], (cortes[i] + cortes[i + 1]) / 2)
              for i in range(0, len(cortes) - 1, 2)]
    return {"lat": lat, "lon": max(tramos)[1]}


# Regla Alamar: "Zonas: 1, 2, 3..." se resuelve por coords OSM, no por calles.
ALAMAR = re.compile(r"\bzonas?\b[\s:]*\d", re.I)


def dist_m(a, b):
    """Distancia aproximada (m) en proyección plana, como en todo el repo."""
    return math.hypot((a[0] - b[0]) * 111000, (a[1] - b[1]) * 102000)


def entradas_hermanas(cache_geo):
    """[(tokens_distintivos, (lat, lon), clave, match)] de todos los hits 'circ|'."""
    out = []
    for k, v in cache_geo.items():
        if not k.startswith("circ|") or not v:
            continue
        out.append((tokens_distintivos(k[5:]), (v["lat"], v["lon"]), k, v.get("match")))
    return out


def match_generica(m):
    """Match que no apunta a un POI concreto: derivado de evidencia propia de
    la dirección (mediana, barrio local, centroide) o legacy sin nombre. Esas
    entradas las geocodificó cada clave por separado, no es la huella de una
    familia: cada una vota como punto independiente."""
    return not m or m.startswith(("mediana", "barrio local", "centroide",
                                  "centro municipio", "gaceta de barrios"))


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


# ---------------------------------------------------------------------------
# Gaceta de barrios: ubicación de CIRCUITOS sin red. El explore simuló que un
# gazetteer-first ingenuo pinta PEOR que los centroides ('reparto'→Reparto
# Inav, 'santa'→Santa María del Mar, 'monte'→Monterrey). Por eso cada hit
# pasa dos gates: el de genéricos (un token suelto no es un barrio) y el de
# autoridad (el punto debe caer dentro de un municipio del circuito). Sin
# polys no hay gate posible -> None: 'sin ubicar' honesto, no centroide.
# ---------------------------------------------------------------------------

# GEN_BARRIO cubre el español; 'monte/martes' no: 'monte' presta su prefijo a
# Monterrey y 'martes' no existe — la lista la fijan los textos reales.
_GACETA_GENERICO = GEN_BARRIO | {"monte", "montes", "repartos", "santigalo"}

# 'atare'~'atares' con 4 letras es moneda al aire; de 5 para arriba el prefijo
# ya identifica (el radio de concordancia pone el resto).
_ANCLA_MIN = 5


def nombres_de_gaceta(dire):
    """Los mismos nombres de _nombres_calles pero sobre la gaceta completa:
    los dos puntos también cierran un nombre ('Chivás, Vía...' vs 'Chivás:
    calles...')."""
    return _nombres_calles(dire.replace(":", ";"))


def _variantes(nombre):
    """Claves de comparación de un nombre de gaceta: normalizado, sin
    stopwords iniciales ('El Globo'~'globo') y sin paréntesis de desambiguación."""
    sin_par = re.sub(r"\(.*?\)", " ", nombre).strip()
    vs = {_norm(sin_par), _norm(re.sub(_STOP, "", sin_par, flags=re.I))}
    return {v for v in vs if v}


def _ancla(p):
    """Palabra que puede prestar su prefijo: ni genérica ni numérica ni corta."""
    return len(p) >= _ANCLA_MIN and p not in _GACETA_GENERICO and not p.isdigit()


_GACETA = None


def gaceta_entries():
    """{canonico: {variantes, puntos, anillo}} uniendo data/barrios_osm.json
    (puntos) y web/data/barrios_poligonos.json (anillos). Lazy: los tests y el
    auditor cargan sin leer los JSON si nadie pregunta por la gaceta."""
    global _GACETA
    if _GACETA is None:
        entradas = {}

        def entrada(canon):
            return entradas.setdefault(
                canon, {"variantes": set(), "puntos": [], "anillo": None})

        def canonico(nombre):
            return _norm(re.sub(r"\(.*?\)", " ", nombre).strip())

        try:
            with open(BARRIOS_OSM) as f:
                filas = json.load(f)
        except Exception:
            filas = []
        for b in filas:
            canon = canonico(b["nombre"])
            if not canon:
                continue
            e = entrada(canon)
            e["puntos"].append((b["lat"], b["lon"]))
            e["variantes"] |= _variantes(b["nombre"])
        try:
            with open(BARRIOS_POLI) as f:
                pols = json.load(f)
        except Exception:
            pols = {}
        for k, val in pols.items():
            nombre = ((val or {}).get("nombre") or k).strip()
            canon = canonico(nombre)
            if not canon:
                continue
            e = entrada(canon)
            if val.get("anillo") and e["anillo"] is None:
                e["anillo"] = val["anillo"]
            e["variantes"] |= _variantes(nombre)
            e["variantes"].add(_norm(k))
        _GACETA = entradas
    return _GACETA


def _concuerda_debil(t, e):
    """Aceptación tras perder la preferencia exacta: prefijo de frase, o
    contención de todas las palabras del nombre, o prefijo del nombre único
    sobre una palabra ancla del token ('ensenada de atare'~'atares')."""
    for v in e["variantes"]:
        if len(v) >= _ANCLA_MIN and (t.startswith(v) or v.startswith(t)):
            return True
    tw = set(t.split())
    for v in e["variantes"]:
        vw = v.split()
        if all(w in tw for w in vw) and any(_ancla(w) for w in vw):
            return True
    for v in e["variantes"]:
        if " " not in v and _ancla(v) and any(
                _ancla(w) and (v.startswith(w) or w.startswith(v)) and v != w
                for w in tw):
            return True
    return False


def _lugar_gazetteer(dire, dentro):
    """(lat, lon, 'gaceta de barrios', candidato) del primer nombre de la
    gaceta que cae dentro de la autoridad del circuito, o None. El punto OSM
    manda; el centro del anillo solo lo sustituye si el punto cae fuera
    (CCP20/Atarés). Un MISMO nombre partido en dos entradas lejanas (más de
    RADIO_CONCUERDIA_M, canónicos distintos) es ambigüedad: None, no una
    moneda al aire. Nombres DISTINTOS mencionados a la vez no lo son: la
    primera mención es el referente del parte (GC7 'Cojímar y Comunidad
    Guamá' -> Cojímar), y lo que cruza de autoridad ya lo rechazó el gate."""
    if dentro is None:
        return None
    entradas = gaceta_entries()
    for t in nombres_de_gaceta(dire):
        if not t or t in _GACETA_GENERICO or t.isdigit():
            continue
        posibles = [(c, e) for c, e in entradas.items() if t in e["variantes"]] \
            or [(c, e) for c, e in entradas.items() if _concuerda_debil(t, e)]
        superv = []
        for canon, e in posibles:
            punto = next((p for p in e["puntos"] if dentro(p[0], p[1])), None)
            if punto is None and e["anillo"] is not None:
                pi = punto_interior(e["anillo"])
                if pi and dentro(pi["lat"], pi["lon"]):
                    punto = (pi["lat"], pi["lon"])
            if punto is not None:
                superv.append((canon, punto))
        for i, (c1, p1) in enumerate(superv):
            for c2, p2 in superv[i + 1:]:
                if c1 != c2 and dist_m(p1, p2) > RADIO_CONCUERDIA_M:
                    return None
        if superv:
            canon, (lat, lon) = superv[0]
            return {"lat": lat, "lon": lon, "match": "gaceta de barrios",
                    "candidato": canon}
    return None


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
    El punto pintado nunca vota: la clave 'circ|' propia se excluye del pool.
    Auto-certificación (SG316): N variantes del mismo parte geocodificadas por
    el MISMO POI homónimo comparten el match; no son N evidencias concordantes
    sino UNA, así que los hermanos con match no-genérico se deduplican por ese
    valor. Los genéricos (mediana/barrio local/centroide) sí son triangulación
    independiente y cada uno vota. SG314 conserva su racimo porque sus
    hermanos traen matches DISTINTOS ('Santiago' y 'Antón Rocío'), no una
    familia clonada."""
    ck = clave_cache(calles)
    ctoks = tokens_distintivos(calles)
    pool, n_hermanos, vistos = [], 0, set()
    for toks, pt, k, match in entradas:
        if k == ck or len(ctoks & toks) < 2:
            continue
        if not match_generica(match):
            if match in vistos:
                continue  # familia: otra copia del mismo POI no añade voto
            vistos.add(match)
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
