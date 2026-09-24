"""Construye web/data/circuitos.json: catálogo APRENDIDO de circuitos de la red a
partir de los códigos oficiales que la Empresa pone en sus partes (ej. "A1443-
Rio Verde…", "PG940- Comodoro…"). El código es el prefijo de la subestación +
número del circuito; es un identificador estable de un tramo físico de red que
alimenta un conjunto fijo de calles.

No acumula un fichero propio: reconstruye desde el histórico del canal en
data/canal_cache.json (commiteado). Ese caché se llena INCREMENTALMENTE desde
Supabase (solo mensajes nuevos por message_id + una ventana reciente para
recoger partes editados por la Empresa), así que la corrida ya no re-lee las
~12 mil filas del canal en cada paso. Sin Supabase (corrida local o red
caída) el replay sigue en pie con el caché commiteado; solo se pierden los
mensajes nuevos de la ventana incremental. Para cada código guarda las calles
que sirve, el municipio, el bloque en que rota (inferido del post), cuántas
veces se ha visto, cuándo, y su último estado conocido (con/sin servicio).
El reloj del estado es `estado_desde` (fecha del parte que CAMBIÓ el estado);
`estado_fecha` NO cambia de significado: sigue siendo la última MENCIÓN de
estado (lo consumen build_seo, build_serie24h, coherencia_catalogo, bot y el
worker como «última mención»).

La dirección (`calles`) de cada circuito se decide por CONSENSO CORROBORADO del
canal —con peso por recencia (vida media 45 días) e histéresis—, no por la
última mención: la dirección vigente no cae por una mención suelta ni por un
error de la fuente (un parte con el código cruzado), y solo la reemplaza un
racimo dominante (>=60% del peso y >=3 menciones) o una variante de la misma
zona. La tabla oficial de la UNE NO es verdad absoluta (los circuitos cambiaron
desde su publicación): es el arranque en frío y el árbitro del detector de
cruce. Los casos irreducibles quedan marcados en `direccion_en_revision` para
revisión manual.

Sobre el MISMO replay emite web/data/circuitos_horas.json: el histórico
COMPLETO de horas sin corriente CONFIRMADAS por circuito. Cada vez que un
mensaje (vía regex o vía caché LLM) actualiza r["estado"]/r["estado_fecha"]
se anota el evento del circuito (fecha, sin/con); el acumulador empareja
afectación→restablecimiento y reparte cada intervalo por día calendario en
HORA_CUBA (UTC-4 fijo, con split de medianoche). REGLA del mantenedor
(coherente con el ciclo de vida del estado): las horas son HORAS
CONFIRMADAS — un intervalo CERRADO con restablecimiento cuenta completo de
punta a punta (la UNE confirmó ambos extremos), mientras que el que siga
abierto al final solo cuenta hasta la APERTURA + 216 h (el umbral azul): el
reloj del silencio se ancla en el CAMBIO de estado y solo lo resetea un parte
CONTRARIO, así que una re-mención "sin" o una señal vecinal "sin" posterior
que coincide con el apagón NO lo extiende. Fin efectivo
min(generado, apertura + 216 h). `generado` viene de
web/data/estado.json, que estado.py escribe antes en CI; respaldo: el
timestamp del último mensaje del canal. Días/circuitos sin horas:
ausentes, nunca 0; redondeo a 1 decimal por día. Alimenta el ranking
"Circuitos más afectados" de las páginas de municipio (build_seo.py).
"""

import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, time as dtime, timezone
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
HORAS_JSON = os.path.join(RAIZ, "web", "data", "circuitos_horas.json")
CANAL_CACHE = os.path.join(RAIZ, "data", "canal_cache.json")
# Huso fijo para el reparto por día calendario del histórico de horas: el mismo
# respaldo que build_seo.py (desde 2026 La Habana no atrasa el reloj).
HORA_CUBA = timezone(timedelta(hours=-4))
# Horas que cada mención del circuito CONFIRMA del histórico de horas (regla
# del mantenedor: las horas sin corriente son HORAS CONFIRMADAS). Mismo valor
# que _UMBRAL_DESC_H de build_seo.py —el umbral que deja el estado en
# "desconocido"—: si el estado deja de afirmar a las 48 h de silencio, las
# horas tampoco pueden seguir contando. Solo afecta al tramo ABIERTO (el
# cerrado con restablecimiento cuenta completo).
# Las horas cuentan mientras el circuito está EFECTIVAMENTE caído: se
# detienen al asumirlo con corriente (umbral azul, mismo valor que
# _UMBRAL_AZUL_H de build_seo.py: 48 h + 7 días) o ante una señal vecinal
# de retorno. Docstring de horas_historicas para la regla completa.
_UMBRAL_HORAS_AZUL_H = 216.0
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


