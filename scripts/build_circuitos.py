"""Construye web/data/circuitos.json: catálogo APRENDIDO de circuitos de la red a
partir de los códigos oficiales que la Empresa pone en sus partes (ej. "A1443-
Rio Verde…", "PG940- Comodoro…"). El código es el prefijo de la subestación +
número del circuito; es un identificador estable de un tramo físico de red que
alimenta un conjunto fijo de calles.

No acumula un fichero propio: reconstruye desde el histórico del canal en
data/canal_cache.json (commiteado). Ese caché se llena INCREMENTALMENTE desde
Supabase (solo mensajes nuevos por message_id + una ventana reciente para
recoger partes editados por la Empresa), así que la corrida ya no re-lee las
~12 mil filas del canal en cada paso. Para cada código guarda las calles que
sirve, el municipio, el bloque en que rota (inferido del post), cuántas veces se
ha visto, cuándo, y su último estado conocido (con/sin servicio).
"""

import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from supabase import create_client

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "extractor"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract import (bloques_en, municipios_en, causa_en, normalizar,  # noqa: E402
                     quitar_avisos, texto_degenerado)
from circuitos_id import _tokens, canonico, es_conocido  # noqa: E402
from evidencia_calles import _nombres_calles  # noqa: E402

RAIZ = os.path.join(os.path.dirname(__file__), "..")
CACHE_LINEAS = os.path.join(RAIZ, "data", "geocache_circuitos_lineas.json")
CACHE_INTENTOS = os.path.join(RAIZ, "data", "geocache_lineas_intentos.json")
CAMBIOS_FILE = os.path.join(RAIZ, "data", "cambios_direccion.json")
CONTEO_USUARIO_FILE = os.path.join(RAIZ, "data", "conteo_usuario.json")
ESTADO_FILE = os.path.join(RAIZ, "web", "data", "estado.json")
CATALOGO_SALIDA = os.path.join(RAIZ, "web", "data", "circuitos.json")
CANAL_CACHE = os.path.join(RAIZ, "data", "canal_cache.json")
# La Empresa edita partes ya publicados (ingest.py re-sub los DAF editados):
# los últimos N mensajes del canal se re-leen SIEMPRE para recoger la versión final.
VENTANA_EDICIONES = 50

# Resolución de geometría (Overpass). El tope anterior de 8 por corrida dejaba
# 108 circuitos pendientes a 13 ingestas de distancia; con el presupuesto de
# reloj se puede subir sin arriesgar el timeout del job.
MAX_GEO = int(os.environ.get("MAX_GEO_CIRCUITOS", "40"))
MAX_SEGUNDOS_GEO = int(os.environ.get("MAX_SEGUNDOS_GEO", "240"))
REINTENTOS = int(os.environ.get("REINTENTOS_GEO", "3"))
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
# separador entre códigos: coma, barra o " y "/" e " ("T44 y T41 Toyo" jul/2026)
_SEP = r"(?:\s*[,/]\s*|\s+[yeYE]\s+)"
RE_CIRC = re.compile(
    # la viñeta puede traer modificador de tono de piel ("👉🏼", jul/2026)
    #
    # La viñeta NO se ancla a inicio de línea: la Empresa mete varios circuitos
    # en la misma línea separados por espacios ("👉2073Calle 256... Arroyo
    # Arenas    👉AL53:Zonas: 1, 2, 3..."), y con "^\s*👉" el segundo se perdía
    # y además su texto quedaba pegado a las calles del primero. La descripción
    # termina en la siguiente viñeta o al final de la línea.
    r"(?m)👉[\U0001F3FB-\U0001F3FF]?\s*"
    rf"({_COD}(?:{_SEP}{_COD})*)"
    r"\s*([-–:])?\s*(.+?)\s*(?=👉|$)"
)
# Respaldo para el parte semanal DAF: ocasionalmente la Empresa omite la viñeta
# en una línea (p. ej. ``AL53 : Zonas...``). Se exige prefijo alfabético y un
# separador explícito para no convertir números de calles en circuitos.
RE_CIRC_DAF_SIN_VINETA = re.compile(
    rf"(?m)^\s*([A-Za-z]{{1,3}}\d{{1,4}}(?:{_SEP}[A-Za-z]{{1,3}}\d{{1,4}})*)"
    r"\s*[-–:]\s*(.+?)\s*$"
)
RE_UN_CODIGO = re.compile(rf"^{_COD}$")


