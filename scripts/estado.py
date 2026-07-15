"""Calcula el estado actual por bloque y por municipio a partir de la tabla eventos
y lo escribe como web/data/estado.json (que se publica como archivo estático).

Reglas:
  - Estado de un bloque = último evento oficial (canal) de ese bloque en las
    últimas VENTANA_BLOQUE horas: 'afectado' o 'restablecido'. Sin eventos
    recientes -> 'desconocido' (el estado envejece).
  - Reportes de usuarios de los últimos VENTANA_REPORTES minutos se agregan
    como conteos por bloque (señal de confirmación/contradicción).
  - Por municipio: eventos oficiales recientes que lo mencionan, con sus zonas,
    para el detalle del mapa.

Variables de entorno: SUPABASE_URL, SUPABASE_SERVICE_KEY
"""

import json
import math
import os
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone

from supabase import create_client

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "extractor"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract import bloques_en, municipios_en, zonas_en  # noqa: E402
from geocode_zonas import nominatim, bbox_municipios, normalizar, resolver_zonas_numeradas  # noqa: E402
import correcciones  # noqa: E402
import circuitos_id  # noqa: E402

# Lugares con coords corregidas a mano (verdad local): Nominatim ubica mal muchos
# barrios cubanos. clave normalizada -> {lat, lon}.
LUGARES_MANUAL = {normalizar(k): v for k, v in correcciones.lugares_manual().items()}

CACHE_AVERIAS = os.path.join(os.path.dirname(__file__), "..", "data", "geocache_averias.json")
BBOX_HABANA = (-82.70, 22.90, -81.90, 23.35)

RE_HORAS = re.compile(
    r"llev(?:amos|o|an?|a)\s+(?:ya\s+|m[aá]s de\s+|casi\s+)?(\d{1,2})\s*(horas?|h\b|d[ií]as?)",
    re.IGNORECASE,
)


def parsear_averias(texto):
    """Parsea un parte 'Averías existentes hasta el momento' del canal.
    Devuelve items {tipo, municipio, direccion}."""
    items, tipo, municipio = [], None, None
    for linea in texto.split("\n"):
        li = linea.strip()
        # tipo de avería: la UNE usa 🚨 o 🛑 (cambió el emoji); dirección 👉 o 💥.
        m = re.match(r"[🚨🛑]\s*(.+?)\s*:", li)
        if m:
            tipo, municipio = m.group(1), None
            continue
        m = re.match(r"📌\s*Municipios?\s*:\s*(.+)", li)
        if m:
            municipio = m.group(1).strip(" .")
            continue
        m = re.match(r"(?:[👉💥]\s*Direcci[oó]n|📈\s*Afecta)\s*:\s*(.+)", li)
        if m and tipo:
            items.append({"tipo": tipo, "municipio": municipio, "direccion": m.group(1).strip(" .")})
    return items


def zonas_emergencia(sb, ahora, cajas):
    """Zonas del último post de corte por EMERGENCIA en la generación nacional
    (circuitos sueltos, sin bloque ni municipio). Se ubican por el catálogo de
    barrios OSM y, si no aparecen, por Nominatim (misma caché de averías)."""
    post = (
        sb.table("mensajes")
        .select("texto,fecha")
        .eq("chat", "canal")
        .ilike("texto", "%EMERGENCIA en la GENERACI%")
        .ilike("texto", "%se afecta%")
        .gte("fecha", (ahora - timedelta(hours=12)).isoformat())
        .order("message_id", desc=True)
        .limit(1)
        .execute()
        .data
    )
    if not post:
        return {"fecha": None, "items": []}

    ruta = os.path.join(os.path.dirname(__file__), "..", "data", "barrios_osm.json")
    with open(ruta) as f:
        osm = {}
        for b in json.load(f):
            # indexa también sin el sufijo entre paréntesis:
            # "Camilo Cienfuegos (Habana del Este)" debe matchear "Camilo Cienfuegos"
            osm.setdefault(normalizar(b["nombre"].lower()), b)
            pelado = re.sub(r"\s*\(.*?\)", "", b["nombre"]).strip()
            osm.setdefault(normalizar(pelado.lower()), b)
    cache = json.load(open(CACHE_AVERIAS)) if os.path.exists(CACHE_AVERIAS) else {}

    import time

    items = []
    nombres = []
    for linea in zonas_en(post[0]["texto"]):
        nombres += [n.strip(" .") for n in re.split(r",| y ", linea) if 2 < len(n.strip(" .")) < 40]
    for nombre in dict.fromkeys(nombres):
        it = {"nombre": nombre}
        b = osm.get(normalizar(nombre.lower()))
        if b:
            it["lat"], it["lon"] = b["lat"], b["lon"]
        else:
            clave = f"EMG|{nombre}"
            if clave not in cache:
                cache[clave] = nominatim(f"{nombre}, La Habana, Cuba", BBOX_HABANA)
                time.sleep(1.1)
            if cache[clave]:
                it["lat"], it["lon"] = cache[clave]["lat"], cache[clave]["lon"]
            else:
                # último recurso: si menciona un municipio, usar su centro
                mun = (municipios_en(nombre) or [None])[0]
                if mun and mun in cajas:
                    x0, y0, x1, y1 = cajas[mun]
                    it["lat"], it["lon"] = (y0 + y1) / 2, (x0 + x1) / 2
                    it["aproximado"] = True
        items.append(it)

    json.dump(cache, open(CACHE_AVERIAS, "w"), ensure_ascii=False)

    # ¿Hay posts posteriores anunciando el restablecimiento de estos circuitos?
    # ("Se trabaja en el proceso de restablecimiento... Soterrados: 👉Naval...")
    restos = (
        sb.table("mensajes")
        .select("texto")
        .eq("chat", "canal")
        .ilike("texto", "%restablecimiento%")
        .gte("fecha", post[0]["fecha"])
        .execute()
        .data
    )
    texto_restos = normalizar(" ".join(r["texto"] for r in restos))
    for it in items:
        if normalizar(it["nombre"]) in texto_restos:
            it["restablecido"] = True

    return {"fecha": post[0]["fecha"], "items": items}