# --- Decisión de dirección por CONSENSO corroborado del canal (en vez de
# "gana la última mención") ---. Los parámetros salen de medir el caché local
# (2.5 meses, ~27k menciones, 206 circuitos con >=30): 154 con racimo dominante
# >=90% (dirección clara), solo 3 cambios reales y sostenidos, y 41 pares de
# cruce. La tabla oficial NO pisa decisiones: es arranque en frío y árbitro.
VIDA_MEDIA_DIRECCION_D = 45.0      # vida media del peso por recencia (días)
_UMBRAL_MISMO_RACIMO = 0.6         # dos textos son el mismo racimo
_UMBRAL_MISMA_DIRECCION = 0.35     # el texto nuevo es la misma zona que la vigente
_UMBRAL_SHARE_CAMBIO = 0.60        # un cambio real necesita este respaldo
_MIN_MENCIONES_CAMBIO = 3          # y al menos estas menciones
_UMBRAL_CRUCE = 0.7                # el texto es la dirección de OTRO circuito


def _reportado_con(c, cu):
    """Dirección 2 del reporte vecinal (espejo del flag `discrepado`): los
    vecinos reportan que VOLVIÓ la corriente y el catálogo sigue "sin
    servicio". True cuando el circuito está "sin servicio", existe
    `ultimo_con` POSTERIOR a `estado_fecha` (el "volvió" es posterior a la
    caída que declara la UNE) y el veredicto vecinal VIGENTE es "con": no hay
    reporte "se fue" (`desde`) posterior al último "volvió". El ÚLTIMO
    veredicto manda — ultimo_con NO se limpia con reportes "sin" posteriores,
    así que sin el guard un flip-flop (caída T1, "volvió" T2, "se fue" T3 >
    T2) mostraría "con servicio (según vecinos)" en falso. Sin umbral de
    vecinos (1 reporte basta, igual que discrepado con `desde`) y sin
    ventana de recencia: la frescura la fija la propia comparación contra
    estado_fecha — anclarla a la fecha de la caída es lo que evita
    resucitar reportes viejos. Colisión con discrepado: imposible
    por construcción (discrepado exige estado "con servicio"/None; aquí exige
    "sin servicio"); si algún día colisionara, discrepado gana (ya tiene
    prioridad de rama en los tres clientes JS). Determinista: compara
    timestamps de los datos, nunca el reloj de la corrida. NUNCA lanza:
    cualquier excepción de comparación (timestamps no-string, p. ej. un
    epoch crudo) degrada a False — conservador: sin reportado_con."""
    if not (c.get("estado") == "sin servicio" and cu and cu.get("ultimo_con")
            and c.get("estado_fecha")):
        return False
    a, b = cu["ultimo_con"], c["estado_fecha"]
    try:  # TODO el razonamiento temporal está envuelto: NUNCA lanza de verdad
        desde_dt = datetime.fromisoformat(cu["desde"]) if cu.get("desde") else None
        volvio_dt = datetime.fromisoformat(a)
        caida_dt = datetime.fromisoformat(b)
    except (TypeError, ValueError):  # formato raro: comparación léxica
        try:
            return (not cu.get("desde") or cu["desde"] < a) and a > b
        except Exception:  # tipos mixtos: False, sin abortar el build
            return False
    return (not desde_dt or desde_dt < volvio_dt) and volvio_dt > caida_dt


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


def _peso_recencia(fecha_iso, generado):
    """Peso de una mención por antigüedad: 0.5 ** (días / 45).

    `generado` es el horizonte del build (datetime). Sin horizonte verificable
    o con fecha irrecuperable la mención cuenta como reciente (1.0): no se
    castiga un dato por no poder fecharlo.
    """
    if generado is None:
        return 1.0
    fd = _fecha_dt(fecha_iso)
    if fd is None:
        return 1.0
    try:
        dias = max(0, (generado - fd).days)
    except TypeError:  # tz-aware contra naive: sin comparación posible
        return 1.0
    return 0.5 ** (dias / VIDA_MEDIA_DIRECCION_D)