def _cobertura(nuevo, viejo):
    """Solapamiento de tokens entre dos descripciones de calles (0-1).
    < 0.25 indica que son zonas diferentes (cambio de dirección del circuito)."""
    if not viejo or not nuevo:
        return 0.0
    tn, tv = _tokens(nuevo), _tokens(viejo)
    if not tn or not tv:
        return 0.0
    return len(tn & tv) / min(len(tn), len(tv))


def _adoptar_calles(r, nuevo, fuente=None):
    """Actualiza la dirección del registro con la última lectura del parte
    (regex o LLM), compartiendo la regla para que las dos vías no diverjan.

    Primero descarta basura del LLM (bucle de repetición / longitud imposible:
    el parte 78278 dio al L316 una dirección de 3014 chars repitiendo 'uda'
    que la regla "longest wins" habría publicado en circuitos.html). Sobre
    texto sano: si la nueva dirección solapa poco con la conocida (< 0.25 de
    cobertura de tokens), es un CAMBIO de dirección y la nueva gana; si
    solapa bien, gana la más completa."""
    if not nuevo or texto_degenerado(nuevo, fuente):
        return
    if (not r["calles"] or _cobertura(nuevo, r["calles"]) < 0.25
            or len(nuevo) > len(r["calles"])):
        r["calles"] = nuevo

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
RE_LINEA_PERIODO_DAF = re.compile(r"(?im)^.*\bDAF\s*:.*$")
RE_FIN_DAF = re.compile(
    r"jueves\s+(\d{1,2})\s+de\s+([a-záéíóúñ]+)", re.IGNORECASE
)
RE_INICIO_DAF = re.compile(
    r"viernes\s+(\d{1,2})(?:\s+de\s+([a-záéíóúñ]+))?", re.IGNORECASE
)


def _fecha_cercana(dia, mes, publicada):
    """Fecha con el año más cercano al parte (resuelve semanas dic/ene)."""
    candidatas = []
    for anio in (publicada.year - 1, publicada.year, publicada.year + 1):
        try:
            candidatas.append(date(anio, mes, dia))
        except ValueError:
            pass
    return min(candidatas, key=lambda d: abs((d - publicada).days))


def extraer_daf_oficial(filas, ahora=None):
    """Última rotación semanal DAF publicada por la Empresa.

    Los partes pueden traer dos períodos en el mismo mensaje (semana saliente y
    entrante). Se elige el que cubre la fecha actual; si ninguno está vigente se
    conserva la última lista, marcada como vencida, para informar sin presentarla
    como estado actual.
    """
    hoy = (ahora or datetime.now(ZoneInfo("America/Havana"))).date()
    rotaciones = []
    for f in filas:
        texto = f.get("texto") or ""
        plano = normalizar(texto)
        if "rotad" not in plano or "viernes" not in plano or \
                ("circuitos designados" not in plano and
                 "circuitos protegidos" not in plano):
            continue
        publicada = datetime.fromisoformat(f["fecha"]).date()
        marcas = list(RE_LINEA_PERIODO_DAF.finditer(texto))
        for i, marca in enumerate(marcas):
            linea = marca.group(0)
            fin_m = RE_FIN_DAF.search(linea)
            if not fin_m:
                continue
            mes_fin = MESES.get(normalizar(fin_m.group(2)))
            if not mes_fin:
                continue
            hasta = _fecha_cercana(int(fin_m.group(1)), mes_fin, publicada)
            ini_m = RE_INICIO_DAF.search(linea)
            if ini_m:
                mes_ini = MESES.get(normalizar(ini_m.group(2))) if ini_m.group(2) else mes_fin
                if not mes_ini:
                    continue
                # "viernes 31 al jueves 6 de agosto": el viernes es del mes anterior.
                if not ini_m.group(2) and int(ini_m.group(1)) > hasta.day:
                    mes_ini = 12 if mes_fin == 1 else mes_fin - 1
                desde = _fecha_cercana(int(ini_m.group(1)), mes_ini, hasta)
                if desde > hasta:
                    desde = date(desde.year - 1, desde.month, desde.day)
            else:
                # Algunos encabezados solo dicen "hasta el jueves N".
                desde = hasta - timedelta(days=6)

            fin_contenido = marcas[i + 1].start() if i + 1 < len(marcas) else len(texto)
            contenido = texto[marca.end():fin_contenido]
            codigos = []
            circuitos_en_texto = list(RE_CIRC.finditer(contenido))
            circuitos_en_texto += list(RE_CIRC_DAF_SIN_VINETA.finditer(contenido))
            circuitos_en_texto.sort(key=lambda m: m.start())
            for circ in circuitos_en_texto:
                for cod in re.split(_SEP, circ.group(1)):
                    cod = cod.strip().upper()
                    if RE_UN_CODIGO.match(cod) and cod not in codigos:
                        codigos.append(cod)
            if codigos:
                rotaciones.append({
                    "desde": desde.isoformat(),
                    "hasta": hasta.isoformat(),
                    "publicado": f["fecha"],
                    "message_id": f["message_id"],
                    "circuitos": codigos,
                })
    if not rotaciones:
        return None
    activas = [r for r in rotaciones
               if date.fromisoformat(r["desde"]) <= hoy <= date.fromisoformat(r["hasta"])]
    elegida = max(activas or rotaciones,
                  key=lambda r: (r["hasta"], r["publicado"]))
    return {**elegida, "vigente": elegida in activas}


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
    if re.search(r"restableci|teniendo con servicio|quedan? con servicio|en servicio|reparad", plano):
        return "con servicio"
    # "se afect" cubre presente y pasado; "afectando" el gerundio de las averías;
    # "se localiza aver"/"via libre" son los avisos de avería y de emergencia
    if re.search(r"se afect|afectaci|afectando|afectados|se localiza aver|via libre"
                 r"|disparo autom|\bdaf\b|emergencia", plano):
        return "sin servicio"
    return None