def _en_anillo(lat, lon, anillo):
    """Punto-en-polígono (ray casting) sobre un anillo [[lon, lat], ...]."""
    dentro, n = False, len(anillo)
    for i in range(n):
        x1, y1 = anillo[i]
        x2, y2 = anillo[(i + 1) % n]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            dentro = not dentro
    return dentro


_AUTORIDAD = None


def restricciones_circuito(codigo, cajas):
    """(caja, polys) del municipio de AUTORIDAD de un circuito (tabla oficial UNE
    o corrección manual), para acotar su geocodificación igual que en
    build_circuitos. Sin esto, los partes de restablecimiento (que traen las
    mismas calles) geocodificaban sin restricción y envenenaban la caché
    compartida (caso GC19/S513)."""
    global _AUTORIDAD
    if _AUTORIDAD is None:
        base = os.path.join(os.path.dirname(__file__), "..")
        munis, polys = {}, {}
        try:
            for cod, info in json.load(open(os.path.join(base, "data", "circuitos_oficial.json"))).items():
                if info.get("municipios"):
                    munis[cod] = info["municipios"]
        except Exception:
            pass
        for cod, m in correcciones.circuitos_municipio().items():
            munis.setdefault(cod, [m])
        try:
            gj = json.load(open(os.path.join(base, "web", "data", "municipios.geojson")))
            polys = {f["properties"]["municipio"]: f["geometry"]["coordinates"][0]
                     for f in gj["features"]}
        except Exception:
            pass
        _AUTORIDAD = (munis, polys)
    munis, polys = _AUTORIDAD
    ms = munis.get(codigo) or []
    anillos = [polys[m] for m in ms if m in polys]
    bbs = [cajas[m] for m in ms if m in cajas]
    if not bbs:
        return None, []
    caja = (min(b[0] for b in bbs), min(b[1] for b in bbs),
            max(b[2] for b in bbs), max(b[3] for b in bbs))
    return caja, anillos


PARTES_LLM_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "partes_llm.json")
_PARTES_LLM = None


def partes_llm_cache():
    """Extracciones LLM de partes (partes_llm.py), {message_id: {...}}. La caché
    es best-effort: si no existe o está rota, el camino regex sigue solo."""
    global _PARTES_LLM
    if _PARTES_LLM is None:
        try:
            _PARTES_LLM = json.load(open(PARTES_LLM_FILE))
        except Exception:
            _PARTES_LLM = {}
    return _PARTES_LLM


def circuitos_llm(message_id):
    """Circuitos validados que el LLM extrajo de un post (sin los por_confirmar)."""
    v = partes_llm_cache().get(str(message_id))
    if not v or v.get("via") != "llm":
        return []
    dudosos = set(v.get("por_confirmar") or [])
    out = []
    for item in v.get("circuitos") or []:
        for cod in item.get("codigos") or []:
            if cod not in dudosos:
                out.append({**item, "codigo": cod})
    return out


def _punto_interior(anillo):
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


def _geocode_mediana_calles(dire, caja, dentro=None):
    """Ubica un circuito por sus CALLES cuando no hay barrio: geocodifica varias y
    toma el centroide del grupo mayoritario (mediana, robusta a outliers). Las calles
    de un circuito están juntas, así que aunque 'Washington' aparezca en varios sitios,
    el grupo que coincide marca la zona. Ej.: '21 desde 2 hasta K, J...' -> Vedado."""
    import time
    parts = re.split(r"\s+(?:desde|hasta|entre|y|e|a)\s+|[;,]", dire, flags=re.I)
    toks, seen = [], set()
    for p in parts:
        p = re.sub(r"\(.*?\)", "", p).strip()
        if len(p) < 1 or p.lower() in seen or re.match(r"^\d{4,}$", p):
            continue
        seen.add(p.lower())
        toks.append(p)
    pts = []
    for t in toks[:4]:
        h = None
        for q in (f"Calle {t}, La Habana, La Habana, Cuba", f"{t}, La Habana, Cuba"):
            h = nominatim(q, caja)
            time.sleep(1.1)
            if h:
                break
        if h and (dentro is None or dentro(h["lat"], h["lon"])):
            pts.append((h["lat"], h["lon"]))
    if not pts:
        return None
    mla, mlo = statistics.median([p[0] for p in pts]), statistics.median([p[1] for p in pts])
    cerca = [p for p in pts if math.hypot((p[0] - mla) * 111000, (p[1] - mlo) * 102000) < 2000] or pts
    return {"lat": sum(p[0] for p in cerca) / len(cerca), "lon": sum(p[1] for p in cerca) / len(cerca)}