def _racimos(votables, generado):
    """Agrupa los votos por variante de escritura (racimos) y los pondera.

    Recorrido greedy en orden de llegada: un voto entra al primer racimo cuyo
    representante solapa >= _UMBRAL_MISMO_RACIMO (mismo lugar escrito distinto:
    tildes, mayúsculas, prefijos). El representante es el texto MÁS LARGO del
    racimo (la variante más completa gana). Devuelve la lista
    [{"texto", "n", "peso"}, ...] ordenada por peso descendente.
    """
    racs = []
    for v in votables:
        texto = (v.get("texto") or "").strip()
        if not texto:
            continue
        peso = _peso_recencia(v.get("fecha"), generado)
        rac = next((r for r in racs
                    if _cobertura(texto, r["texto"]) >= _UMBRAL_MISMO_RACIMO), None)
        if rac is None:
            racs.append({"texto": texto, "n": 1, "peso": peso})
        else:
            rac["n"] += 1
            rac["peso"] += peso
            if len(texto) > len(rac["texto"]):
                rac["texto"] = texto
    racs.sort(key=lambda r: r["peso"], reverse=True)
    return racs


def _mejor_ajena(texto, cod, referencias):
    """La dirección de OTRO circuito que mejor solapa con `texto`.

    `referencias` es {codigo: direccion} (dirección publicada en la corrida
    anterior + tabla oficial). Devuelve (codigo_ajeno | None, cobertura): el
    detector de cruce lo usa para saber si el texto ganador es, en realidad,
    la dirección de un circuito distinto.
    """
    mejor, mejor_cob = None, 0.0
    for otro, dir_otro in (referencias or {}).items():
        if otro == cod or not dir_otro:
            continue
        cob = _cobertura(texto, dir_otro)
        if cob > mejor_cob:
            mejor, mejor_cob = otro, cob
    return mejor, mejor_cob


def resolver_direcciones(cat, votos, previos, generado, oficial):
    """Decide la dirección de cada circuito por consenso corroborado del canal.

    Muta `cat` in place (calles, direccion_confianza, direccion_menciones y,
    cuando hay problema, direccion_en_revision/direccion_de) y devuelve la
    lista de casos para revisión manual, ordenada por menciones descendente.

    Histéresis deliberada: la dirección vigente solo cae si el racimo nuevo
    solapa con ella (misma zona: gana la variante más votada) o si es un
    cambio real y sostenido (share >= 0.60 y n >= 3). Una mención compartida
    (un bloque de texto repartido entre varios códigos) no vota, salvo que el
    circuito no tenga ningún otro voto. El detector de cruce usa como
    referencias la tabla oficial y la dirección publicada en la corrida
    anterior —NUNCA los candidatos de esta corrida: dos circuitos no pueden
    validarse mutuamente—; si el ganador es la dirección de otro circuito se
    busca el mejor racimo propio no cruzado y se marca. Un código sin votos
    queda con su `calles` tal cual: cae al respaldo oficial/aprendido y a
    `_adoptar_calles`.
    """
    referencias = {}
    for cod_of, info in (oficial or {}).items():
        calles_of = " · ".join(v for v in (info.get("calles") or {}).values() if v)
        if calles_of:
            referencias[cod_of] = calles_of
    referencias.update(previos or {})  # lo publicado manda sobre el PDF

    revision = []
    for cod, r in cat.items():
        vs = (votos or {}).get(cod) or []
        if not vs:
            continue
        votables = [v for v in vs if not v.get("compartida")] or vs
        racs = _racimos(votables, generado)
        if not racs:
            continue
        total = sum(x["peso"] for x in racs) or 1.0
        top = racs[0]
        share = top["peso"] / total
        vigente = (previos or {}).get(cod) or ""
        motivo, ajeno = None, None

        def pasa_puerta(cand):
            # Misma zona que la vigente o cambio real y sostenido.
            if not vigente:
                return True
            if _cobertura(cand["texto"], vigente) >= _UMBRAL_MISMA_DIRECCION:
                return True
            return (cand["peso"] / total >= _UMBRAL_SHARE_CAMBIO
                    and cand["n"] >= _MIN_MENCIONES_CAMBIO)

        def cruce(cand):
            # Devuelve el código ajeno si `cand` es la dirección de OTRO
            # circuito (y no es la misma zona que la vigente).
            otro, cob = _mejor_ajena(cand["texto"], cod, referencias)
            if (cob >= _UMBRAL_CRUCE
                    and _cobertura(cand["texto"], vigente) < _UMBRAL_MISMA_DIRECCION):
                return otro
            return None

        cand = top if pasa_puerta(top) else None
        if cand is None:
            motivo = "disputada"
        else:
            de = cruce(cand)
            if de:
                motivo, ajeno = "cruzada", de
                cand = next((x for x in racs
                             if not cruce(x) and pasa_puerta(x)), None)

        r["direccion_confianza"] = round(share, 2)
        r["direccion_menciones"] = top["n"]
        if cand is not None:
            r["calles"] = cand["texto"]
        elif vigente:
            # Sin candidato propio se conserva la VIGENTE (histéresis real):
            # el replay pudo dejar en `calles` la última mención —p. ej. el
            # texto cruzado—, y publicarla sería justo lo que este cambio
            # evita. No pisar la vigente solo vale cuando no hay vigente.
            r["calles"] = vigente
        if motivo:
            r["direccion_en_revision"] = motivo
            if motivo == "cruzada":
                r["direccion_de"] = ajeno
            revision.append({
                "codigo": cod, "publicada": r["calles"],
                "evidencia": top["texto"], "motivo": motivo, "de": ajeno,
                "menciones": top["n"], "confianza": round(share, 2),
            })
    revision.sort(key=lambda e: e["menciones"], reverse=True)
    return revision


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


