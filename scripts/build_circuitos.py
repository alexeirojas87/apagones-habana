"""Construye web/data/circuitos.json: catálogo APRENDIDO de circuitos de la red a
partir de los códigos oficiales que la Empresa pone en sus partes (ej. "A1443-
Rio Verde…", "PG940- Comodoro…"). El código es el prefijo de la subestación +
número del circuito; es un identificador estable de un tramo físico de red que
alimenta un conjunto fijo de calles.

No acumula un fichero propio: reconstruye desde TODO el histórico del canal en
Supabase (que ya es la fuente de verdad). Para cada código guarda las calles que
sirve, el municipio, el bloque en que rota (inferido del post), cuántas veces se
ha visto, cuándo, y su último estado conocido (con/sin servicio).
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

from supabase import create_client

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "extractor"))
from extract import bloques_en, municipios_en, causa_en, normalizar  # noqa: E402

RAIZ = os.path.join(os.path.dirname(__file__), "..")
CACHE_LINEAS = os.path.join(RAIZ, "data", "geocache_circuitos_lineas.json")
RECORD_FILE = os.path.join(RAIZ, "data", "record_circuitos.json")
OFICIAL_FILE = os.path.join(RAIZ, "data", "circuitos_oficial.json")  # tablas oficiales UNE

# Línea de circuito en un parte: viñeta opcional + CÓDIGO(s) + descripción de calles.
# CÓDIGO = 1-3 letras (subestación) + 2-4 dígitos. Admite varios separados por coma
# ("T41, T43 - Toyo…") que comparten descripción. Se exige prefijo de letra para no
# confundir con números de dirección sueltos.
_COD = r"(?:[A-Za-z]{1,3}\d{1,4}|\d{2,4})"  # letra+dígitos (C7, A1443) o número puro (1243)
# Línea de circuito: viñeta 👉 + CÓDIGO(s) + separador (:/-/espacio) + descripción de
# calles. Los códigos pueden ir separados por coma O por barra ("OP310/OP417/...:
# Soterrado Habana Vieja") y comparten la misma descripción. Exigimos la viñeta 👉
# para admitir códigos de número puro sin falsos positivos.
RE_CIRC = re.compile(
    r"(?m)^\s*👉\s*"
    rf"({_COD}(?:\s*[,/]\s*{_COD})*)"
    r"\s*([-–:])?\s*(.+?)\s*$"
)
RE_UN_CODIGO = re.compile(rf"^{_COD}$")


def _en_poly(lat, lon, ring):
    """Ray casting: ¿(lat,lon) dentro del anillo [[lon,lat],…]?"""
    dentro, n, j = False, len(ring), len(ring) - 1
    for i in range(n):
        xi, yi, xj, yj = ring[i][0], ring[i][1], ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            dentro = not dentro
        j = i
    return dentro


def municipios_geo():
    """[(nombre, anillo)] de los 15 municipios de La Habana, para ubicar por punto."""
    with open(os.path.join(RAIZ, "web", "data", "municipios.geojson")) as f:
        gj = json.load(f)
    return [(ft["properties"]["municipio"], ft["geometry"]["coordinates"][0]) for ft in gj["features"]]


def estado_de(plano):
    """con/sin servicio según el tipo de parte donde aparece el circuito."""
    if re.search(r"restableci|teniendo con servicio|quedan? con servicio|en servicio", plano):
        return "con servicio"
    if re.search(r"se afecta|afectaci|afectados|disparo autom|\bdaf\b|emergencia", plano):
        return "sin servicio"
    return None


def limpiar_calles(texto):
    return re.sub(r"[📉🚧✅📣📌🔔‼️⚡️👉💥📈🔹]+", "", texto).strip(" .-–")


def main():
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    filas, off = [], 0
    while True:
        lote = (sb.table("mensajes").select("fecha,texto").eq("chat", "canal")
                .order("fecha").range(off, off + 999).execute().data)
        filas += lote
        if len(lote) < 1000:
            break
        off += 1000

    # catálogo oficial (se usa para validar códigos numéricos ambiguos y, más
    # abajo, para la fusión de municipios/calles oficiales)
    try:
        oficial = json.load(open(OFICIAL_FILE))
    except Exception:
        oficial = {}
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import correcciones  # noqa: E402
    falsos = set(correcciones.circuitos_falsos())  # 'L2' = calle L, no circuito

    cat = {}  # codigo -> registro acumulado (en orden cronológico gana el último)
    for f in filas:
        texto = f.get("texto") or ""
        plano = normalizar(texto)
        blqs = bloques_en(texto)
        bloque = blqs[0] if len(blqs) == 1 else None  # solo si el post es de UN bloque
        munis = municipios_en(texto)
        muni = munis[0] if len(munis) == 1 else None
        causa = causa_en(texto)
        est = estado_de(plano)
        fecha = f["fecha"]
        for m in RE_CIRC.finditer(texto):
            separador = bool(m.group(2))
            calles = limpiar_calles(m.group(3))
            if len(calles) < 3:
                continue
            for cod in re.split(r"\s*[,/]\s*", m.group(1)):
                cod = cod.strip().upper()
                if not RE_UN_CODIGO.match(cod):
                    continue
                # número puro: ambiguo con direcciones ("👉206 y 210, Plaza" es la
                # CALLE 206). Solo se acepta con separador explícito ("1243 - ...")
                # o si está en las tablas oficiales de la UNE.
                if cod.isdigit() and not separador and cod not in oficial:
                    continue
                if cod in falsos:  # verdad local: parece código pero no lo es
                    continue
                _pre = re.match(r"^[A-Z]+", cod)
                r = cat.setdefault(cod, {
                    "codigo": cod, "prefijo": _pre.group(0) if _pre else "",
                    "calles": "", "municipio": None, "bloque": None, "causa": None,
                    "veces": 0, "primera": fecha, "ultima": fecha,
                    "estado": None, "estado_fecha": None,
                })
                r["veces"] += 1
                r["ultima"] = fecha
                # nos quedamos con la lista de calles más completa vista
                if len(calles) > len(r["calles"]):
                    r["calles"] = calles
                if bloque is not None:
                    r["bloque"] = bloque          # último bloque conocido gana
                if muni:
                    r["municipio"] = muni
                if causa:
                    r["causa"] = causa
                if causa == "DAF":
                    r["daf"] = True  # circuito visto en aviso de Disparo Automático por Frecuencia
                if est:
                    r["estado"] = est
                    r["estado_fecha"] = fecha

        # Circuitos que SOLO aparecen en el parte de déficit ("✅CODE - N horas"),
        # sin calles: los registramos igual (quedan "sin información de la UNE"),
        # con estado sin servicio. Así el catálogo incluye TODOS los circuitos.
        if "actualizaci" in plano and "afectaciones" in plano:
            for cod, mun_p in re.findall(
                    r"([A-Za-z]{1,3}\d{1,4}|\d{3,4})\s*(?:\(([^)]{2,30})\))?\s*-?\s*\d+\s*horas?", texto):
                cod = cod.strip().upper()
                if not RE_UN_CODIGO.match(cod) or cod in falsos:
                    continue
                # municipio entre paréntesis (formato UNE jul/2026): dato directo
                mun_cod = (municipios_en(mun_p) or [None])[0] if mun_p else None
                _pre = re.match(r"^[A-Z]+", cod)
                r = cat.setdefault(cod, {
                    "codigo": cod, "prefijo": _pre.group(0) if _pre else "",
                    "calles": "", "municipio": None, "bloque": None, "causa": "déficit de generación",
                    "veces": 0, "primera": fecha, "ultima": fecha,
                    "estado": None, "estado_fecha": None,
                })
                r["veces"] += 1
                r["ultima"] = fecha
                r["estado"] = "sin servicio"
                r["estado_fecha"] = fecha
                if mun_cod and not r["municipio"]:
                    r["municipio"] = mun_cod

    # --- Fusión con el catálogo OFICIAL de la UNE (data/circuitos_oficial.json,
    # extraído de las tablas PDF, cargado arriba) ---. Es la fuente de verdad:
    # añade circuitos que no hemos visto en Telegram y aporta calles oficiales
    # (más limpias, geocodifican mejor). El municipio oficial se aplica más abajo.
    for cod, info in oficial.items():
        r = cat.get(cod)
        if not r:
            _pre = re.match(r"^[A-Z]+", cod)
            r = cat[cod] = {"codigo": cod, "prefijo": _pre.group(0) if _pre else "",
                            "calles": "", "municipio": None, "bloque": None, "causa": None,
                            "veces": 0, "primera": None, "ultima": None,
                            "estado": None, "estado_fecha": None}
        r["oficial"] = True
        r["municipios"] = info.get("municipios") or []  # lista oficial (puede ser >1: feeders de frontera)
        calles_of = " · ".join(v for v in (info.get("calles") or {}).values() if v)
        if calles_of:
            r["calles"] = calles_of  # las calles oficiales ganan (más completas)

    circuitos = sorted(cat.values(), key=lambda r: r["ultima"] or "", reverse=True)

    # Récord de circuitos apagados A LA VEZ: contamos cuántos están sin servicio
    # AHORA (no los ~10 que muestra un parte) y guardamos el pico histórico.
    sin_ahora = sum(1 for c in circuitos if c.get("estado") == "sin servicio")
    record = {}
    try:
        record = json.load(open(RECORD_FILE))
    except Exception:
        pass
    if sin_ahora > record.get("max_apagados", 0):
        record = {"max_apagados": sin_ahora, "fecha": datetime.now(timezone.utc).isoformat()}
    json.dump(record, open(RECORD_FILE, "w"), ensure_ascii=False)

    # Fase 2: geocodificar cada circuito por sus calles (barrio/lugar o mediana de
    # varias calles), con la misma caché y correcciones manuales que las averías.
    # Así el catálogo queda como base geolocalizada: código -> {calles, bloque, lat, lon}.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import estado as E  # noqa: E402
    import correcciones  # noqa: E402
    from geocode_zonas import bbox_municipios  # noqa: E402

    # Caja de búsqueda por circuito: si conocemos su municipio (oficial UNE o
    # manual), Nominatim se acota a esa caja (bounded=1). Sin esto, una "calle 379"
    # se encontraba en cualquier punto de la ciudad (el caso Mulgoba en la bahía)
    # y salían decenas de circuitos pintados en el municipio equivocado.
    _cajas = bbox_municipios()
    _manual_muni = correcciones.circuitos_municipio()
    _polys = dict(municipios_geo())

    def _munis_autoridad(c):
        info = oficial.get(c["codigo"])
        return (info.get("municipios") if info else None) or \
            ([_manual_muni[c["codigo"]]] if c["codigo"] in _manual_muni else [])

    def _caja_circuito(ms):
        bbs = [_cajas[m] for m in ms if m in _cajas]
        if not bbs:
            return None
        return (min(b[0] for b in bbs), min(b[1] for b in bbs),
                max(b[2] for b in bbs), max(b[3] for b in bbs))

    def _item(c):
        ms = _munis_autoridad(c)
        # polígonos reales del municipio: la caja (rectángulo) incluye pedazos de
        # municipios vecinos, así que el hit se VALIDA punto-en-polígono además
        return {"municipio": c.get("municipio"), "direccion": c["calles"],
                "caja": _caja_circuito(ms),
                "polys": [_polys[m] for m in ms if m in _polys], "_c": c}

    geo = [_item(c) for c in circuitos if c.get("calles")]
    # tope de geocodificaciones nuevas por corrida (Nominatim es lento); la caché
    # se acumula, así que en pocas corridas quedan todos ubicados.
    E.geocodificar_averias(geo, bbox_municipios(), solo_lugar=True, max_nuevos=25)
    for g in geo:
        if "lat" in g:
            g["_c"]["lat"], g["_c"]["lon"] = round(g["lat"], 5), round(g["lon"], 5)
    ubicados = sum(1 for c in circuitos if "lat" in c)

    # Municipio por UBICACIÓN real (point-in-polygon), no por el texto del parte
    # (que se equivoca: p. ej. GC18 'Zona Franca de Berroa' salía como La Lisa cuando
    # está en Habana del Este). El municipio geográfico gana; si el circuito no está
    # ubicado, se conserva el del texto.
    munis = municipios_geo()
    for c in circuitos:
        if "lat" not in c:
            c["municipio"] = None  # sin ubicación no confiamos en el municipio del texto
            continue
        hallado = None
        for nombre, ring in munis:
            if _en_poly(c["lat"], c["lon"], ring):
                hallado = nombre
                break
        c["municipio"] = hallado  # geográfico; None si cae fuera de los polígonos

    # Autoridad de municipio: 1) el OFICIAL (UNE) manda —si el punto cae en uno de
    # sus municipios usamos ese (resuelve los que cruzan varios); si no, el primero—;
    # 2) para los NO oficiales, corrección manual (verdad local).
    import correcciones  # noqa: E402
    manual_muni = correcciones.circuitos_municipio()
    for c in circuitos:
        info_of = oficial.get(c["codigo"])
        if info_of and info_of.get("municipios"):
            g = c.get("municipio")
            c["municipio"] = g if g in info_of["municipios"] else info_of["municipios"][0]
        elif c["codigo"] in manual_muni:
            c["municipio"] = manual_muni[c["codigo"]]

    # Fase 2b: geometría de las CALLES reales (OSM/Overpass) de cada circuito, para
    # dibujarlas en el mapa. Cacheada por código (la geometría no cambia) y acotada
    # por corrida (Overpass es lento). Los que no resuelven quedan sin líneas (el
    # frontend cae a una bolita grande que engloba la zona).
    from build_lineas import overpass as _overpass, norm as _norm  # noqa: E402
    cache_l = json.load(open(CACHE_LINEAS)) if os.path.exists(CACHE_LINEAS) else {}

    def _nombres_calles(calles):
        nombres = []
        for seg in re.split(r";", calles):
            seg = re.sub(r"\(.*?\)", "", seg)
            primero = re.split(r"\s+(?:desde|de|entre|hasta|a)\s+", seg.strip(), flags=re.I)[0]
            n = _norm(primero)
            if n and 1 <= len(n) <= 24 and n not in nombres:
                nombres.append(n)
        return nombres[:8]

    nuevas = 0
    for c in circuitos:
        cod = c["codigo"]
        if cod in cache_l:
            if cache_l[cod]:
                c["lineas"] = cache_l[cod]
            continue
        if "lat" not in c or not c.get("calles") or nuevas >= 8:
            continue
        nombres = _nombres_calles(c["calles"])
        if not nombres:
            continue
        caja = (c["lat"] - 0.018, c["lon"] - 0.022, c["lat"] + 0.018, c["lon"] + 0.022)  # s,w,n,e
        try:
            vias = _overpass(nombres, caja)
        except Exception:
            vias = []
        nset = set(nombres)
        lineas = [[[round(p["lon"], 5), round(p["lat"], 5)] for p in v["geometry"]]
                  for v in vias if v.get("geometry") and _norm(v.get("tags", {}).get("name", "")) in nset]
        cache_l[cod] = lineas
        if lineas:
            c["lineas"] = lineas
        nuevas += 1
    json.dump(cache_l, open(CACHE_LINEAS, "w"), ensure_ascii=False)
    con_lineas = sum(1 for c in circuitos if c.get("lineas"))

    salida = {
        "generado": datetime.now(timezone.utc).isoformat(),
        "total": len(circuitos),
        "record_apagados": record,   # pico histórico de circuitos sin servicio a la vez
        "circuitos": circuitos,
    }
    destino = os.path.join(RAIZ, "web", "data", "circuitos.json")
    json.dump(salida, open(destino, "w"), ensure_ascii=False)
    print(f"circuitos.json: {len(circuitos)} circuitos, {ubicados} geolocalizados, "
          f"{con_lineas} con calles dibujadas ({os.path.getsize(destino) // 1024} KB)")


if __name__ == "__main__":
    main()