def geocodificar_averias(items, cajas, solo_lugar=False, max_nuevos=None):
    """Añade lat/lon usando Nominatim con caché persistente.

    solo_lugar=True (circuitos restablecidos): geocodifica SOLO por barrio/lugar
    nombrado — la pista entre paréntesis '(Miramar)' o el primer nombre de reparto.
    Si la dirección es un simple rango de calles sin barrio ('Calle 33 desde 5ta...'),
    NO se ubica: Nominatim la coloca mal (o colapsa varias al mismo punto), y es
    preferible no pintarla que ponerla en el lugar equivocado.
    solo_lugar=False (averías): comportamiento original (primera calle + reparto)."""
    cache = json.load(open(CACHE_AVERIAS)) if os.path.exists(CACHE_AVERIAS) else {}
    import time
    nuevos = 0  # geocodificaciones con red en esta corrida (para acotar tiempo)

    for it in items:
        # Regla Alamar: "Zonas: 1, 2, 3..." se resuelve sin red (coords OSM).
        alamar = resolver_zonas_numeradas(it["direccion"])
        if alamar:
            it["lat"], it["lon"] = alamar["lat"], alamar["lon"]
            continue
        # Corrección manual de lugar (verdad local): la pista entre paréntesis o el
        # primer nombre. Gana a Nominatim, que ubica mal barrios como Guiteras/Comodoro.
        cands = []
        mh0 = re.search(r"\(([^)]+)\)", it["direccion"])
        if mh0:
            cands.append(mh0.group(1))
        cands.append(re.split(r"[;,]", it["direccion"])[0])
        for cand in cands:
            lm = LUGARES_MANUAL.get(normalizar(cand).strip(" ."))
            if lm:
                it["lat"], it["lon"] = lm["lat"], lm["lon"]
                break
        if "lat" in it:
            continue
        if solo_lugar:
            # Espacio de claves propio 'circ|' con dirección normalizada: evita las
            # entradas viejas mal geocodificadas (que se reintroducen al fusionar el
            # caché) y las duplicadas por puntuación/municipio ruidoso.
            dire = re.sub(r"\s+", " ", it["direccion"]).strip(" .;,")
            clave = f"circ|{dire}"
        else:
            dire = it["direccion"]
            clave = f"{it['municipio']}|{dire}"
        if clave not in cache:
            if max_nuevos is not None and nuevos >= max_nuevos:
                continue  # tope de red por corrida: se geocodifica en la próxima (caché acumula)
            # pista de barrio entre paréntesis: "(Miramar)", "(Reparto Kohly)"
            barrio = None
            mh = re.search(r"\(([^)]+)\)", dire)
            if mh and not re.search(r"parte|atr[aá]s|desde|hasta|cuadr", mh.group(1), re.I):
                barrio = re.sub(r"^(reparto|rpto\.?)\s+", "", mh.group(1).strip(), flags=re.I).strip()
            # pista de barrio como ÚLTIMO segmento: "entre 379 y final, Mulgoba".
            # Sin esto, la mediana de calles manda el punto a cualquier '379' de la
            # ciudad (el caso del circuito 166 pintado en la bahía).
            if solo_lugar and not barrio and "," in dire:
                ult = dire.rsplit(",", 1)[1].strip(" .")
                if ult and not re.match(r"(calle|avenida|ave\.?|cuadrante|desde|hasta|entre|final|parte|\d)", ult, re.I):
                    barrio = re.sub(r"^(reparto|rpto\.?)\s+", "", ult, flags=re.I).strip()
            consultas = []
            if solo_lugar:
                # circuitos: solo barrio/lugar nombrado. Si el circuito trae su caja
                # (municipio oficial conocido), la búsqueda se ACOTA a ella (bounded):
                # evita que una calle homónima de otro municipio se lleve el punto.
                caja = it.get("caja") or BBOX_HABANA
                if barrio:
                    consultas.append(f"{barrio}, La Habana, Cuba")
                primer = re.split(r"[;,]", dire)[0].strip()
                if not re.match(r"(calle|avenida|ave\.?|cuadrante|\d|desde|hasta|entre|parte)\b", primer, re.I):
                    lugar = re.sub(r"^(reparto|rpto\.?)\s+", "", primer, flags=re.I).strip()
                    consultas.append(f"{lugar}, La Habana, Cuba")
            else:
                muni = (municipios_en(it["municipio"] or "") or [None])[0]
                caja = cajas.get(muni, BBOX_HABANA)
                if barrio:
                    consultas.append(f"{barrio}, {muni or 'La Habana'}, La Habana, Cuba")
                calle = re.split(r"\s+(entre|y|desde|hasta)\s+|,", dire)[0].strip()
                partes = [p.strip() for p in dire.split(",")]
                reparto = partes[-1] if len(partes) > 1 and not partes[-1][:1].isdigit() else None
                if reparto:
                    consultas.append(f"{calle}, {reparto}, {muni or 'La Habana'}, Cuba")
                consultas += [f"Calle {calle}, {muni or 'La Habana'}, La Habana, Cuba",
                              f"{calle}, {muni or 'La Habana'}, La Habana, Cuba"]
            # validación por POLÍGONO del municipio de autoridad: la caja es un
            # rectángulo que incluye pedazos de vecinos; un hit fuera del polígono
            # real se descarta (era la causa de circuitos pintados en el municipio
            # de al lado aun con la búsqueda acotada).
            polys = it.get("polys") or []
            dentro = (lambda la, lo: any(_en_anillo(la, lo, p) for p in polys)) if polys else None
            hit = None
            for q in consultas:
                hit = nominatim(q, caja)
                time.sleep(1.1)
                if hit and dentro and not dentro(hit["lat"], hit["lon"]):
                    hit = None
                    continue
                if hit:
                    break
            # circuito sin barrio -> ubicar por sus calles (mediana de varias)
            if not hit and solo_lugar:
                hit = _geocode_mediana_calles(dire, caja, dentro)
            # respaldo: punto representativo del municipio oficial. Peor precisión,
            # pero en el municipio CORRECTO. No vale promediar vértices (en
            # polígonos cóncavos cae FUERA): se toma el punto medio del tramo
            # interior más ancho a la latitud del centroide.
            if not hit and polys:
                hit = _punto_interior(polys[0])
                if hit:
                    hit["match"] = "centro municipio"
            cache[clave] = hit
            nuevos += 1
            if nuevos % 15 == 0:  # guardado parcial: no perder progreso si se corta
                json.dump(cache, open(CACHE_AVERIAS, "w"), ensure_ascii=False)
        if cache.get(clave):
            it["lat"], it["lon"] = cache[clave]["lat"], cache[clave]["lon"]

    os.makedirs(os.path.dirname(CACHE_AVERIAS), exist_ok=True)
    json.dump(cache, open(CACHE_AVERIAS, "w"), ensure_ascii=False)
    return items