# --- Histórico de horas sin corriente por circuito (circuitos_horas.json) ---
# El acumulador vive AQUÍ, en el replay del canal: la historia COMPLETA está en
# canal_cache.json (14k+ mensajes commiteados, replayados cronológicamente con
# regex y caché LLM), no en la ventana de ~7 días de web/data/partes.json.

def _fecha_dt(iso):
    """datetime desde el ISO de un mensaje o de estado.json; None si es
    irrecuperable (nunca lanza: el histórico no puede tumbar el build)."""
    try:
        return datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


def _anotar_horas(eventos, codigos, fecha, estado):
    """Anota el evento de estado de un mensaje en el histórico de horas.

    Se llama EXACTAMENTE donde el replay actualiza r["estado"] /
    r["estado_fecha"] (camino regex y camino LLM): el histórico replica el
    flujo autoritativo del catálogo, no un camino paralelo que pueda
    divergir. `estado` llega tal cual lo escribe el catálogo ("sin servicio" /
    "con servicio"); un estado nulo no anota nada y una fecha irrecuperable se
    descarta (no hay dónde ponerla en la línea de tiempo).
    """
    if not estado:
        return
    dt = _fecha_dt(fecha)
    if dt is None:
        return
    st = "sin" if estado == "sin servicio" else "con"
    for cod in codigos:
        eventos.setdefault(cod, []).append((dt, st))