def usar_extraccion_llm(v):
    """¿Esta entrada de la caché de partes_llm puede modificar el catálogo?

    El criterio es la EVIDENCIA que trae la entrada, no un número de versión.
    Antes se exigía `validador_version == 2` y, cuando partes_llm.py pasó a la
    v3, el refuerzo del LLM quedó desconectado en silencio: 1.316 extracciones
    válidas descartadas durante semanas, con circuitos publicando un estado
    viejo (p. ej. AL53 el 12-ago). Una igualdad exacta convierte cualquier
    mejora del validador en una regresión callada.

    Lo que v2 introdujo y aquí se comprueba es `codigos_estado`: la lista de
    códigos realmente escritos en el parte, sin la cual no se puede saber si el
    LLM tenía derecho a cambiar el estado.
    """
    if not v or v.get("via") != "llm":
        return False
    if (v.get("validador_version") or 0) < 2:
        return False
    return all("codigos_estado" in item for item in (v.get("circuitos") or []))


def limpiar_calles(texto):
    # quitar_avisos suelta el aviso institucional que la Empresa pega en la misma
    # línea ("...El Mamey.  📣Usted puede, aún siendo cliente..."): sin esto las
    # calles del circuito se contaminaban y no resolvían contra OpenStreetMap.
    return quitar_avisos(re.sub(r"[📉🚧✅📣📌🔔‼️⚡️👉💥📈🔹]+", "", texto)).strip(" .-–")


def _cargar_cache_canal():
    try:
        c = json.load(open(CANAL_CACHE))
        if isinstance(c.get("filas"), dict):
            return c
    except Exception:
        pass
    # caché nuevo o corrupto: se reconstruye leyendo todo el canal una vez
    return {"version": 1, "cursor": 0, "filas": {}}


def _guardar_cache_canal(cache):
    tmp = CANAL_CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f, ensure_ascii=False)
    os.replace(tmp, CANAL_CACHE)