VENTANA_BLOQUE_H = 12
VENTANA_MUNICIPIO_H = 6
VENTANA_REPORTES_MIN = 90
VENTANA_PARCIAL_H = 5   # los circuitos restablecidos parcialmente son transitorios


def circuitos_parciales(sb, eventos, ahora, cajas, sen_desde=None):
    """Circuitos con servicio anunciados en posts de restablecimiento parcial
    ('...siguientes circuitos: 👉...'). Se geocodifican (caché de averías) y se
    pintan verdes. Normalmente solo la ventana de 5 h (restablecimientos transitorios
    en rotación). Con el SEN caído (sen_desde) se toman TODOS los restablecidos desde
    la caída, porque en la recuperación esos circuitos quedan con servicio de forma
    permanente y hay que verlos acumulados en el mapa. En ese caso se consulta la
    tabla directamente (acotada por tipo+fecha) para no chocar con el tope de 1000
    filas del fetch general de 12 h."""
    if sen_desde:
        corte = sen_desde
        fuente, off = [], 0
        while True:
            lote = (
                sb.table("eventos").select("chat,tipo,bloque,municipios,zonas,fecha")
                .eq("chat", "canal").eq("tipo", "restablecimiento_parcial")
                .gte("fecha", sen_desde).order("fecha").range(off, off + 999).execute().data
            )
            fuente += lote
            if len(lote) < 1000:
                break
            off += 1000
    else:
        corte = (ahora - timedelta(hours=VENTANA_PARCIAL_H)).isoformat()
        fuente = eventos
    items, vistos = [], set()
    for ev in fuente:
        if ev["chat"] != "canal" or ev["tipo"] != "restablecimiento_parcial":
            continue
        if ev["fecha"] < corte:
            continue
        muni = (ev["municipios"] or [None])[0]
        for zona in ev["zonas"] or []:
            clave = f"{muni}|{zona}"
            if clave in vistos:
                continue
            vistos.add(clave)
            it = {"municipio": muni, "direccion": zona, "bloque": ev["bloque"], "fecha": ev["fecha"]}
            # identidad del circuito: código explícito, número conocido del
            # catálogo, o matching por calles/zonas (circuitos_id, reglas seguras)
            res = circuitos_id.resolver(zona)
            if res["codigos"] and not res["por_confirmar"]:
                it["codigo"] = res["codigos"][0]
                if res["via"] in ("codigo", "codigo_catalogo"):
                    it["direccion"] = res["direccion"]  # sin el código delante
            items.append(it)

    # LLM primero con fallback regex (plan tarea 4): restablecimientos que el LLM
    # extrajo de posts con formatos que el extractor clásico no entendió. Solo
    # circuitos validados (sin por_confirmar) y no vistos ya por el regex.
    ya_cod = {it.get("codigo") for it in items if it.get("codigo")}
    cat_coords = {}
    try:
        for cc in json.load(open(SALIDA.replace("estado.json", "circuitos.json")))["circuitos"]:
            cat_coords[cc["codigo"]] = cc
    except Exception:
        pass
    for mid, v in partes_llm_cache().items():
        if v.get("via") != "llm" or v.get("tipo") != "restablecimiento":
            continue
        if (v.get("fecha") or "") < corte:
            continue
        dudosos = set(v.get("por_confirmar") or [])
        for item in v.get("circuitos") or []:
            for cod in item.get("codigos") or []:
                if cod in dudosos or cod in ya_cod:
                    continue
                ya_cod.add(cod)
                info = cat_coords.get(cod) or {}
                nuevo = {"municipio": item.get("municipio") or info.get("municipio"),
                         "direccion": item.get("calles") or info.get("calles") or "",
                         "bloque": None, "fecha": v["fecha"], "codigo": cod}
                if info.get("lat") is not None:  # coords del catálogo: sin red
                    nuevo["lat"], nuevo["lon"] = info["lat"], info["lon"]
                items.append(nuevo)

    # Los que resuelve la regla Alamar no gastan red: se resuelven todos aquí.
    # El tope solo acota los que requieren Nominatim.
    red, zonas_verdes = [], set()
    for it in items:
        # circuito identificado -> su geocodificación se acota al municipio de
        # autoridad (misma restricción que build_circuitos; caché compartida)
        if it.get("codigo") and "lat" not in it:
            caja_c, polys_c = restricciones_circuito(it["codigo"], cajas)
            if caja_c:
                it["caja"], it["polys"] = caja_c, polys_c
        alamar = resolver_zonas_numeradas(it["direccion"])
        if alamar:
            it["lat"], it["lon"] = alamar["lat"], alamar["lon"]
            # identidades de zona (para teñir de verde su polígono en el mapa):
            # "Habana del Este|Zona N" — el mismo id que usa zonas_poligonos.
            for n in re.findall(r"\b(\d{1,2})\b", re.sub(r"micro\s*\w+", "", it["direccion"], flags=re.I)):
                zonas_verdes.add(f"Habana del Este|Zona {n}")
        else:
            red.append(it)
    geocodificar_averias(red[:40], cajas, solo_lugar=True)
    return [it for it in items if "lat" in it], sorted(zonas_verdes)