def _a_utc(dt):
    """Normaliza a tz-aware en UTC; naive se interpreta UTC (misma convención
    que build_seo: las fechas de los datos viajan con offset explícito)."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _reparto_dias(desde, hasta):
    """{YYYY-MM-DD (HORA_CUBA): horas} del intervalo [desde, hasta).

    Un intervalo que cruza medianoche se reparte entre los días calendario que
    toca (huso fijo −4: sin DST, la aritmética conserva la duración). Un
    intervalo vacío o invertido no aporta nada.
    """
    desde4, hasta4 = _a_utc(desde).astimezone(HORA_CUBA), _a_utc(hasta).astimezone(HORA_CUBA)
    if hasta4 <= desde4:
        return {}
    dias = {}
    cursor = desde4
    while cursor < hasta4:
        dia = cursor.date()
        medianoche = datetime.combine(dia + timedelta(days=1), dtime.min,
                                      tzinfo=HORA_CUBA)
        trozo = min(medianoche, hasta4)
        clave = dia.isoformat()
        dias[clave] = dias.get(clave, 0.0) + (trozo - cursor).total_seconds() / 3600.0
        cursor = trozo
    return dias


def horas_historicas(eventos, generado, senales=None, senales_con=None):
    """Histórico de horas sin corriente por circuito (sin redondear).

    Empareja los eventos del replay por circuito: una afectación abre el
    intervalo "sin" (timestamp del mensaje) y un restablecimiento lo cierra;
    varios ciclos acumulan. REGLA DEL MANTENEDOR (coherente con el ciclo de
    vida del estado: apagado sigue apagado hasta asumirlo con corriente):

    - Las horas cuentan mientras el circuito está EFECTIVAMENTE caído: desde
      la apertura y a través del estado "desconocido", hasta el umbral azul
      (_UMBRAL_HORAS_AZUL_H desde la APERTURA — llegar a azul significa
      "asumimos con corriente" y ahí se detienen). El tope se ancla en el
      CAMBIO de estado: una re-mención de la UNE o una señal vecinal "sin"
      que COINCIDE con el apagón no resetea ni extiende el reloj.
    - El parámetro `senales` ({codigo: [datetime]} con las señales vecinales
      "sin") se conserva en la firma por compatibilidad (lo pasa main()),
      pero ya NO participa del tope: una señal que coincide no extiende el
      reloj. Nunca participó de la apertura/cierre del tramo.
    - Una señal vecinal de RETORNO (`senales_con`: {codigo: [datetime]} con
      ultimo_con del conteo_usuario) DETIENE las horas en ese punto: los
      vecinos dicen que volvió y después no sabemos. Si la UNE volvió a
      declarar "sin" DESPUÉS de esa señal, la señal quedó desautorizada y
      el conteo sigue (la caída re-confirmada manda).
    - Un intervalo CERRADO con restablecimiento cuenta COMPLETO de punta a
      punta (la UNE confirmó ambos extremos del episodio) — salvo que haya
      señal vecinal de retorno DENTRO del episodio y sin "sin" posterior:
      el tramo se corta en esa señal.
    - `generado` None: nada abierto queda contable (sin horizonte
      verificable no se inventa).

    Devuelve {codigo: {"por_dia": {fecha: horas}, "total": horas}} en horas
    exactas; el reparto por día es calendario HORA_CUBA (UTC-4 fijo). Un
    circuito sin intervalos "sin" no aparece.
    """
    fin = _a_utc(generado) if generado is not None else None
    historico = {}
    for codigo, evs in eventos.items():
        por_dia = {}
        abierto = None
        re_menciones = []  # "sin" posteriores a la apertura del tramo abierto
        ult_con = None     # señal vecinal de retorno (ultimo_con), si cae en el tramo
        con_sig = sorted(_a_utc(s) for s in (senales_con or {}).get(codigo, []))
        for dt, estado in sorted(evs, key=lambda x: x[0]):  # replay ya asc; sort defensivo
            dt = _a_utc(dt)
            if estado == "sin":
                if abierto is None:  # dos "sin" seguidos: manda el primero
                    abierto = dt
                else:
                    re_menciones.append(dt)  # re-mención: resetea el reloj
            elif abierto is not None:
                # Cerrado con restablecimiento: cuenta COMPLETO de punta a
                # punta, salvo señal vecinal de retorno dentro del episodio
                # sin "sin" posterior (el episodio quedó en duda).
                corte = dt
                con_dentro = [s for s in con_sig
                              if abierto < s < dt
                              and not (re_menciones and re_menciones[-1] > s)]
                if ult_con and abierto < ult_con < dt:
                    con_dentro.append(ult_con)
                if con_dentro:
                    corte = min(min(con_dentro), dt)
                for dia, h in _reparto_dias(abierto, corte).items():
                    por_dia[dia] = por_dia.get(dia, 0.0) + h
                abierto = None
                re_menciones = []
                ult_con = None
        # señales vecinales que caen en el tramo abierto (cargadas aparte)
        if abierto is not None and fin is not None:
            for s in con_sig:
                if abierto < s <= fin and not (re_menciones and re_menciones[-1] > s):
                    ult_con = s
                    break
        if abierto is not None and fin is not None:
            # Tramo abierto al horizonte: cuenta hasta la señal vecinal de
            # retorno si llegó antes y sigue vigente, o hasta el umbral azul
            # medido desde la APERTURA del tramo — que ES el cambio de estado.
            # Una noticia que COINCIDE (una re-mención "sin" de la UNE o una
            # señal vecinal "sin") NO extiende el tope: el reloj del silencio
            # solo lo resetea un parte contrario. `senales` se conserva en la
            # firma porque la pasa main(), pero no participa del tope.
            azul_cap = abierto + timedelta(hours=_UMBRAL_HORAS_AZUL_H)
            hasta = min(fin, azul_cap)
            if ult_con and ult_con <= hasta:
                hasta = ult_con
            for dia, h in _reparto_dias(abierto, hasta).items():
                por_dia[dia] = por_dia.get(dia, 0.0) + h
        if por_dia:
            historico[codigo] = {"por_dia": por_dia, "total": sum(por_dia.values())}
    return historico


def redondear_horas(historico):
    """Redondeo a 1 decimal POR DÍA; los días que quedan en 0.0 y los
    circuitos sin horas publicables se omiten (ausencia ≠ 0). Devuelve el par
    {"total": {codigo: h}, "por_dia": {codigo: {fecha: h}}} del JSON final,
    con los códigos ordenados para diffs estables.
    """
    total, por_dia = {}, {}
    for codigo in sorted(historico):
        dias = {}
        for fecha, h in historico[codigo]["por_dia"].items():
            r = round(h, 1)
            if r > 0:
                dias[fecha] = r
        if not dias:
            continue
        por_dia[codigo] = dias
        total[codigo] = round(historico[codigo]["total"], 1)
    return {"total": total, "por_dia": por_dia}


def _generado_horas(filas):
    """Horizonte del histórico de horas (datetime): el `generado` de
    web/data/estado.json —estado.py corre antes en CI y es el reloj del resto
    del sitio—; si el archivo no está o no trae fecha, el timestamp del último
    mensaje del canal (filas en orden ascendente). None si no hay nada: sin
    horizonte verificable, ningún intervalo abierto se cierra por su cuenta.
    """
    try:
        with open(ESTADO_FILE, encoding="utf-8") as f:
            dt = _fecha_dt(json.load(f).get("generado"))
        if dt is not None:
            return dt
    except Exception:
        pass
    for f in reversed(filas):  # ascendentes: el último con fecha válida
        dt = _fecha_dt(f.get("fecha"))
        if dt is not None:
            return dt
    return None


def replay_canal(filas, oficial, falsos, llm_cache, votos=None):
    """Replay cronológico del canal: reconstruye el catálogo (en orden
    cronológico gana el último) y anota, EN EL MISMO PASO, los eventos de
    estado del histórico de horas. `filas` llega ascendente (cargar_canal);
    `oficial`, `falsos` y `llm_cache` son los insumos que main() ya cargó.

    `votos` (opcional) es el acumulador de menciones para decidir la dirección
    por consenso: {codigo: [{"fecha", "texto", "compartida"}, ...]}. Se anota
    ADEMÁS del `_adoptar_calles` actual (que queda como respaldo), sin tocar
    la lógica de estado/horas ni el retorno.

    Cada registro acumula DOS fechas de estado: `estado_fecha` (última MENCIÓN
    de estado, sin cambiar su significado) y `estado_desde` (fecha del parte
    que CAMBIÓ el estado). Un parte que coincide con el estado vigente (otro
    "sin" estando ya en apagón) actualiza `estado_fecha` pero NO `estado_desde`:
    es el reloj del silencio que consumen el sitio y las horas.

    Devuelve (cat, eventos_horas): cat es {codigo: registro} y eventos_horas
    es {codigo: [(datetime, "sin"|"con"), ...]} en orden cronológico, con UN
    evento por cada actualización de r["estado"] (regex o LLM). Empieza de
    cero en cada corrida: el catálogo y las horas no arrastran estado.
    """
    if votos is None:
        votos = {}
    cat = {}  # codigo -> registro acumulado (en orden cronológico gana el último)
    eventos_horas = {}
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
            # Los códigos se calculan UNA vez: si el parte lista varios, ese
            # bloque de texto se reparte idéntico a todos y NO es evidencia por
            # circuito (mención compartida).
            codigos = [c.strip().upper() for c in re.split(_SEP, m.group(1))]
            compartida = len(codigos) > 1
            for cod in codigos:
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
                    "estado": None, "estado_fecha": None, "estado_desde": None,
                })
                r["veces"] += 1
                r["ultima"] = fecha
                r["ultima_message_id"] = f["message_id"]
                votos.setdefault(cod, []).append(
                    {"fecha": fecha, "texto": calles, "compartida": compartida})
                _adoptar_calles(r, calles, texto)
                if bloque is not None:
                    r["bloque"] = bloque          # último bloque conocido gana
                if muni:
                    r["municipio"] = muni
                if causa:
                    r["causa"] = causa
                if est:
                    # Solo un parte CONTRARIO al estado vigente mueve el reloj:
                    # `estado_desde` es la fecha del CAMBIO. Una mención que
                    # coincide (otro "sin" estando ya en apagón) la deja quieta
                    # y el contador sigue corriendo desde el último cambio.
                    if r["estado"] != est:
                        r["estado_desde"] = fecha
                    r["estado"] = est
                    r["estado_fecha"] = fecha
                    _anotar_horas(eventos_horas, [cod], fecha, est)

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
                    "estado": None, "estado_fecha": None, "estado_desde": None,
                })
                r["veces"] += 1
                r["ultima"] = fecha
                r["ultima_message_id"] = f["message_id"]
                r["estado"] = "sin servicio"
                r["estado_fecha"] = fecha
                _anotar_horas(eventos_horas, [cod], fecha, "sin servicio")
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
                codigos_llm = item.get("codigos") or []
                calles_llm = item.get("calles")
                # El LLM también puede repetir un bloque para varios códigos:
                # misma regla de mención compartida que la vía regex. Un texto
                # degenerado no vota ni se adopta.
                compartida_llm = len(codigos_llm) > 1
                voto_llm = bool(calles_llm) and not texto_degenerado(calles_llm, texto)
                for cod in codigos_llm:
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
                        "estado": None, "estado_fecha": None, "estado_desde": None,
                    })
                    if (r["ultima"] or "") <= fecha:
                        r["ultima"] = fecha
                        r["ultima_message_id"] = f["message_id"]
                    if voto_llm:
                        votos.setdefault(cod, []).append(
                            {"fecha": fecha, "texto": calles_llm,
                             "compartida": compartida_llm})
                    # El texto degenerado del LLM no se adopta ni aunque sea el
                    # más largo: la defensa en profundidad vive en _adoptar_calles.
                    _adoptar_calles(r, calles_llm, texto)
                    if item.get("municipio") and not r["municipio"]:
                        r["municipio"] = (municipios_en(item["municipio"]) or [None])[0]
                    # Solo un código escrito explícitamente en el parte puede
                    # cambiar estado; casar un nombre por calles es auxiliar.
                    if cod in codes_estado and \
                            item.get("estado") and (r["estado_fecha"] or "") <= fecha:
                        # Mismo reloj que el camino regex: solo el CAMBIO de
                        # estado mueve `estado_desde`; el guard temporal de
                        # arriba evita retrocederlo con un parte viejo
                        # revalidado. Una mención que coincide no lo toca.
                        if r["estado"] != item["estado"]:
                            r["estado_desde"] = fecha
                        r["estado"] = item["estado"]
                        r["estado_fecha"] = fecha
                        _anotar_horas(eventos_horas, [cod], fecha, item["estado"])

    return cat, eventos_horas


def main():
    # 0) Aprende los circuitos recurrentes que el LLM ve pero el catálogo no
    #    registra (embudo 'por_confirmar'). Sin red, desde partes_llm.json; es
    #    el paso que cierra el ciclo para que sus códigos entren solos. Debe ir
    #    ANTES de todo consumo del catálogo (es_conocido y canonico ya lo ven).
    import aprende_circuitos  # noqa: E402
    aprende_circuitos.main()

    # 1) Histórico del canal. Supabase es la ventana INCREMENTAL (mensajes
    #    nuevos + ediciones recientes); sin credenciales o sin red (corrida
    #    local) el replay sigue en pie con el caché commiteado, que trae la
    #    historia COMPLETA — solo se pierden los mensajes de la ventana.
    try:
        sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
        filas, n_nuevos, n_total = cargar_canal(sb)
        print(f"canal_cache: {n_total} mensajes, {n_nuevos} nuevos de la DB")
    except Exception as e:
        cache = _cargar_cache_canal()
        filas = sorted(cache["filas"].values(),
                       key=lambda m: (m.get("fecha") or "", m.get("message_id") or 0))
        print(f"canal_cache: {len(filas)} mensajes del caché commiteado "
              f"(sin Supabase: {e.__class__.__name__}), replay offline")

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

    # Replay del canal: catálogo Y eventos del histórico de horas en un paso.
    # `votos` acumula las menciones de calles para resolver la dirección por
    # consenso más abajo.
    votos = {}
    cat, eventos_horas = replay_canal(filas, oficial, falsos, llm_cache, votos)

    # Histórico de horas sin corriente (mismo replay) para las páginas de
    # municipio. Días/circuitos sin horas: ausentes, nunca 0. Va ANTES de la
    # geocodificación: si la red se agota, las horas ya quedaron escritas.
    # Señales vecinales "sin" (desde/ultima_sin del conteo de usuario): se
    # siguen cargando solo por compatibilidad de la firma — una señal que
    # COINCIDE con el apagón ya NO extiende el tope del tramo abierto (el reloj
    # se ancla en la apertura; ver horas_historicas). No abren intervalo.
    senales_usuario = {}
    try:
        conteo_previo = json.load(open(CONTEO_USUARIO_FILE))
    except Exception:
        conteo_previo = {}
    for cod, cu in conteo_previo.items():
        fts = []
        for iso in (cu.get("desde"), cu.get("ultima_sin")):
            dt = _fecha_dt(iso)
            if dt is not None:
                fts.append(_a_utc(dt))
        if fts:
            senales_usuario[cod] = fts
    senales_con_usuario = {}
    for cod, cu in conteo_previo.items():
        if not isinstance(cu, dict):
            continue
        dtc = _fecha_dt(cu.get("ultimo_con"))
        if dtc is not None:
            senales_con_usuario[cod] = [_a_utc(dtc)]
    generado_h = _generado_horas(filas)
    horas = redondear_horas(horas_historicas(eventos_horas, generado_h,
                                             senales_usuario,
                                             senales_con_usuario))
    with open(HORAS_JSON, "w", encoding="utf-8") as f:
        f.write(json.dumps({"generado": generado_h.isoformat() if generado_h else None,
                            **horas}, ensure_ascii=False))
    print(f"circuitos_horas: {len(horas['total'])} circuitos con horas históricas "
          f"(replay del canal, por día en HORA_CUBA)")

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

    # Dirección por CONSENSO corroborado del canal (peso por recencia +
    # histéresis + detector de cruce). Va ANTES de la detección de cambios y
    # de la geocodificación (Fase 2), porque cambia `calles`; los códigos sin
    # votos caen al respaldo oficial/aprendido y a `_adoptar_calles`.
    revision = resolver_direcciones(cat, votos, prev_circuitos, generado_h, oficial)
    if revision:
        print(f"direcciones en revisión: {len(revision)} circuitos")
        for e in revision[:20]:
            print(f"  {e['codigo']}: {e['motivo']} · evidencia \"{e['evidencia']}\" "
                  f"({e['menciones']} menciones, confianza {e['confianza']:.2f})")
    else:
        print("direcciones en revisión: ninguna")

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

    # --- Conteo de usuario (discrepancia UNE vs vecinos, EN AMBOS SENTIDOS) ---
    # Lee el conteo producido por estado.py. Cuando la UNE dice "con servicio",
    # resetea el contador de usuario (ultima_reset persiste para que estado.py
    # ignore los "sin" reportes anteriores al reset).
    # Dirección 1 (discrepado, intacta): UNE "con" (o nada) y vecinos dicen
    # sin → los clientes lo pintan "usuarios reportan sin corriente".
    # Dirección 2 (reportado_con, nueva): UNE "sin servicio" y vecinos
    # reportan que volvió (ultimo_con posterior a estado_fecha) → los clientes
    # lo pintan "con servicio (según vecinos)" (estado con_vecinos).
    try:
        conteo_usuario = json.load(open(CONTEO_USUARIO_FILE))
    except Exception:
        conteo_usuario = {}
    for c in circuitos:
        cu = conteo_usuario.get(c["codigo"])
        if not cu:
            c["discrepado"] = False
            c["reportado_con"] = False
            continue
        if c.get("estado") == "con servicio" and cu.get("desde"):
            cu["desde"] = None
            cu.pop("holder_ip_hash", None)  # el holder viejo no suprime tras el reset
            cu["ultimo_reset"] = ahora_c.isoformat()
            cu.pop("horas", None)
        # discrepancia: usuario dice sin (desde not null) y UNE dice con (o nada)
        c["discrepado"] = bool(cu.get("desde") and c.get("estado") in ("con servicio", None))
        c["reportado_con"] = _reportado_con(c, cu)
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