def cargar_canal(sb):
    """Histórico del canal desde el caché local, refrescado incrementalmente.

    Baja solo los mensajes nuevos (message_id > cursor) y re-lee la ventana de
    ediciones recientes. Devuelve las filas en orden cronológico.
    """
    cache = _cargar_cache_canal()
    filas, off = [], 0
    while True:
        lote = (sb.table("mensajes").select("message_id,fecha,texto").eq("chat", "canal")
                .gt("message_id", cache["cursor"]).order("message_id")
                .range(off, off + 999).execute().data)
        filas += lote
        if len(lote) < 1000:
            break
        off += 1000
    recientes = (sb.table("mensajes").select("message_id,fecha,texto")
                 .eq("chat", "canal").order("message_id", desc=True)
                 .limit(VENTANA_EDICIONES).execute().data)
    for m in filas + recientes:
        cache["filas"][str(m["message_id"])] = m
    if filas:
        cache["cursor"] = max(m["message_id"] for m in filas)
    _guardar_cache_canal(cache)
    return sorted(cache["filas"].values(),
                  key=lambda m: (m.get("fecha") or "", m.get("message_id") or 0)), \
        len(filas), len(cache["filas"])


def main():
    # 0) Aprende los circuitos recurrentes que el LLM ve pero el catálogo no
    #    registra (embudo 'por_confirmar'). Sin red, desde partes_llm.json; es
    #    el paso que cierra el ciclo para que sus códigos entren solos. Debe ir
    #    ANTES de todo consumo del catálogo (es_conocido y canonico ya lo ven).
    import aprende_circuitos  # noqa: E402
    aprende_circuitos.main()

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    filas, n_nuevos, n_total = cargar_canal(sb)
    print(f"canal_cache: {n_total} mensajes, {n_nuevos} nuevos de la DB")

    # catálogo oficial (se usa para validar códigos numéricos ambiguos y, más
    # abajo, para la fusión de municipios/calles oficiales)
    try:
        oficial = json.load(open(OFICIAL_FILE))
    except Exception:
        oficial = {}
    # Baseline: catálogo de la corrida anterior (para detectar cambios de dirección)
    prev_circuitos = {}
    try:
        for c in json.load(open(CATALOGO_SALIDA)).get("circuitos", []):
            prev_circuitos[c["codigo"]] = c.get("calles", "")
    except Exception:
        pass
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import correcciones  # noqa: E402
    falsos = set(correcciones.circuitos_falsos())  # 'L2' = calle L, no circuito
    # extracciones LLM por post (partes_llm.py): el LLM entiende redacciones que
    # los regex no ("se afectó", "T44 y T41", "👉🏼"...). Se aplican DESPUÉS del
    # regex en cada post (mismo orden cronológico), así el catálogo se corrige
    # aunque el regex no haya entendido el parte.
    try:
        llm_cache = json.load(open(os.path.join(RAIZ, "data", "partes_llm.json")))
    except Exception:
        llm_cache = {}

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
            for cod in re.split(_SEP, m.group(1)):
                cod = cod.strip().upper()
                if not RE_UN_CODIGO.match(cod):
                    continue
                cod = canonico(cod)  # alias aprendido ('581') -> registro canónico
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
                    "ultima_message_id": f["message_id"],
                    "estado": None, "estado_fecha": None,
                })
                r["veces"] += 1
                r["ultima"] = fecha
                r["ultima_message_id"] = f["message_id"]
                _adoptar_calles(r, calles, texto)
                if bloque is not None:
                    r["bloque"] = bloque          # último bloque conocido gana
                if muni:
                    r["municipio"] = muni
                if causa:
                    r["causa"] = causa
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
                if not RE_UN_CODIGO.match(cod):
                    continue
                cod = canonico(cod)  # alias aprendido -> canónico (sin gemelos)
                if cod in falsos:
                    continue
                # municipio entre paréntesis (formato UNE jul/2026): dato directo
                mun_cod = (municipios_en(mun_p) or [None])[0] if mun_p else None
                _pre = re.match(r"^[A-Z]+", cod)
                r = cat.setdefault(cod, {
                    "codigo": cod, "prefijo": _pre.group(0) if _pre else "",
                    "calles": "", "municipio": None, "bloque": None, "causa": "déficit de generación",
                    "veces": 0, "primera": fecha, "ultima": fecha,
                    "ultima_message_id": f["message_id"],
                    "estado": None, "estado_fecha": None,
                })
                r["veces"] += 1
                r["ultima"] = fecha
                r["ultima_message_id"] = f["message_id"]
                r["estado"] = "sin servicio"
                r["estado_fecha"] = fecha
                if mun_cod and not r["municipio"]:
                    r["municipio"] = mun_cod

        # --- Extracción LLM de ESTE post (si existe): complementa/corrige ---
        v = llm_cache.get(str(f.get("message_id")))
        # Las cachés anteriores a v2 no registran evidencia del código original;
        # no deben modificar ni estado ni metadatos hasta ser revalidadas.
        if usar_extraccion_llm(v):
            dudosos = set(v.get("por_confirmar") or [])
            codes_estado = set()
            for item in v.get("circuitos") or []:
                codes_estado |= {canonico(x) for x in (item.get("codigos_estado") or [])}
            for item in v.get("circuitos") or []:
                for cod in item.get("codigos") or []:
                    cod = canonico(cod)  # '581' -> SF581: un solo registro, sin gemelo
                    # Un código que el aprendiz ya registró (directo o por alias)
                    # dejó de ser dudoso aunque el caché viejo lo marcara: se
                    # re-valida contra es_conocido, no contra la lista congelada.
                    if cod in falsos or not RE_UN_CODIGO.match(cod):
                        continue
                    if cod in dudosos and not es_conocido(cod):
                        continue
                    _pre = re.match(r"^[A-Z]+", cod)
                    r = cat.setdefault(cod, {
                        "codigo": cod, "prefijo": _pre.group(0) if _pre else "",
                        "calles": "", "municipio": None, "bloque": None, "causa": None,
                        "veces": 0, "primera": fecha, "ultima": fecha,
                        "ultima_message_id": f["message_id"],
                        "estado": None, "estado_fecha": None,
                    })
                    if (r["ultima"] or "") <= fecha:
                        r["ultima"] = fecha
                        r["ultima_message_id"] = f["message_id"]
                    # El texto degenerado del LLM no se adopta ni aunque sea el
                    # más largo: la defensa en profundidad vive en _adoptar_calles.
                    _adoptar_calles(r, item.get("calles"), texto)
                    if item.get("municipio") and not r["municipio"]:
                        r["municipio"] = (municipios_en(item["municipio"]) or [None])[0]
                    # Solo un código escrito explícitamente en el parte puede
                    # cambiar estado; casar un nombre por calles es auxiliar.
                    if cod in codes_estado and \
                            item.get("estado") and (r["estado_fecha"] or "") <= fecha:
                        r["estado"] = item["estado"]
                        r["estado_fecha"] = fecha

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
                            "ultima_message_id": None,
                            "estado": None, "estado_fecha": None}
        r["oficial"] = True
        r["municipios"] = info.get("municipios") or []  # lista oficial (puede ser >1: feeders de frontera)
        calles_of = " · ".join(v for v in (info.get("calles") or {}).values() if v)
        if calles_of and not r["calles"]:
            r["calles"] = calles_of  # respaldo: solo si Telegram no trajo nada

    # --- Semilla APRENDIDA (aprende_circuitos.py): códigos que la UNE no tabla
    # pero los partes repiten. Entran como registros base —las calles las
    # aportan sus propios partes—, SIN autoridad de municipio (no la inventamos:
    # el pipeline las ubica por geocodificación y point-in-polygon). Los alias
    # no se siembran: su evidencia ya se enruta al canónico arriba.
    try:
        aprendidos = json.load(open(os.path.join(RAIZ, "data", "circuitos_aprendidos.json")))
    except Exception:
        aprendidos = {}
    for cod, info in aprendidos.items():
        if info.get("alias_de") or cod in falsos:
            continue
        r = cat.get(cod)
        if not r:
            _pre = re.match(r"^[A-Z]+", cod)
            r = cat[cod] = {"codigo": cod, "prefijo": _pre.group(0) if _pre else "",
                            "calles": "", "municipio": None, "bloque": None, "causa": None,
                            "veces": 0, "primera": None, "ultima": None,
                            "ultima_message_id": None,
                            "estado": None, "estado_fecha": None}
        r["aprendido"] = True
        if info.get("calles") and not r["calles"]:
            r["calles"] = info["calles"]  # respaldo: Telegram/LLM no trajeron nada

    circuitos = sorted(cat.values(), key=lambda r: r["ultima"] or "", reverse=True)

    # --- Detección de cambios de dirección ---
    # Compara el catálogo final contra el de la corrida anterior. Si la cobertura
    # de tokens de calles es < 0.25, el circuito se mudó de zona.
    try:
        prev_cambios = json.load(open(CAMBIOS_FILE))
    except Exception:
        prev_cambios = []
    ahora_c = datetime.now(timezone.utc)
    for c in circuitos:
        base = prev_circuitos.get(c["codigo"])
        if base and c.get("calles") and _cobertura(c["calles"], base) < 0.25:
            # ya reportado con el mismo 'ahora'? no duplicar
            ya = next((pc for pc in prev_cambios if pc["codigo"] == c["codigo"]
                       and pc.get("ahora") == c["calles"]), None)
            if not ya:
                prev_cambios = [pc for pc in prev_cambios if pc["codigo"] != c["codigo"]]
                prev_cambios.insert(0, {
                    "codigo": c["codigo"], "antes": base, "ahora": c["calles"],
                    "solapamiento": round(_cobertura(c["calles"], base), 2),
                    "detectado": ahora_c.isoformat(),
                })
    # podar > 7 días
    corte_cambios = (ahora_c - timedelta(days=7)).isoformat()
    prev_cambios = [c for c in prev_cambios if c.get("detectado", "") >= corte_cambios]
    json.dump(prev_cambios, open(CAMBIOS_FILE, "w"), ensure_ascii=False)
    codigos_cambiados = {c["codigo"] for c in prev_cambios
                         if c.get("detectado", "") >= (ahora_c - timedelta(days=1)).isoformat()}

    # --- Conteo de usuario (discrepancia UNE vs vecinos) ---
    # Lee el conteo producido por estado.py. Cuando la UNE dice "con servicio",
    # resetea el contador de usuario (ultima_reset persiste para que estado.py
    # ignore los "sin" reportes anteriores al reset).
    try:
        conteo_usuario = json.load(open(CONTEO_USUARIO_FILE))
    except Exception:
        conteo_usuario = {}
    for c in circuitos:
        cu = conteo_usuario.get(c["codigo"])
        if not cu:
            c["discrepado"] = False
            continue
        if c.get("estado") == "con servicio" and cu.get("desde"):
            cu["desde"] = None
            cu["ultimo_reset"] = ahora_c.isoformat()
            cu.pop("horas", None)
        # discrepancia: usuario dice sin (desde not null) y UNE dice con (o nada)
        c["discrepado"] = bool(cu.get("desde") and c.get("estado") in ("con servicio", None))
        c["conteo_usuario"] = cu
    # persistir los resets de UNE para que estado.py los respete
    json.dump(conteo_usuario, open(CONTEO_USUARIO_FILE, "w"), ensure_ascii=False)

    # Récord de circuitos apagados A LA VEZ: contamos cuántos están sin servicio
    # AHORA (no los ~10 que muestra un parte) y guardamos el pico histórico.
    # Los discrepados no suman: son una categoría separada (cuenta como con).
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

    # Rotación semanal DAF oficial: sustituye la vieja bandera histórica ("alguna
    # vez apareció en un disparo") por la lista vigente del parte de la UNE.
    daf_oficial = extraer_daf_oficial(filas)
    daf_vigentes = set(daf_oficial["circuitos"]) \
        if daf_oficial and daf_oficial["vigente"] else set()
    for c in circuitos:
        c.pop("daf", None)
        if c["codigo"] in daf_vigentes:
            c["daf"] = True

    # Fase 2b: geometría de las CALLES reales (OSM/Overpass) de cada circuito, para
    # dibujarlas en el mapa. Cacheada por código (la geometría no cambia) y acotada
    # por corrida (Overpass es lento). Los que no resuelven quedan sin líneas (el
    # frontend cae a una bolita grande que engloba la zona).
    from build_lineas import (overpass as _overpass, overpass_lugares as _lugares,
                              norm as _norm)  # noqa: E402
    cache_l = json.load(open(CACHE_LINEAS)) if os.path.exists(CACHE_LINEAS) else {}
    # Cuántas veces se intentó resolver cada código. Antes, un [] en la caché
    # era definitivo: un timeout de Overpass condenaba al circuito a no tener
    # geometría nunca. Se reintenta hasta REINTENTOS veces y luego se deja.
    intentos = json.load(open(CACHE_INTENTOS)) if os.path.exists(CACHE_INTENTOS) else {}

    # Purgar caché de líneas para circuitos que cambiaron de dirección: las
    # líneas viejas corresponden a la ubicación anterior y hay que re-resolverlas.
    for cod in codigos_cambiados:
        cache_l.pop(cod, None)
        intentos.pop(cod, None)

    # Barrios con polígono que ya están en el repo: para un circuito descrito
    # por reparto, la geometría correcta ya la tenemos y no hace falta red.
    try:
        barrios_poly = {_norm(k): v for k, v in
                        json.load(open(os.path.join(RAIZ, "web", "data",
                                                    "barrios_poligonos.json"))).items()}
    except Exception:
        barrios_poly = {}

    # _STOP y _nombres_calles viven ahora en evidencia_calles.py (los consume
    # también el gazetteer de circuitos): mismo troceo, cero duplicación.
    def _geoms(elementos, nset):
        return [[[round(p["lon"], 5), round(p["lat"], 5)] for p in e["geometry"]]
                for e in elementos
                if e.get("geometry") and _norm(e.get("tags", {}).get("name", "")) in nset]

    # OJO: agotar el presupuesto NO puede cortar el bucle. Con un `break` aquí,
    # los circuitos que vienen después se quedan sin sus líneas YA CACHEADAS y
    # el mapa pierde trazos que no costaban una sola consulta. Pasó en
    # producción: 22 trazos publicados frente a 129 en la caché, porque Overpass
    # responde lento desde los runners y el corte saltaba enseguida.
    nuevas, inicio_geo, sin_presupuesto = 0, time.monotonic(), False
    for c in circuitos:
        cod = c["codigo"]
        if cache_l.get(cod):                 # ya resuelto: se reutiliza
            c["lineas"] = cache_l[cod]
            continue
        if sin_presupuesto:                  # se sigue recorriendo, sin resolver
            continue
        if intentos.get(cod, 0) >= REINTENTOS:
            continue
        if "lat" not in c or not c.get("calles") or nuevas >= MAX_GEO:
            continue
        if time.monotonic() - inicio_geo > MAX_SEGUNDOS_GEO:
            sin_presupuesto = True
            continue
        nombres = _nombres_calles(c["calles"])
        if not nombres:
            intentos[cod] = REINTENTOS      # sin nombres no hay nada que reintentar
            continue
        nset = set(nombres)

        # 1) Polígono de barrio que ya tenemos en casa: gratis y sin red.
        lineas = []
        for n in nombres:
            entrada = barrios_poly.get(n) or {}
            anillo = entrada.get("anillo") if isinstance(entrada, dict) else None
            if anillo:
                lineas.append([[round(p[0], 5), round(p[1], 5)] for p in anillo])
                break

        # 2) Calles reales en OSM.
        if not lineas:
            caja = (c["lat"] - 0.018, c["lon"] - 0.022,
                    c["lat"] + 0.018, c["lon"] + 0.022)  # s,w,n,e
            try:
                lineas = _geoms(_overpass(nombres, caja), nset)
            except Exception:
                lineas = []
            # 3) Si ninguna casa como vía, puede ser un reparto: se busca como lugar.
            if not lineas:
                try:
                    lineas = _geoms(_lugares(nombres, caja), nset)
                except Exception:
                    lineas = []

        cache_l[cod] = lineas
        intentos[cod] = intentos.get(cod, 0) + 1
        if lineas:
            c["lineas"] = lineas
        nuevas += 1
    json.dump(cache_l, open(CACHE_LINEAS, "w"), ensure_ascii=False)
    json.dump(intentos, open(CACHE_INTENTOS, "w"), ensure_ascii=False)
    con_lineas = sum(1 for c in circuitos if c.get("lineas"))

    salida = {
        "generado": datetime.now(timezone.utc).isoformat(),
        "total": len(circuitos),
        "record_apagados": record,   # pico histórico de circuitos sin servicio a la vez
        "daf_oficial": daf_oficial,
        "circuitos": circuitos,
    }
    destino = CATALOGO_SALIDA
    json.dump(salida, open(destino, "w"), ensure_ascii=False)
    discrepados = sum(1 for c in circuitos if c.get("discrepado"))
    print(f"circuitos.json: {len(circuitos)} circuitos, {ubicados} geolocalizados, "
          f"{con_lineas} con calles dibujadas, {discrepados} discrepados, "
          f"{len(prev_cambios)} cambios de dirección ({os.path.getsize(destino) // 1024} KB)")


if __name__ == "__main__":
    main()