SALIDA = os.path.join(os.path.dirname(__file__), "..", "web", "data", "estado.json")


def detectar_evento_nacional(sb, ahora):
    """Desconexión total del SEN ACTIVA: hay un aviso de desconexión en las últimas
    72 h y NO se ha reanudado la rotación normal (un parte de 'Actualización de
    afectaciones' POSTERIOR a la desconexión = el SEN volvió a operar por bloques).
    Devuelve dict {desde, causa, restablecido_pct?, pct_fecha?} o None."""
    desc = (
        sb.table("mensajes").select("fecha,texto").eq("chat", "canal")
        .ilike("texto", "%desconexi%total%")
        # la UNE alterna la redacción: "Sistema Electroenergético Nacional"
        # (oct/2025), "Sistema Eléctrico Nacional" (10/jul/2026) y "del SEN" a
        # secas (14/jul/2026); el _ tolera la tilde
        .or_("texto.ilike.%electroenerg%,texto.ilike.%el_ctrico nacional%,"
             "texto.ilike.%del sen%")
        .gte("fecha", (ahora - timedelta(hours=72)).isoformat())
        .order("message_id", desc=True).limit(1).execute().data
    )
    f_desc = desc[0]["fecha"] if desc else None
    if not f_desc:
        # respaldo LLM: el extractor clasifica 'caida_sen' aunque la redacción
        # sea nueva (el regex de arriba ya falló 3 veces por cambios de la UNE)
        corte = (ahora - timedelta(hours=72)).isoformat()
        fechas = [v.get("fecha") for v in partes_llm_cache().values()
                  if v.get("via") == "llm" and v.get("tipo") == "caida_sen"
                  and (v.get("fecha") or "") >= corte]
        f_desc = max(fechas) if fechas else None
    if not f_desc:
        return None
    reanuda = (
        sb.table("mensajes").select("fecha").eq("chat", "canal")
        .ilike("texto", "%Actualización de afectaciones%")
        .gt("fecha", f_desc).limit(1).execute().data
    )
    # también cierra el evento el anuncio EXPLÍCITO de restablecimiento total
    # ("restablecido el Sistema Eléctrico Nacional", 15/jul/2026). OJO: los
    # parciales dicen "se han restablecido: Subestaciones..." y NO cuentan
    # (por eso se exige "restablecido el sistema/sen").
    if not reanuda:
        reanuda = (
            sb.table("mensajes").select("fecha").eq("chat", "canal")
            .or_("texto.ilike.%restablecido el sistema el%,"
                 "texto.ilike.%restablecido el sen%")
            .gt("fecha", f_desc).limit(1).execute().data
        )
    if reanuda:
        return None
    ev = {"desde": f_desc, "causa": "Desconexión total del SEN"}
    # % de la ciudad restablecido (parte "...Sistema Eléctrico Capitalino: ...51%").
    prog = (
        sb.table("mensajes").select("fecha,texto").eq("chat", "canal")
        .ilike("texto", "%Capitalino%").gt("fecha", f_desc)
        .order("message_id", desc=True).limit(1).execute().data
    )
    if prog:
        mp = re.search(r"para el\s*([\d]+(?:[.,]\d+)?)\s*%", prog[0]["texto"])
        if mp:
            ev["restablecido_pct"] = float(mp.group(1).replace(",", "."))
            ev["pct_fecha"] = prog[0]["fecha"]
    return ev


POBLACION_HABANA = 1_749_964   # suma de población por municipio de La Habana (tabla)


def estimar_poblacion(sb, ahora, deficit):
    """Cifra OFICIAL de personas con/sin corriente cuando la Empresa la publica
    (parte 'Actualización del Sistema Eléctrico Capitalino': '...para el X% de la
    ciudad'). Si no hay, devuelve None y el frontend estima en tiempo real por el
    conteo de circuitos con/sin servicio (que varía en cada actualización)."""
    cap = (
        sb.table("mensajes").select("fecha,texto").eq("chat", "canal")
        .ilike("texto", "%Capitalino%").gte("fecha", (ahora - timedelta(hours=12)).isoformat())
        .order("message_id", desc=True).limit(1).execute().data
    )
    if cap:
        m = re.search(r"para el\s*([\d]+(?:[.,]\d+)?)\s*%", cap[0]["texto"])
        if m:
            con = max(0.0, min(100.0, float(m.group(1).replace(",", "."))))
            return {"con_pct": round(con, 1), "sin_pct": round(100 - con, 1),
                    "con_personas": round(POBLACION_HABANA * con / 100),
                    "sin_personas": round(POBLACION_HABANA * (100 - con) / 100),
                    "fuente": "oficial", "fecha": cap[0]["fecha"]}
    return None


def main():
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    ahora = datetime.now(timezone.utc)
    evento_nacional = detectar_evento_nacional(sb, ahora)
    desde = (ahora - timedelta(hours=VENTANA_BLOQUE_H)).isoformat()

    # Retención: los reportes vecinales solo se muestran 6h; purgamos los de más
    # de 48h para que la tabla no crezca sin límite (privacidad + cuota Supabase).
    sb.table("reportes").delete().lt(
        "fecha", (ahora - timedelta(hours=48)).isoformat()
    ).execute()

    eventos = (
        sb.table("eventos")
        .select("chat,tipo,bloque,municipios,zonas,causa,fecha")
        .gte("fecha", desde)
        .order("fecha")
        .execute()
        .data
    )

    # --- Estado por bloque (0 = Circuitos de Emergencia) ---
    # "parcial en curso" solo si el post de restablecimiento parcial es reciente
    # (misma ventana que los circuitos verdes); si no, ya no hay nada "en curso".
    corte_parcial = (ahora - timedelta(hours=VENTANA_PARCIAL_H)).isoformat()
    bloques = {b: {"estado": "desconocido", "desde": None, "causa": None} for b in range(0, 7)}
    for ev in eventos:
        if ev["chat"] != "canal" or ev["bloque"] is None:
            continue
        b = bloques[ev["bloque"]]
        if ev["tipo"] == "restablecimiento_parcial":
            # "se trabaja en el restablecimiento, teniendo con servicio..." prueba
            # que el bloque SIGUE afectado (aunque su afectación haya envejecido a
            # desconocido): reafirma el estado y marca el parcial en curso.
            if b["estado"] != "restablecido":
                if b["estado"] != "afectado":
                    b["desde"] = ev["fecha"]
                b["estado"] = "afectado"
                if ev["fecha"] >= corte_parcial:
                    b["parcial"] = ev["fecha"]
                else:
                    b.pop("parcial", None)
            continue
        nuevo = "afectado" if ev["tipo"] == "afectacion" else "restablecido"
        if ev["tipo"] == "afectacion":
            b["ult_afecta"] = ev["fecha"]  # hora del aviso de afectación más reciente
        if b["estado"] != nuevo:
            b["desde"] = ev["fecha"]
            b.pop("parcial", None)
        elif ev["tipo"] == "afectacion":
            # nueva afectación con el bloque ya afectado = se volvió a cortar:
            # supersede cualquier restablecimiento parcial anterior.
            b.pop("parcial", None)
        b["estado"] = nuevo
        b["causa"] = ev["causa"]

    # --- Parte oficial de horas acumuladas ("Actualización de afectaciones...") ---
    # Si la Empresa declara más horas que nuestro conteo, gana el mayor:
    # se retrocede el 'desde' a (fecha del parte - duración declarada).
    parte_h = (
        sb.table("mensajes")
        .select("message_id,fecha,texto")
        .eq("chat", "canal")
        .ilike("texto", "%Actualización de afectaciones%")
        .gte("fecha", (ahora - timedelta(hours=4)).isoformat())
        .order("message_id", desc=True)
        .limit(1)
        .execute()
        .data
    )
    deficit = None
    if parte_h:
        texto_parte = parte_h[0]["texto"]
        fecha_parte = datetime.fromisoformat(parte_h[0]["fecha"])
        listados = set()
        for m in re.finditer(
            r"Bloque\s*(\d)\s*(\d{1,3})\s*horas?(?:\s*y\s*(\d{1,2})\s*minutos?)?",
            texto_parte,
        ):
            nb, hh, mm = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
            if nb not in bloques:
                continue
            listados.add(nb)
            b = bloques[nb]
            # No resucitar un bloque restablecido por un evento POSTERIOR al parte:
            # si ya se restableció después de esta hora oficial, el parte está viejo.
            if b["estado"] == "restablecido" and b["desde"] and b["desde"] >= fecha_parte.isoformat():
                continue
            # El parte declara las horas del corte ACTUAL: es la fuente autoritativa
            # de la duración y gana SIEMPRE (aunque diga menos que nuestro conteo —
            # el bloque se restableció y se volvió a cortar; nosotros no lo captamos).
            b["estado"] = "afectado"
            b.setdefault("causa", "déficit de generación")
            b["desde"] = (fecha_parte - timedelta(hours=hh, minutes=mm)).isoformat()
            b["duracion_fuente"] = "parte oficial"

        if listados:
            # DESCARTE (solo cuando el parte es por BLOQUE): un bloque 1-6 que NO
            # aparece está restablecido (salvo afectación posterior). Señal de "vuelve
            # la luz". OJO: solo aplica en formato por bloque; si el parte es por
            # circuito, NO descartamos (si no, marcaríamos todo verde en falso).
            for nb in range(1, 7):
                if nb in listados:
                    continue
                b = bloques[nb]
                # ¿Hubo un aviso de afectación DESPUÉS del parte? Entonces el bloque se
                # volvió a cortar tras haber tenido luz: sigue apagado desde ese aviso,
                # NO restablecido. (Bug real: parte 6:02 sin B3, afectan B3 a las 7:16.)
                if b.get("ult_afecta") and b["ult_afecta"] > fecha_parte.isoformat():
                    b["estado"] = "afectado"
                    b["desde"] = b["ult_afecta"]
                    b["duracion_fuente"] = "afectación posterior al parte"
                    continue
                b["estado"] = "restablecido"
                b["desde"] = parte_h[0]["fecha"]
                b["duracion_fuente"] = "descarte del parte oficial"
                b.pop("parcial", None)
        else:
            # Formato POR CIRCUITO (tras la caída del SEN la Empresa dejó de reportar
            # por bloque): "se afectan 620 MW ... ✅R454 - 27 horas...". No hay dato por
            # bloque -> los dejamos 'desconocido' (no verde falso) y surfaceamos los
            # circuitos afectados con sus calles del catálogo (los que conocemos).
            # admite el municipio entre paréntesis que la UNE añadió en jul/2026:
            # "✅2075 (Lisa)   29 horas" además del viejo "✅R454 - 27 horas"
            circ = [{"codigo": c.upper(), "horas": int(h)}
                    for c, _mun, h in re.findall(
                        r"([A-Za-z]{1,3}\d{1,4}|\d{3,4})\s*(?:\(([^)]{2,30})\))?\s*-?\s*(\d+)\s*horas?",
                        texto_parte)]
            # LLM primero con fallback regex (plan tarea 4): si este parte está en
            # la caché LLM, sus circuitos COMPLEMENTAN a los del regex (capturan
            # formatos que el regex no entiende; ya vienen validados contra el
            # catálogo). El regex se mantiene: nunca peor que hoy.
            ya = {c["codigo"] for c in circ}
            for lc in circuitos_llm(parte_h[0].get("message_id")):
                if lc["codigo"] not in ya:
                    circ.append({"codigo": lc["codigo"],
                                 "horas": int(lc["horas"] or 0)})
                    ya.add(lc["codigo"])
            if circ:
                # Base de conocimiento de circuitos (código -> calles/municipio/bloque/coords)
                cat = {}
                try:
                    for cc in json.load(open(SALIDA.replace("estado.json", "circuitos.json")))["circuitos"]:
                        cat[cc["codigo"]] = cc
                except Exception:
                    pass
                geo = []
                for it in circ:
                    info = cat.get(it["codigo"])
                    if info:
                        it["calles"] = info.get("calles")
                        it["bloque"] = info.get("bloque")
                        if info.get("lineas"):
                            it["lineas"] = info["lineas"]  # geometría de calles reales (OSM)
                        if info.get("lat") is not None:
                            it["lat"], it["lon"] = info["lat"], info["lon"]  # ya geolocalizado en el catálogo
                        elif it["calles"]:
                            geo.append({"municipio": info.get("municipio"), "direccion": it["calles"], "_it": it})
                # geocodificamos los que tienen calles conocidas para pintarlos en el mapa
                geocodificar_averias(geo, bbox_municipios(), solo_lugar=True)
                for g in geo:
                    if "lat" in g:
                        g["_it"]["lat"], g["_it"]["lon"] = g["lat"], g["lon"]
                mmw = re.search(r"(\d{2,4})\s*MW", texto_parte)
                deficit = {
                    "fecha": parte_h[0]["fecha"],
                    "mw": int(mmw.group(1)) if mmw else None,
                    "por_circuito": True,
                    "circuitos": circ,
                }

    # --- Reportes de usuarios recientes por bloque ---
    corte_rep = (ahora - timedelta(minutes=VENTANA_REPORTES_MIN)).isoformat()
    for ev in eventos:
        if ev["chat"] != "comentarios" or not ev["bloque"] or ev["fecha"] < corte_rep:
            continue
        b = bloques[ev["bloque"]]
        clave = "reportes_sin" if ev["tipo"] == "reporte_sin_servicio" else "reportes_con"
        b[clave] = b.get(clave, 0) + 1

    # --- Horas sin corriente según los propios usuarios ("llevamos 26 horas sin...") ---
    corte_txt = (ahora - timedelta(hours=3)).isoformat()
    comentarios = (
        sb.table("mensajes")
        .select("texto,fecha")
        .eq("chat", "comentarios")
        .gte("fecha", corte_txt)
        .execute()
        .data
    )
    horas_rep = {b: [] for b in range(0, 7)}
    for c in comentarios:
        m = RE_HORAS.search(c["texto"] or "")
        if not m:
            continue
        horas = int(m.group(1)) * (24 if m.group(2).lower().startswith("d") else 1)
        menciones = bloques_en(c["texto"])
        if len(menciones) == 1 and 0 < horas <= 96:
            horas_rep[menciones[0]].append(horas)
    for b, lista in horas_rep.items():
        if len(lista) >= 2:  # al menos 2 usuarios coincidiendo
            bloques[b]["horas_reportadas"] = round(statistics.median(lista))
            bloques[b]["n_reportes_horas"] = len(lista)

    # --- Averías activas: último parte "Averías existentes" (snapshot oficial) ---
    averias = {"fecha": None, "items": []}
    parte = (
        sb.table("mensajes")
        .select("texto,fecha")
        .eq("chat", "canal")
        .ilike("texto", "%Averías existentes%")
        .gte("fecha", (ahora - timedelta(hours=24)).isoformat())
        .order("message_id", desc=True)
        .limit(1)
        .execute()
        .data
    )
    if parte:
        items = parsear_averias(parte[0]["texto"])
        averias = {"fecha": parte[0]["fecha"], "items": geocodificar_averias(items, bbox_municipios())}

    # --- Cortes de emergencia en circuitos sueltos ---
    emergencia = zonas_emergencia(sb, ahora, bbox_municipios())
    parciales, zonas_verdes = circuitos_parciales(
        sb, eventos, ahora, bbox_municipios(),
        sen_desde=evento_nacional["desde"] if evento_nacional else None,
    )

    # Reportes vecinales ubicados por el LLM (comentarios en lenguaje libre).
    corte_com = (ahora - timedelta(hours=6)).isoformat()
    reportes_llm = [
        {"lat": r["lat"], "lon": r["lon"], "tipo": r["reporta"],
         "lugar": r["lugar"], "horas": r["horas"], "fecha": r["fecha"]}
        for r in (
            sb.table("comentarios_llm").select("lat,lon,reporta,lugar,horas,fecha")
            .gte("fecha", corte_com).not_.is_("lat", "null")
            .in_("reporta", ["sin_corriente", "con_corriente"]).execute().data
        )
    ]

    # --- Estado DAF (microcortes): último evento oficial con causa DAF ---
    daf = {"estado": "sin_eventos", "desde": None}
    for ev in eventos:
        if ev["chat"] == "canal" and ev["causa"] == "DAF":
            nuevo = "activo" if ev["tipo"] == "afectacion" else "restablecido"
            if daf["estado"] != nuevo:
                daf["desde"] = ev["fecha"]
            daf["estado"] = nuevo

    # --- Detalle por municipio (eventos oficiales recientes) ---
    corte_mun = (ahora - timedelta(hours=VENTANA_MUNICIPIO_H)).isoformat()
    municipios = {}
    for ev in eventos:
        if ev["fecha"] < corte_mun or not ev["municipios"]:
            continue
        for m in ev["municipios"]:
            d = municipios.setdefault(m, {"eventos": [], "afectaciones": 0, "restablecimientos": 0, "reportes_sin": 0})
            if ev["chat"] == "canal":
                if ev["tipo"] == "afectacion":
                    d["afectaciones"] += 1
                elif ev["tipo"] == "restablecimiento":
                    d["restablecimientos"] += 1
                d["eventos"].append(
                    {
                        "tipo": ev["tipo"],
                        "bloque": ev["bloque"],
                        "causa": ev["causa"],
                        "zonas": (ev["zonas"] or [])[:4],
                        "fecha": ev["fecha"],
                    }
                )
            elif ev["tipo"] == "reporte_sin_servicio" and ev["fecha"] >= corte_rep:
                d["reportes_sin"] += 1
    for d in municipios.values():
        d["eventos"] = d["eventos"][-8:][::-1]  # los 8 más recientes, nuevo primero

    # Con el SEN caído TODO el país está sin corriente: forzamos los 6 bloques a
    # 'afectado' y seguimos contando horas, aunque los eventos de la desconexión ya
    # hayan envejecido fuera de la ventana normal. El conteo arranca desde la caída
    # del SEN, salvo que el bloque ya viniera apagado de antes (se conserva ese inicio).
    if evento_nacional:
        f_desc = evento_nacional["desde"]
        for nb in range(1, 7):
            b = bloques[nb]
            ya_apagado_antes = b["estado"] == "afectado" and b.get("desde") and b["desde"] <= f_desc
            b["estado"] = "afectado"
            b["causa"] = "desconexión total del SEN"
            b["desde"] = b["desde"] if ya_apagado_antes else f_desc
            b.pop("parcial", None)

    salida = {
        "generado": ahora.isoformat(),
        "evento_nacional": evento_nacional,
        "ventanas": {
            "bloque_horas": VENTANA_BLOQUE_H,
            "municipio_horas": VENTANA_MUNICIPIO_H,
            "reportes_min": VENTANA_REPORTES_MIN,
        },
        "bloques": bloques,
        "deficit": deficit,
        "poblacion": estimar_poblacion(sb, ahora, deficit),
        "daf": daf,
        "averias": averias,
        "emergencia": emergencia,
        "parciales": parciales,
        "zonas_verdes": zonas_verdes,
        "reportes_llm": reportes_llm,
        "municipios": municipios,
    }
    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    with open(SALIDA, "w") as f:
        json.dump(salida, f, ensure_ascii=False)

    resumen = ", ".join(f"B{b}:{d['estado'][:3]}" for b, d in bloques.items())
    print(f"Estado OK ({resumen}) | municipios con actividad: {len(municipios)}")


if __name__ == "__main__":
    main()
