"""Resumen semanal del servicio eléctrico — La Habana, por email (Mailtrap).

Lee los JSON que el pipeline deja en el árbol (catálogo de circuitos,
histórico de horas confirmadas, estado del sistema, partes clasificados y
señales de usuario), agrega los últimos 7 días calendario y envía el resumen
por la API de Mailtrap (HTML + texto plano). Todo el cálculo es OFFLINE y
determinista — el reloj es el sello `generado` de los datos, nunca el reloj
de la corrida —; la red solo se usa para el envío final.

La clasificación de ciclo de vida (sin/sin_vecinos/con_vecinos/desconocido/
con/asum) reusa la MISMA regla que publica el sitio: importa build_seo
(biblioteca estándar pura, importable sin efectos) y llama a su `_vigencia`,
para que el email y la web nunca diverjan — el reporte vecinal manda en
ambos sentidos (UNE "con" + vecinos "sin" → sin_vecinos; UNE "sin" + vecinos
"con" → con_vecinos). El flag `reportado_con` que esa regla lee se monta
aquí con un espejo exacto de la regla de build_circuitos.py (no se importa
ese módulo porque arrastra dependencias de red que no existen en el
entorno del envío).

Credenciales SOLO por variables de entorno (jamás valores fijos en el
código, jamás en archivos del árbol):
  - MAILTRAP_API_KEY: token de la API de Mailtrap. Requerida para enviar; si
    falta, el script termina con un error claro. En CI debe existir como el
    secret MAILTRAP_API_KEY del sistema de CI (se crea aparte con
    `gh secret set MAILTRAP_API_KEY`); este script solo lo lee del entorno y
    jamás lo imprime, lo persiste ni lo incluye en los mensajes de error.
  - RESUMEN_DESTINATARIO: destinatario de producción (por defecto el
    despacho oficial que recibe el resumen).
  - RESUMEN_REMITENTE: remitente verificado en Mailtrap (por defecto el
    dominio sandbox de la propia Mailtrap).
  - RESUMEN_TEST_PARA: si está definida ANULA al destinatario y antepone
    "[PRUEBA]" al asunto; es la única vía de apuntar el envío a otro buzón,
    para que ninguna dirección de prueba quede escrita en el código ni en
    la automatización.

Uso:
  python3 scripts/resumen_semanal.py              # agrega y envía
  python3 scripts/resumen_semanal.py --dry-run    # agrega y renderiza la
      # vista previa en web/data/.resumen_preview.html (fuera del control de
      # versiones) SIN enviar nada y sin necesitar credenciales

El gráfico "Dónde se soporta el déficit de la capital" va como IMAGEN PNG
generada con matplotlib (backend Agg, importado solo al usarlo): todos los
circuitos catalogados, ordenados de mayor a menor por horas confirmadas sin
corriente, con gradiente rojo→azul, incrustada por `cid` como attachment
inline de Mailtrap. Si matplotlib no está disponible, el correo sale sin
imagen (el HTML muestra una nota y el texto plano conserva el bloque ASCII).
"""

import argparse
import base64
import html
import io
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone

RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_seo  # noqa: E402  (misma regla de `_vigencia` que el sitio, sin duplicar)

CATALOGO_FILE = os.path.join(RAIZ, "web", "data", "circuitos.json")
HORAS_FILE = os.path.join(RAIZ, "web", "data", "circuitos_horas.json")
ESTADO_FILE = os.path.join(RAIZ, "web", "data", "estado.json")
PARTES_FILE = os.path.join(RAIZ, "data", "partes_llm.json")
CONTEO_FILE = os.path.join(RAIZ, "data", "conteo_usuario.json")
CANAL_FILE = os.path.join(RAIZ, "data", "canal_cache.json")

PREVIEW_HTML = os.path.join(RAIZ, "web", "data", ".resumen_preview.html")
PREVIEW_TXT = os.path.join(RAIZ, "web", "data", ".resumen_preview.txt")
PREVIEW_PNG = os.path.join(RAIZ, "web", "data", ".resumen_preview.png")

MAILTRAP_URL = "https://send.api.mailtrap.io/api/send"
NOMBRE_REMITENTE = "Resumen Eléctrico"
DESTINATARIO_DEFECTO = "despacho@presidencia.gob.cu"
REMITENTE_DEFECTO = "resumen@demomailtrap.co"  # dominio sandbox de Mailtrap

VENTANA_DIAS = 7
HORAS_SEMANA = 24.0 * VENTANA_DIAS  # 168 h: techo semanal por circuito
HORA_CUBA = timezone(timedelta(hours=-4))  # mismo huso fijo que el pipeline

# Emojis y adornos del texto del canal (mensajería instantánea) que no deben
# salir en el correo: rangos de símbolos/dingbats/emoji + selectores y ZWJ.
_RE_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002190-\U00002BFF\U0000FE00-\U0000FE0F\u200d\u20e3]"
)
_RE_ESPACIOS = re.compile(r"\s+")

# Etiquetas visibles de la clasificación de ciclo de vida (mismos estados que
# publica el sitio; orden de gravedad). El reporte vecinal manda en AMBOS
# sentidos: UNE "con" + vecinos "sin" → "sin_vecinos" (sin corriente según
# vecinos); UNE "sin" + vecinos "con" → "con_vecinos" (con servicio según
# vecinos). No existe etiqueta de "discrepado": el estado sin_vecinos lo
# sustituye.
ETIQUETAS_VIGENCIA = (
    ("sin", "sin servicio"),
    ("sin_vecinos", "sin corriente (según vecinos)"),
    ("con_vecinos", "con servicio (según vecinos)"),
    ("desconocido", "desconocido"),
    ("con", "con servicio"),
    ("asum", "asumido con corriente"),
)

# Filas autoexplicativas de la tabla "Estado del sistema": el nombre de cada
# fila se explica solo (de dónde sale el estado y desde cuándo), sin exigir
# conocer la jerga interna.
ETIQUETAS_ESTADO_SISTEMA = {
    "sin": "Sin corriente (confirmado por parte oficial)",
    "sin_vecinos": ("Sin corriente (según reportes de vecinos) — "
                    "parte oficial con corriente"),
    "con_vecinos": "Con corriente (según reportes de vecinos)",
    "desconocido": "Desconocido (más de 48 h sin noticias)",
    "con": "Con corriente (confirmado por parte oficial)",
    "asum": "Asumido con corriente (más de una semana sin noticias)",
}

# Párrafo introductorio del correo (HTML y texto plano): de dónde salen los
# datos y qué significan los estados de silencio.
INTRODUCCION = (
    "Este resumen se elabora a partir de la información publicada por la "
    "Empresa Eléctrica de La Habana en su grupo oficial de Telegram, "
    "procesada y analizada de forma automatizada, y complementada con los "
    "reportes y comentarios de la población en ese mismo grupo. Las horas "
    "sin corriente son horas confirmadas por parte oficial o por señales de "
    "la población; los circuitos sin noticias pasan a estado desconocido a "
    "las 48 horas y a asumido con corriente tras una semana, hasta que una "
    "nueva noticia los actualice."
)


# --------------------------------------------------------------------------
# Lectura y reloj de datos
# --------------------------------------------------------------------------

def _leer_json(ruta, obligatorio=True):
    """JSON del árbol o (opcional) {} si falta; errores claros y exit 1."""
    if not obligatorio and not os.path.exists(ruta):
        return {}
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        sys.exit(f"Error: no existe {ruta} — el resumen se alimenta de los "
                 f"JSON que publica el pipeline; ejecútalo antes")
    except ValueError as e:
        sys.exit(f"Error: {ruta} no es JSON válido ({e})")


def _dt(iso):
    """datetime desde ISO de los datos o None (nunca lanza). Los naive se
    leen como UTC, misma convención de build_seo.py — nunca reloj real."""
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _cuba(dt):
    """Conversión a la hora local de La Habana para lo que se MUESTRA."""
    return dt.astimezone(HORA_CUBA)


def reloj_de(estado, catalogo_doc, horas):
    """Sello `generado` del resumen: el MÁS RECIENTE de los sellos de los
    JSON que alimenta (en una corrida normal salen de la misma generación;
    tomar el máximo cubre árboles donde alguno quedó de una generación
    anterior). Determinista: nunca el reloj de la corrida."""
    sellos = [s for s in (_dt(d.get("generado")) for d in (estado, catalogo_doc, horas)) if s]
    if not sellos:
        sys.exit("Error: ningún JSON trae el sello 'generado' — no hay reloj de datos")
    return max(sellos)


def limpiar_texto(texto):
    """Texto apto para el correo: sin emojis/adornos del canal, espacios
    colapsados. No altera el contenido, solo el formato."""
    return _RE_ESPACIOS.sub(" ", _RE_EMOJI.sub(" ", str(texto or ""))).strip()


def _normalizar(texto):
    """Minúsculas, sin tildes ni emojis — para clasificar patrones del canal
    sin importar cómo lo escribió el operador (AVERÍA / AVERIA)."""
    t = unicodedata.normalize("NFD", limpiar_texto(texto))
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return t.lower()


# --------------------------------------------------------------------------
# Clasificación de roturas (función pura, testeada)
# --------------------------------------------------------------------------

def categorizar_rotura(texto):
    """Tipo de avería según el texto ORIGINAL del canal (message_id del caché),
    insensible a tildes, mayúsculas, emojis y espacios dobles. Categorías: los
    patrones que el canal realmente usa — 'AVERÍA SECUNDARIA por TRANSFORMADOR
    DAÑADO' (la combinación habitual), 'AVERÍA PRIMARIA' / 'PRIMARIO PARTIDO'
    (la familia de la línea primaria) — y 'Otra avería' para el resto o para
    mensajes que ya no están en el caché. Gana el patrón más específico:
    el transformador es el dato operativo del arreglo."""
    t = _normalizar(texto)
    if re.search(r"transformador\s+danad", t):
        return "Transformador dañado"
    if re.search(r"averia\s+secundaria", t):
        return "Avería secundaria"
    if re.search(r"averia\s+primaria|primario\s+partido", t):
        return "Avería primaria"
    return "Otra avería"


# --------------------------------------------------------------------------
# Ventana y agregaciones (funciones puras, testeadas)
# --------------------------------------------------------------------------

def en_ventana(iso, desde, hasta):
    """True si el ISO cae en [desde, hasta] (cerrado). Fechas rotas o
    ausentes → False (no inventar)."""
    dt = _dt(iso)
    return dt is not None and desde <= dt <= hasta


def _dia(iso_dia):
    """Fecha calendario desde la clave de por_dia ('AAAA-MM-DD') o None."""
    try:
        return datetime.strptime(iso_dia, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def horas_sin_ventana(horas, codigo, dia0, dia1):
    """Horas CONFIRMADAS sin corriente del circuito dentro de la ventana:
    suma del reparto diario de circuitos_horas.json cuyos días calendario de
    La Habana (mismo huso del reparto del pipeline) tocan [dia0, dia1].
    Circuitos o días sin registro: ausentes, nunca 0 — aquí 0.0 solo si no
    hay nada que sumar."""
    por_dia = (horas.get("por_dia") or {}).get(codigo) or {}
    total = 0.0
    for dia, h in por_dia.items():
        d = _dia(dia)
        if d is not None and dia0 <= d <= dia1 and h:
            total += float(h)
    return total


def con_datos_horas(horas, codigo):
    """El circuito tiene medición de horas (aparece en el histórico):
    requisito para el top 'más horas con corriente' — sin medición no se
    puede afirmar corriente constante, aunque no registre cortes."""
    return (codigo in (horas.get("total") or {})
            or codigo in (horas.get("por_dia") or {}))


def municipio_de(c):
    """Municipio principal del circuito (campo `municipio`; respaldo: el
    primero de `municipios`)."""
    m = c.get("municipio")
    if m:
        return m
    otros = c.get("municipios") or []
    return otros[0] if otros else None


def municipio_canonico(nombre, nombres):
    """Nombre canónico del municipio del parte: los partes traen el municipio
    a mano (con variantes de tilde o sin el artículo inicial — 'Lisa',
    'San Miguel del Padròn'); se mapea al nombre EXACTO del catálogo
    (primero igualdad sin tildes, luego contención de nombre completo) para
    que la agrupación no parta un mismo municipio en dos filas. Sin pareja
    conocida, se respeta el texto original."""
    if not nombre or not nombres:
        return nombre
    n = _normalizar(nombre)
    for canon in sorted(nombres):
        if _normalizar(canon) == n:
            return canon
    for canon in sorted(nombres):
        c = _normalizar(canon)
        if c and (c in n or n in c):
            return canon
    return nombre


def resumen_por_municipio(catalogo, horas, dia0, dia1):
    """Fila por municipio — circuitos catalogados, afectados en la ventana
    (con alguna hora confirmada de corte), horas sin corriente (suma
    confirmada), horas con corriente (circuitos × 168 h menos las sin
    corriente, piso 0) y % del tiempo con corriente — ordenado por horas sin
    corriente DESC, más la fila total de La Habana. Devuelve (filas, total)."""
    filas = {}
    for c in catalogo:
        m = municipio_de(c)
        if not m:
            continue
        f = filas.setdefault(m, {"catalogados": 0, "afectados": 0, "horas_sin": 0.0})
        f["catalogados"] += 1
        hs = horas_sin_ventana(horas, c.get("codigo"), dia0, dia1)
        f["horas_sin"] += hs
        if hs > 0:
            f["afectados"] += 1
    for f in filas.values():
        techo = f["catalogados"] * HORAS_SEMANA
        f["horas_con"] = max(0.0, techo - f["horas_sin"])
        f["pct"] = (f["horas_con"] / techo * 100.0) if techo else 0.0
    orden = sorted(filas.items(), key=lambda kv: (-kv[1]["horas_sin"], kv[0]))
    tot = {
        "catalogados": sum(f["catalogados"] for _, f in orden),
        "afectados": sum(f["afectados"] for _, f in orden),
        "horas_sin": sum(f["horas_sin"] for _, f in orden),
    }
    techo = tot["catalogados"] * HORAS_SEMANA
    tot["horas_con"] = max(0.0, techo - tot["horas_sin"])
    tot["pct"] = (tot["horas_con"] / techo * 100.0) if techo else 0.0
    return orden, tot


def menciones_ventana(partes, desde, hasta):
    """Código de circuito → número de partes de la ventana (cualquier tipo)
    que lo mencionan, por circuitos confirmados y por confirmar."""
    conteo = Counter()
    for p in partes.values():
        if not en_ventana(p.get("fecha"), desde, hasta):
            continue
        codigos = set()
        for ci in p.get("circuitos") or []:
            codigos.update(ci.get("codigos") or [])
        codigos.update(pc for pc in (p.get("por_confirmar") or []) if pc)
        for cod in codigos:
            conteo[cod] += 1
    return conteo


def top_sin_global(catalogo, horas, dia0, dia1, menciones, top=15):
    """Top GLOBAL (un solo ranking, no por municipio) de circuitos con MÁS
    horas confirmadas sin corriente en la ventana (solo los que registran
    corte), ordenado por horas DESC: filas (municipio, código, horas, # de
    partes que lo mencionaron en la semana). Techo 15: el detalle por
    municipio ya está en el resumen de arriba."""
    res = []
    for c in catalogo:
        m = municipio_de(c)
        cod = c.get("codigo")
        if not m or not cod:
            continue
        hs = horas_sin_ventana(horas, cod, dia0, dia1)
        if hs <= 0:
            continue
        res.append((m, cod, hs, menciones.get(cod, 0)))
    res.sort(key=lambda t: (-t[2], t[0], t[1]))
    return res[:top]


def top_con_global(catalogo, horas, vigencias, dia0, dia1, top=15):
    """Top GLOBAL de circuitos con MÁS horas con corriente en la ventana,
    ENTRE LOS QUE TIENEN MEDICIÓN (criterio: un circuito sin registros de
    horas no prueba corriente constante, no entra), ordenado por horas con
    corriente DESC: filas (municipio, código, horas, etiqueta del estado del
    ciclo de vida). Horas con corriente = 168 h menos las confirmadas sin
    corriente, piso 0. Techo 15."""
    etiqueta = dict(ETIQUETAS_VIGENCIA)
    res = []
    for c in catalogo:
        m = municipio_de(c)
        cod = c.get("codigo")
        if not m or not cod or not con_datos_horas(horas, cod):
            continue
        con = max(0.0, HORAS_SEMANA - horas_sin_ventana(horas, cod, dia0, dia1))
        res.append((m, cod, con, etiqueta.get(vigencias.get(cod), "desconocido")))
    res.sort(key=lambda t: (-t[2], t[0], t[1]))
    return res[:top]


# Gráfico "Dónde se soporta el déficit de la capital": la DISTRIBUCIÓN
# completa de las horas sin corriente confirmadas de la semana, un circuito
# del catálogo por barra. N es la cantidad de circuitos del bloque superior
# con la que se mide la concentración (el titular).
TOP_GRAFICO = 10

# Gradiente del gráfico: rojo para las horas máximas de la semana → azul para
# los circuitos con 0 h (siempre con corriente). Interpolación RGB manual.
COLOR_ROJO = "#dc2626"
COLOR_AZUL = "#2563eb"


def color_deficit(horas, maximo):
    """Color de la barra del circuito: interpolación lineal en RGB de
    COLOR_AZUL (0 h) a COLOR_ROJO (el máximo de la semana), según horas/max.
    Devuelve el hex '#rrggbb' que consumen matplotlib y los tests."""
    t = 0.0 if maximo <= 0 else min(1.0, max(0.0, horas / maximo))
    r = round(0x25 + (0xDC - 0x25) * t)
    g = round(0x63 + (0x26 - 0x63) * t)
    b = round(0xEB + (0x26 - 0xEB) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def datos_grafico_deficit(catalogo, horas, dia0, dia1):
    """Datos del gráfico de distribución del déficit: TODOS los circuitos
    catalogados (con municipio y código), incluidos los de 0 h — siempre con
    corriente: son parte del mensaje visual —, ordenados DESC por horas
    CONFIRMADAS sin corriente en la ventana (la MISMA medida que ya agrega el
    script), con desempate por municipio y código. Cada fila trae su color
    del gradiente (rojo = máximo de la semana, azul = 0 h). Aparte va la
    concentración P del bloque superior (TOP_GRAFICO circuitos): horas del
    top × 100 / TOTAL de horas sin corriente de la semana — el denominador es
    el MISMO que suma la tabla del resumen por municipio, de modo que gráfico
    y tabla no puedan divergir. Devuelve None si la semana no registra
    cortes (la sección se omite por completa; la concentración sería 0/0)."""
    filas = []
    total = 0.0
    for c in catalogo:
        m = municipio_de(c)
        cod = c.get("codigo")
        if not m or not cod:
            continue
        hs = horas_sin_ventana(horas, cod, dia0, dia1)
        total += hs
        filas.append((m, cod, hs))
    if total <= 0:
        return None
    filas.sort(key=lambda t: (-t[2], t[0], t[1]))
    maximo = filas[0][2]
    top_n = min(TOP_GRAFICO, len(filas))
    return {
        "filas": [
            {"municipio": m, "codigo": cod, "horas": hs,
             "color": color_deficit(hs, maximo)}
            for m, cod, hs in filas
        ],
        "total": total,
        "top_n": top_n,
        "concentracion": int(round(
            sum(hs for _, _, hs in filas[:top_n]) * 100.0 / total)),
    }


def _reportado_con(c, cu):
    """Espejo exacto de build_circuitos.py::_reportado_con (dirección 2 del
    reporte vecinal): "sin servicio" de la UNE con `ultimo_con` vecinal
    posterior a `estado_fecha` y sin "se fue" posterior al último "volvió".
    Copiado, no importado: ese módulo arrastra dependencias de red. NUNCA
    lanza (degrada a False), igual que el original."""
    if not (c.get("estado") == "sin servicio" and cu and cu.get("ultimo_con")
            and c.get("estado_fecha")):
        return False
    a, b = cu["ultimo_con"], c["estado_fecha"]
    try:
        desde_dt = datetime.fromisoformat(cu["desde"]) if cu.get("desde") else None
        volvio_dt = datetime.fromisoformat(a)
        caida_dt = datetime.fromisoformat(b)
    except (TypeError, ValueError):  # formato raro: comparación léxica
        try:
            return (not cu.get("desde") or cu["desde"] < a) and a > b
        except Exception:
            return False
    return (not desde_dt or desde_dt < volvio_dt) and volvio_dt > caida_dt


def fusionar_conteo(catalogo, conteo_usuario):
    """Monta las señales de usuario en los registros del catálogo — las dos
    claves que `_vigencia` lee (`conteo_usuario` y `reportado_con`) — tal
    cual están persistidas: el reset oficial de la UNE ya lo aplicó el
    pipeline al escribir el JSON."""
    for c in catalogo:
        cu = conteo_usuario.get(c.get("codigo")) or None
        c["conteo_usuario"] = cu
        c["reportado_con"] = _reportado_con(c, cu) if cu else False


def vigencias_de(catalogo, gen, evento_nacional):
    """Código → estado vigente con la MISMA regla del sitio (build_seo
    `_vigencia`): sin/con_vecinos/desconocido/con/asum. La puerta de
    evento_nacional se fija una vez aquí, igual que el builder la fija por
    corrida; el reloj es el `generado` de los datos."""
    build_seo._EVENTO_NACIONAL = bool(evento_nacional)
    return {c.get("codigo"): build_seo._vigencia(c, gen) for c in catalogo}


def distribucion_vigencia(catalogo, gen, evento_nacional=False):
    """Circuitos por estado del ciclo de vida, en el orden de gravedad.
    Incluye sin_vecinos: la cifra de "sin corriente" del sistema es la de
    los estados caídos, oficiales o vecinales por igual."""
    vigencias = vigencias_de(catalogo, gen, evento_nacional)
    conteo = Counter(vigencias.values())
    return {clave: conteo.get(clave, 0) for clave, _ in ETIQUETAS_VIGENCIA}


def _silencio_superior_a_semana(c, gen):
    """True si el silencio total del circuito supera la semana (168 h):
    reusa el MISMO reloj que `_vigencia` (build_seo._silencio_horas, sobre la
    última noticia de la UNE o del usuario). Sin reloj completo → False
    (no inventar)."""
    if gen is None:
        return False
    h = build_seo._silencio_horas(c, gen)
    return h is not None and h > HORAS_SEMANA


def no_afectados_por_municipio(catalogo, horas, vigencias, dia0, dia1, gen=None):
    """Municipio → códigos que NO se están afectando en la ventana: sin una
    sola hora de corte confirmada Y sin caída confirmada. Criterio del
    mantenedor — entran:
      - vigencia "con" (con servicio confirmado por parte oficial),
      - "con_vecinos" (los vecinos dicen que volvió),
      - "asum" (más de una semana sin noticias — "no se apagan"),
      - "desconocido" con silencio total > 7 días (168 h): aún desconocido,
        pero la semana de incertidumbre ya pasó — el mantenedor los considera
        no afectados también.
    Quedan FUERA: "sin" y "sin_vecinos" (caída confirmada, oficial o
    vecinal) y "desconocido" con silencio <= 7 días (aún en su semana de
    incertidumbre)."""
    res = {}
    for c in catalogo:
        m = municipio_de(c)
        cod = c.get("codigo")
        if not m or not cod:
            continue
        vig = vigencias.get(cod)
        if vig in ("con", "con_vecinos", "asum"):
            pass  # sin caída confirmada; el filtro de horas decide abajo
        elif vig == "desconocido":
            if not _silencio_superior_a_semana(c, gen):
                continue  # aún dentro de su semana de incertidumbre
        else:  # "sin", "sin_vecinos" o estado sin clasificar
            continue
        if horas_sin_ventana(horas, cod, dia0, dia1) > 0:
            continue  # registró corte confirmado en la ventana: sí se afecta
        res.setdefault(m, []).append(cod)
    for lst in res.values():
        lst.sort()
    # Agrupado por municipio con conteo DESC (empate: alfabético).
    return {m: res[m] for m in
            sorted(res, key=lambda m: (-len(res[m]), m))}


def roturas_ventana(partes, canal, desde, hasta, nombres_municipio=None):
    """Partes de avería de la ventana: total de PARTES, conteo por
    (municipio, tipo clasificado del texto original) y una fila por circuito
    del parte (fecha, municipio, tipo, dirección `calles` del parte), en
    orden de fecha DESC (lo más nuevo primero). El tipo sale del texto del
    canal por message_id; si el mensaje ya no está en el caché, 'Otra
    avería'. El municipio del parte se mapea al nombre canónico del catálogo
    (`nombres_municipio`)."""
    total = 0
    conteo = Counter()
    filas = []
    for mid, p in partes.items():
        if p.get("tipo") != "averia" or not en_ventana(p.get("fecha"), desde, hasta):
            continue
        total += 1
        tipo = categorizar_rotura((canal.get(str(mid)) or {}).get("texto", ""))
        for ci in p.get("circuitos") or []:
            m = municipio_canonico(ci.get("municipio"), nombres_municipio) \
                or "sin municipio"
            filas.append({
                "fecha": _dt(p.get("fecha")),
                "municipio": limpiar_texto(m),
                "tipo": tipo,
                "calles": limpiar_texto(ci.get("calles")),
            })
            conteo[(m, tipo)] += 1
    # Fecha DESC (lo más nuevo primero); empate: municipio y tipo ascendentes.
    filas.sort(key=lambda r: (r["municipio"], r["tipo"]))
    filas.sort(key=lambda r: r["fecha"] or datetime.min.replace(tzinfo=timezone.utc),
               reverse=True)
    return {"total": total, "conteo": conteo, "filas": filas}


def senales_vecinales(catalogo, conteo_usuario, vigencias, gen):
    """Señales vecinales de la semana: circuitos con señal de usuario cuyo
    desde/ultima_sin/ultimo_con cae en los últimos 7 días (desde `generado`).
    Devuelve {"total", "por_municipio" ([(municipio, n)] conteo DESC),
    "sin_vecinos" (discrepados vigentes: parte oficial con corriente y
    vecinos sin corriente) y "con_vecinos" (vecinos dicen que volvió)} — el
    reporte vecinal manda en ambos sentidos."""
    desde = gen - timedelta(days=VENTANA_DIAS)
    municipio_de_cod = {c.get("codigo"): municipio_de(c) for c in catalogo}
    total = 0
    por_municipio = Counter()
    discrepados = 0
    con_vecinos = 0
    for cod, cu in (conteo_usuario or {}).items():
        if not any(en_ventana(cu.get(k), desde, gen)
                   for k in ("desde", "ultima_sin", "ultimo_con")):
            continue
        total += 1
        por_municipio[municipio_de_cod.get(cod) or "sin municipio"] += 1
        vig = vigencias.get(cod)
        if vig == "sin_vecinos":
            discrepados += 1
        elif vig == "con_vecinos":
            con_vecinos += 1
    orden = sorted(por_municipio.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"total": total, "por_municipio": orden,
            "sin_vecinos": discrepados, "con_vecinos": con_vecinos}


def agregar(catalogo_doc, horas, estado, partes, canal, conteo_usuario):
    """Agregación completa de la semana → un solo diccionario para el
    render. Determinista: mismas entradas, mismas salidas."""
    catalogo = catalogo_doc.get("circuitos") or []
    gen = reloj_de(estado, catalogo_doc, horas)
    desde = gen - timedelta(days=VENTANA_DIAS)
    # Las horas confirmadas se reparten por día calendario de La Habana (mismo
    # huso fijo del pipeline): la ventana de horas son los ÚLTIMOS 7 DÍAS
    # calendario (los 7 que terminan en el día de `generado`), de modo que el
    # techo de 168 h por circuito es exacto. El rango horario [generado − 7d,
    # generado] completo se aplica a los partes, que traen timestamp.
    dia1 = _cuba(gen).date()
    dia0 = dia1 - timedelta(days=VENTANA_DIAS - 1)
    fusionar_conteo(catalogo, conteo_usuario)
    evento = bool(estado.get("evento_nacional"))
    vigencias = vigencias_de(catalogo, gen, evento)
    filas, total = resumen_por_municipio(catalogo, horas, dia0, dia1)
    nombres_municipio = {m for m in (municipio_de(c) for c in catalogo) if m}
    mw = (estado.get("deficit") or {}).get("mw")
    return {
        "generado": gen,
        "desde": desde,
        "filas": filas,
        "total": total,
        "top_sin": top_sin_global(catalogo, horas, dia0, dia1,
                                  menciones_ventana(partes, desde, gen)),
        "grafico_deficit": datos_grafico_deficit(catalogo, horas, dia0, dia1),
        "top_con": top_con_global(catalogo, horas, vigencias, dia0, dia1),
        "no_afectados": no_afectados_por_municipio(catalogo, horas, vigencias,
                                                   dia0, dia1, gen),
        "roturas": roturas_ventana(partes, canal, desde, gen, nombres_municipio),
        "distribucion": distribucion_vigencia(catalogo, gen, evento),
        "senales": senales_vecinales(catalogo, conteo_usuario, vigencias, gen),
        "mw": mw if isinstance(mw, (int, float)) and mw > 0 else None,
    }


# --------------------------------------------------------------------------
# Render (HTML con estilos inline + texto plano)
# --------------------------------------------------------------------------

# Las tablas fijan su ancho completo con el ATRIBUTO width="100%" (no CSS):
# el atributo es lo que mejor entiende el motor Word de Outlook. La única
# anchura en % de los estilos inline del documento es la de la imagen del
# gráfico de déficit (width:100% con techo max-width, que los clientes de
# correo respetan).
_T_TABLE = "border-collapse:collapse;font-size:13px;"
_T_TH = "text-align:left;padding:6px 8px;background:#eef1f4;border-bottom:2px solid #c8ced4;font-size:12px;color:#333;"
_T_TH_D = "text-align:right;padding:6px 8px;background:#eef1f4;border-bottom:2px solid #c8ced4;font-size:12px;color:#333;"
_T_TD = "padding:6px 8px;border-bottom:1px solid #e4e7ea;"
_T_TD_D = "padding:6px 8px;border-bottom:1px solid #e4e7ea;text-align:right;"

_H2 = "font-size:15px;margin:24px 0 8px;color:#111;"
_NOTA = "font-size:12px;color:#666;margin:6px 0 0;"


def _tabla(cabeceras, filas, numericas=()):
    """Tabla simple con estilos inline (los clientes de correo no cargan CSS
    externo). `numericas` son los índices de columnas alineadas a la derecha.
    Todo dato dinámico pasa por html.escape."""
    th = "".join(
        f'<th style="{_T_TH_D if i in numericas else _T_TH}">{html.escape(c)}</th>'
        for i, c in enumerate(cabeceras)
    )
    cuerpo = []
    for fila in filas:
        tds = "".join(
            f'<td style="{_T_TD_D if i in numericas else _T_TD}">{celda}</td>'
            for i, celda in enumerate(fila)
        )
        cuerpo.append(f"<tr>{tds}</tr>")
    return (f'<table width="100%" style="{_T_TABLE}">'
            f"<thead><tr>{th}</tr></thead><tbody>{''.join(cuerpo)}</tbody></table>")


def _ent(v):
    """Entero para conteos (circuitos, partes, menciones)."""
    return html.escape(str(int(v)))


def _num(v, sufijo=""):
    """Número con 1 decimal para horas/porcentajes."""
    return html.escape(f"{v:.1f}{sufijo}")


def _seccion(titulo, cuerpo, nota=None):
    extra = f'<p style="{_NOTA}">{nota}</p>' if nota else ""
    return f'<h2 style="{_H2}">{titulo}</h2>{cuerpo}{extra}'


_ORDEN_GRAVEDAD = {clave: i for i, (clave, _) in enumerate(ETIQUETAS_VIGENCIA)}


def estado_sistema_ordenado(distribucion):
    """[(clave, cantidad entera)] de la tabla "Estado del sistema", ordenada
    por cantidad DESC (empate: orden de gravedad). Cada fila se explica sola
    con su etiqueta de ETIQUETAS_ESTADO_SISTEMA."""
    return sorted(
        ((clave, int(distribucion.get(clave, 0)))
         for clave in ETIQUETAS_ESTADO_SISTEMA),
        key=lambda kv: (-kv[1], _ORDEN_GRAVEDAD.get(kv[0], len(_ORDEN_GRAVEDAD))))


def filas_estado_sistema(distribucion):
    """Filas renderizables de "Estado del sistema": (etiqueta autoexplicativa
    escapada, cantidad entera)."""
    return [[html.escape(ETIQUETAS_ESTADO_SISTEMA[clave]), _ent(n)]
            for clave, n in estado_sistema_ordenado(distribucion)]


def filas_roturas_resumen(conteo):
    """Filas (municipio, tipo, cantidad) del resumen de roturas, ordenadas
    por cantidad DESC (empate: municipio, tipo); cantidades ENTERAS."""
    pares = sorted(conteo.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))
    return [[html.escape(m), html.escape(tipo), _ent(n)]
            for (m, tipo), n in pares]


# Etiquetas del bloque ASCII del texto plano (la imagen PNG solo sustituye
# al gráfico del HTML).
_MAX_ETIQUETA = 28
_ANCHO_BARRA_TXT = 30  # caracteres █ máximo de la barra en texto plano


def _truncar(texto, maximo=_MAX_ETIQUETA):
    """Etiqueta recortada a `maximo` caracteres + '…' si excede."""
    return texto if len(texto) <= maximo else texto[:maximo] + "…"


# --------------------------------------------------------------------------
# Gráfico del déficit (imagen PNG con matplotlib, incrustada por cid)
# --------------------------------------------------------------------------

# Formato del PNG: ~1400×420 px, fondo blanco, sin adornos, <= 300 KB.
GRAFICO_ANCHO_PX = 1400
GRAFICO_ALTO_PX = 420
GRAFICO_DPI = 100
GRAFICO_LIMITE_BYTES = 300 * 1024
GRAFICO_FILENAME = "deficit_semana.png"
GRAFICO_CID = "grafico-deficit"
GRAFICO_TITULO = "Dónde se soporta el déficit de la capital — distribución por circuito"


def generar_grafico_deficit(g):
    """PNG de la distribución del déficit: una barra vertical por circuito
    (TODOS los del catálogo, ordenados DESC por horas confirmadas sin
    corriente), eje Y = horas (0-168+), eje X sin etiquetas individuales
    (demasiados; solo el orden importa). Gradiente rojo→azul por intensidad:
    bloque rojo de circuitos con la semana completa sin corriente → caída →
    cola azul de los que siempre tuvieron corriente. Anotaciones dentro de la
    imagen: el máximo real arriba del primer bloque, la marca vertical
    punteada donde las barras cruzan las 48 h del ciclo de vida — la de 216 h
    no se dibuja: con el techo semanal de 168 h nunca se cruza y solo
    ensuciaría — y la concentración del top 10 en la esquina. Devuelve los
    bytes del PNG (<= 300 KB) o None si matplotlib no está disponible (el
    correo sale sin imagen, degradación graciosa) o si `g` no trae filas."""
    if not g or not g.get("filas"):
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")  # CI sin display: antes de importar pyplot
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    filas = g["filas"]
    horas = [f["horas"] for f in filas]
    colores = [f["color"] for f in filas]
    maximo = max(horas)

    fig = plt.figure(
        figsize=(GRAFICO_ANCHO_PX / GRAFICO_DPI, GRAFICO_ALTO_PX / GRAFICO_DPI),
        dpi=GRAFICO_DPI, facecolor="white")
    try:
        ax = fig.add_axes([0.06, 0.10, 0.90, 0.74])
        ax.set_facecolor("white")
        ax.bar(range(len(horas)), horas, width=1.0, color=colores,
               linewidth=0)
        ax.set_xlim(-0.5, len(horas) - 0.5)
        ax.set_ylim(0, max(maximo * 1.18, 1.0))
        ax.set_ylabel("Horas sin corriente (confirmadas)", fontsize=9,
                      color="#333")
        ax.tick_params(axis="y", labelsize=8, colors="#333")
        ax.tick_params(axis="x", bottom=False, labelbottom=False)
        for lado in ("top", "right"):
            ax.spines[lado].set_visible(False)
        ax.spines["left"].set_color("#c8ced4")
        ax.spines["bottom"].set_color("#c8ced4")

        # Anotación sobre el primer bloque: el máximo real de la semana.
        if maximo >= HORAS_SEMANA - 0.5:
            texto_maximo = "168 h — sin corriente toda la semana"
        else:
            texto_maximo = f"{maximo:.0f} h — máximo de la semana"
        ax.annotate(texto_maximo,
                    xy=(0, maximo), xytext=(6, 4), textcoords="offset points",
                    fontsize=9, color="#7f1d1d", ha="left", va="bottom")

        # Marca vertical punteada donde las barras cruzan las 48 h (umbral
        # del ciclo de vida: más silencio que eso → desconocido). La de 216 h
        # (azul ← una semana) no se dibuja: el techo semanal es 168 h, la
        # distribución nunca la cruza y solo ensuciaría el gráfico.
        cruce = next((i for i, h in enumerate(horas) if h < 48.0), None)
        if cruce is not None and cruce > 0:
            ax.axvline(cruce - 0.5, color="#94a3b8", linestyle=":",
                       linewidth=1)
            ax.text(cruce + 2, maximo * 0.92, "desconocido ← 48 h",
                    fontsize=7, color="#64748b", ha="left", va="top")

        # Esquina: concentración del top 10 con el denominador correcto
        # (total de horas sin corriente de la semana).
        ax.text(0.995, 0.97,
                f"El {int(g['concentracion'])}% de las horas sin corriente "
                f"de la semana se concentró en {int(g['top_n'])} circuitos",
                transform=ax.transAxes, fontsize=8.5, color="#334155",
                ha="right", va="top",
                bbox={"facecolor": "white", "edgecolor": "#e2e8f0",
                      "boxstyle": "round,pad=0.35", "alpha": 0.9})

        fig.suptitle(GRAFICO_TITULO, fontsize=12, color="#111", x=0.06,
                     ha="left", y=0.97)
        bufer = io.BytesIO()
        fig.savefig(bufer, format="png", dpi=GRAFICO_DPI, facecolor="white")
    finally:
        plt.close(fig)
    return bufer.getvalue()


def render_grafico_deficit(g, png=True):
    """Cuerpo HTML de la sección del gráfico: la cifra de concentración como
    titular en texto (queda ENCIMA de la imagen, también con imagen) y la
    imagen PNG incrustada por `cid` — el attachment inline lo monta el envío.
    Si el PNG no pudo generarse (matplotlib ausente, png falsy), degradación
    graciosa: nota textual en su lugar, sin abortar el envío."""
    titular = (
        f'<p style="margin:0 0 10px;font-size:13px;color:#0f172a;">'
        f"El <strong>{int(g['concentracion'])}%</strong> de las horas sin "
        f"corriente confirmadas de la semana se concentró en solo "
        f"<strong>{int(g['top_n'])}</strong> circuitos.</p>")
    if not png:
        return titular + (
            '<p style="margin:0 0 8px;font-size:13px;color:#0f172a;">'
            "La distribución completa por circuito no pudo renderizarse como "
            "imagen en este envío; el detalle está en las tablas de abajo y "
            "en el bloque de barras del texto plano.</p>")
    return titular + (
        f'<img src="cid:{GRAFICO_CID}" '
        f'alt="Distribución del déficit por circuito" '
        f'style="width:100%;max-width:{GRAFICO_ANCHO_PX}px;height:auto;'
        f'border:0;">')


def render_html(res, png=None):
    """Correo en HTML: tablas simples con estilos inline, sin JavaScript, sin
    imágenes externas. Cero referencias fuera del propio contenido. La imagen
    del gráfico de déficit va incrustada por `cid` (attachment inline que
    monta el envío); con `png` vacío (matplotlib ausente) la sección muestra
    la nota textual en su lugar."""
    gen, desde = _cuba(res["generado"]), _cuba(res["desde"])
    encabezado = (
        '<h1 style="font-size:20px;margin:0 0 4px;color:#111;">'
        "Resumen semanal del servicio eléctrico — La Habana</h1>"
        f'<p style="color:#555;font-size:13px;margin:0 0 8px;">'
        f"Del {desde.strftime('%d/%m')} al {gen.strftime('%d/%m')} · "
        f"Generado el {gen.strftime('%d/%m/%Y %H:%M')} (hora de Cuba)</p>"
        # Introducción: de dónde salen los datos y qué significan los estados
        # de silencio — antes de la primera tabla.
        f'<div style="background:#f4f8fb;border-left:4px solid #2c6e9e;'
        f'padding:10px 14px;margin:0 0 8px;border-radius:4px;'
        f'font-size:13px;color:#333;line-height:1.5;">'
        f"{html.escape(INTRODUCCION)}</div>"
    )

    filas_html = []
    for m, f in res["filas"]:
        filas_html.append([
            html.escape(m), _ent(f["catalogados"]), _ent(f["afectados"]),
            _num(f["horas_sin"]), _num(f["horas_con"]), _num(f["pct"], " %"),
        ])
    t = res["total"]
    filas_html.append([
        "<strong>Total La Habana</strong>",
        f'<strong>{_ent(t["catalogados"])}</strong>',
        f'<strong>{_ent(t["afectados"])}</strong>',
        f'<strong>{_num(t["horas_sin"])}</strong>',
        f'<strong>{_num(t["horas_con"])}</strong>',
        f'<strong>{_num(t["pct"], " %")}</strong>',
    ])
    tabla_resumen = _tabla(
        ["Municipio", "Circuitos", "Afectados", "Horas sin corriente",
         "Horas con corriente", "% con corriente"],
        filas_html, numericas={1, 2, 3, 4, 5})

    # Top 15 GLOBAL (un solo ranking, ordenado por horas sin corriente DESC):
    # el detalle por municipio ya está en el resumen de arriba.
    filas_top = [[html.escape(m), html.escape(cod), _num(hs), _ent(n)]
                 for m, cod, hs, n in res["top_sin"]]
    tabla_top_sin = (_tabla(["Municipio", "Circuito", "Horas sin corriente",
                             "Partes que lo mencionaron"],
                            filas_top, numericas={2, 3})
                     if filas_top else "<p>No hubo circuitos afectados en la semana.</p>")

    # Top 15 GLOBAL por horas con corriente DESC, con el estado del ciclo.
    filas_top_con = [[html.escape(m), html.escape(cod), _num(con),
                      html.escape(etq)]
                     for m, cod, con, etq in res["top_con"]]
    tabla_top_con = (_tabla(["Municipio", "Circuito", "Horas con corriente",
                             "Estado del ciclo"],
                            filas_top_con, numericas={2})
                     if filas_top_con else
                     "<p>No hay mediciones suficientes para este ranking.</p>")

    # Señales vecinales de la semana: el reporte vecinal manda en ambos
    # sentidos — discrepados (vecinos sin corriente, parte oficial con
    # corriente) y restablecimientos según vecinos.
    sen = res["senales"]
    filas_sen = [[html.escape(m), _ent(n)] for m, n in sen["por_municipio"]]
    tabla_sen = (_tabla(["Municipio", "Circuitos con señal"],
                        filas_sen, numericas={1})
                 if filas_sen else "")
    cuerpo_sen = (
        f'<p style="margin:0 0 8px;">Señales de la población (reportes y '
        f"comentarios del grupo) sobre <strong>{_ent(sen['total'])}</strong> "
        f"circuitos en la semana: {_ent(sen['sin_vecinos'])} discrepan con la "
        f"parte oficial (los vecinos los reportan sin corriente) y "
        f"{_ent(sen['con_vecinos'])} confirman restablecimiento (los vecinos "
        f"los reportan con corriente). Estos reportes alimentan las horas y "
        f"los estados del sistema de este resumen.</p>" + tabla_sen
        if sen["total"] else
        "<p>Sin señales de la población en la semana.</p>")

    parrafos_no = []
    for m, codigos in res["no_afectados"].items():
        parrafos_no.append(
            f"<p><strong>{html.escape(m)}</strong> ({len(codigos)}): "
            f"{html.escape(', '.join(codigos))}</p>")
    cuerpo_no = ("".join(parrafos_no) if parrafos_no
                 else "<p>Sin circuitos en esa condición esta semana.</p>")

    # Roturas: frase introductoria + tabla resumen legible (cantidad entera,
    # orden DESC) + tabla detallada por fecha DESC (todas las filas).
    rot = res["roturas"]
    p_rot = (f'<p style="margin:0 0 8px;">En la semana hubo '
             f"<strong>{_ent(rot['total'])}</strong> roturas reportadas por "
             f"la UNE, distribuidas así:</p>")
    filas_rot_resumen = filas_roturas_resumen(rot["conteo"])
    tabla_rot_resumen = (_tabla(["Municipio", "Tipo de avería", "Roturas"],
                                filas_rot_resumen, numericas={2})
                         if filas_rot_resumen else "")
    filas_rot = [
        [html.escape(_cuba(r["fecha"]).strftime("%d/%m %H:%M")),
         html.escape(r["municipio"]), html.escape(r["tipo"]),
         html.escape(r["calles"] or "—")]
        for r in rot["filas"]
    ]
    tabla_rot = (_tabla(["Fecha", "Municipio", "Tipo", "Dirección"],
                        filas_rot)
                 if filas_rot else "<p>No se reportaron roturas en la semana.</p>")

    filas_dist = filas_estado_sistema(res["distribucion"])
    extras = _tabla(["Estado", "Circuitos"], filas_dist, numericas={1})
    if res["mw"]:
        extras += (f'<p style="margin:8px 0 0;">Déficit de generación '
                   f"estimado: <strong>{_num(res['mw'])} MW</strong></p>")

    secciones = (
        _seccion("Resumen por municipio", tabla_resumen,
                 "Horas confirmadas de corte; % del tiempo con corriente sobre "
                 "168 h semanales por circuito. Ordenado por horas sin "
                 "corriente, de mayor a menor.")
        + (_seccion("Dónde se soporta el déficit de la capital",
                    render_grafico_deficit(res["grafico_deficit"], png),
                    "Imagen: todos los circuitos del catálogo, de mayor a "
                    "menor por horas confirmadas sin corriente; rojo = más "
                    "horas, azul = 0 h (siempre con corriente). Horas "
                    "confirmadas por parte oficial o señal vecinal.")
           if res.get("grafico_deficit") else "")
        + _seccion("Circuitos con más horas sin corriente", tabla_top_sin,
                   "Top 15 global del periodo, de mayor a menor; solo "
                   "circuitos con corte confirmado. El detalle por municipio "
                   "está en el resumen de arriba.")
        + _seccion("Circuitos con más horas con corriente", tabla_top_con,
                   "Top 15 global del periodo, de mayor a menor. Criterio: "
                   "solo circuitos con medición de horas en la semana; horas "
                   "con corriente = 168 h menos las confirmadas sin corriente. "
                   "Los circuitos sin registros no se computan.")
        + _seccion("Señales vecinales de la semana", cuerpo_sen,
                   "Circuitos con reporte o comentario de la población en los "
                   "últimos 7 días; influyen en las horas confirmadas y en el "
                   "estado del sistema.")
        + _seccion("Circuitos que no se están afectando", cuerpo_no,
                   "Criterio: sin horas de corte confirmadas en la semana y "
                   "sin caída confirmada — con servicio confirmado por parte "
                   "oficial, con servicio según reportes de vecinos, asumido "
                   "con corriente (más de una semana sin noticias) o "
                   "desconocido con más de 7 días de silencio.")
        + _seccion("Roturas de la semana",
                   p_rot + tabla_rot_resumen
                   + (f'<p style="font-size:13px;font-weight:bold;'
                      f'margin:16px 0 8px;color:#111;">Detalle por fecha</p>'
                      if filas_rot_resumen else "") + tabla_rot,
                   "Tipo según el parte original; dirección según el circuito "
                   "reportado en el parte. Detalle ordenado del más reciente "
                   "al más antiguo.")
        + _seccion("Estado del sistema", extras,
                   "Los circuitos sin noticias pasan a desconocido a las 48 "
                   "horas y a asumido con corriente tras una semana, hasta "
                   "que una noticia nueva los actualice.")
    )

    return (
        '<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"></head>'
        '<body style="margin:0;padding:16px;background:#f4f5f7;'
        'font-family:Arial,Helvetica,sans-serif;color:#1a1a1a;">'
        '<div style="max-width:720px;margin:0 auto;background:#ffffff;'
        'border:1px solid #e0e0e0;border-radius:8px;padding:24px;">'
        + encabezado + secciones +
        '<p style="font-size:11px;color:#999;margin:24px 0 0;">'
        "Mensaje automático del sistema de seguimiento del servicio eléctrico "
        "de La Habana.</p>"
        "</div></body></html>"
    )


def render_texto(res):
    """Alternativa en texto plano del mismo contenido (clientes sin HTML)."""
    gen, desde = _cuba(res["generado"]), _cuba(res["desde"])
    lineas = [
        "RESUMEN SEMANAL DEL SERVICIO ELÉCTRICO — LA HABANA",
        f"Del {desde.strftime('%d/%m')} al {gen.strftime('%d/%m')} · Generado "
        f"el {gen.strftime('%d/%m/%Y %H:%M')} (hora de Cuba)",
        "",
        # Introducción, antes de la primera tabla.
        INTRODUCCION,
        "",
        "RESUMEN POR MUNICIPIO (ordenado por horas sin corriente)",
        "Municipio · circuitos · afectados · horas sin corriente · horas con "
        "corriente · % con corriente",
    ]
    for m, f in res["filas"]:
        lineas.append(f"  {m}: {f['catalogados']:.0f} circuitos · "
                      f"{f['afectados']:.0f} afectados · {f['horas_sin']:.1f} h sin · "
                      f"{f['horas_con']:.1f} h con · {f['pct']:.1f} % con corriente")
    t = res["total"]
    lineas.append(f"  TOTAL LA HABANA: {t['catalogados']:.0f} circuitos · "
                  f"{t['afectados']:.0f} afectados · {t['horas_sin']:.1f} h sin · "
                  f"{t['horas_con']:.1f} h con · {t['pct']:.1f} % con corriente")

    # Gráfico de concentración del déficit, en barras ASCII (la imagen PNG del
    # HTML solo sustituye al gráfico del HTML): top 10 del bloque superior,
    # longitud proporcional a las horas del circuito respecto al más afectado,
    # techo de 30 caracteres.
    graf = res.get("grafico_deficit")
    if graf:
        lineas += ["", "DÓNDE SE SOPORTA EL DÉFICIT DE LA CAPITAL",
                   f"  El {int(graf['concentracion'])}% de las horas sin "
                   f"corriente confirmadas de la semana se concentró en solo "
                   f"{int(graf['top_n'])} circuitos."]
        mayor = graf["filas"][0]["horas"]
        for fila in graf["filas"][:TOP_GRAFICO]:
            etiqueta = _truncar(f"{fila['codigo']} · {fila['municipio']}")
            barra = "█" * max(1, int(round(
                fila["horas"] * _ANCHO_BARRA_TXT / mayor)))
            lineas.append(f"  {etiqueta:<28} {barra} {fila['horas']:.1f} h")
        lineas.append("  Horas sin corriente confirmadas por parte oficial o "
                      "señal vecinal; el resto del sistema absorbe el resto "
                      "del tiempo.")

    lineas += ["", "CIRCUITOS CON MÁS HORAS SIN CORRIENTE (top 15 global)"]
    if res["top_sin"]:
        for m, cod, hs, n in res["top_sin"]:
            lineas.append(f"  {m} · {cod}: {hs:.1f} h sin corriente · "
                          f"{n} parte{'s' if n != 1 else ''} que lo mencionaron")
    else:
        lineas.append("  No hubo circuitos afectados en la semana.")

    lineas += ["", "CIRCUITOS CON MÁS HORAS CON CORRIENTE (top 15 global)",
               "  Criterio: solo circuitos con medición de horas en la semana; "
               "horas con corriente = 168 h menos las confirmadas sin corriente."]
    if res["top_con"]:
        for m, cod, con, etq in res["top_con"]:
            lineas.append(f"  {m} · {cod}: {con:.1f} h con corriente · {etq}")
    else:
        lineas.append("  No hay mediciones suficientes para este ranking.")

    # Señales vecinales de la semana.
    sen = res["senales"]
    lineas += ["", "SEÑALES VECINALES DE LA SEMANA"]
    if sen["total"]:
        lineas.append(
            f"  Señales de la población (reportes y comentarios del grupo) "
            f"sobre {sen['total']} circuitos: {sen['sin_vecinos']} discrepan "
            f"con la parte oficial (vecinos sin corriente) y "
            f"{sen['con_vecinos']} confirman restablecimiento (vecinos con "
            f"corriente). Estos reportes alimentan las horas y los estados "
            f"del sistema de este resumen.")
        for m, n in sen["por_municipio"]:
            lineas.append(f"  {m}: {n} circuito{'s' if n != 1 else ''} con señal")
    else:
        lineas.append("  Sin señales de la población en la semana.")

    lineas += ["", "CIRCUITOS QUE NO SE ESTÁN AFECTANDO",
               "  Criterio: sin horas de corte confirmadas en la semana y sin "
               "caída confirmada — con servicio confirmado por parte oficial, "
               "con servicio según reportes de vecinos, asumido con corriente "
               "(más de una semana sin noticias) o desconocido con más de 7 "
               "días de silencio."]
    if res["no_afectados"]:
        for m, codigos in res["no_afectados"].items():
            lineas.append(f"  {m} ({len(codigos)}): {', '.join(codigos)}")
    else:
        lineas.append("  Sin circuitos en esa condición esta semana.")

    rot = res["roturas"]
    lineas += ["", "ROTURAS DE LA SEMANA",
               f"  En la semana hubo {rot['total']} roturas reportadas por la "
               f"UNE, distribuidas así:"]
    for (m, tipo), n in sorted(rot["conteo"].items(),
                               key=lambda kv: (-kv[1], kv[0][0], kv[0][1])):
        lineas.append(f"    {m} · {tipo}: {int(n)}")
    if rot["filas"]:
        lineas.append("  Detalle (fecha · municipio · tipo · dirección), del "
                      "más reciente al más antiguo:")
        for r in rot["filas"]:
            lineas.append(f"    {_cuba(r['fecha']).strftime('%d/%m %H:%M')} · "
                          f"{r['municipio']} · {r['tipo']} · {r['calles'] or '—'}")
    else:
        lineas.append("  No se reportaron roturas en la semana.")

    lineas += ["", "ESTADO DEL SISTEMA (ordenado por cantidad)"]
    for clave, cantidad in estado_sistema_ordenado(res["distribucion"]):
        lineas.append(f"  {ETIQUETAS_ESTADO_SISTEMA[clave]}: {cantidad}")
    if res["mw"]:
        lineas.append(f"  Déficit de generación estimado: {res['mw']:.0f} MW")
    lineas.append("  Nota: los circuitos sin noticias pasan a desconocido a "
                  "las 48 horas y a asumido con corriente tras una semana, "
                  "hasta que una noticia nueva los actualice.")
    lineas.append("")
    lineas.append("Mensaje automático del sistema de seguimiento del servicio "
                  "eléctrico de La Habana.")
    return "\n".join(lineas)


def asunto_de(res, prueba=False):
    base = (f"Resumen semanal del servicio eléctrico — La Habana "
            f"(del {_cuba(res['desde']).strftime('%d/%m')} "
            f"al {_cuba(res['generado']).strftime('%d/%m')})")
    return f"[PRUEBA] {base}" if prueba else base


# --------------------------------------------------------------------------
# Envío (Mailtrap) y entrada
# --------------------------------------------------------------------------

def enviar_mailtrap(asunto, cuerpo_html, cuerpo_texto, remitente, destinatario,
                    api_key, adjuntos=None):
    """Envía por la API de Mailtrap y devuelve el ID de envío. La clave solo
    viaja en el encabezado Authorization: jamás en el cuerpo, en los errores
    ni en la salida. `adjuntos` son attachments del formato de la API
    (filename, content base64, type, disposition, content_id)."""
    cuerpo = {
        "from": {"email": remitente, "name": NOMBRE_REMITENTE},
        "to": [{"email": destinatario}],
        "subject": asunto,
        "html": cuerpo_html,
        "text": cuerpo_texto,
    }
    if adjuntos:
        cuerpo["attachments"] = adjuntos
    peticion = urllib.request.Request(
        MAILTRAP_URL,
        data=json.dumps(cuerpo, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Cloudflare de send.api.mailtrap.io bloquea el UA por defecto de
            # urllib (error 1010): declaramos uno propio.
            "User-Agent": "resumen-electrico/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(peticion, timeout=30) as respuesta:
            crudo = respuesta.read().decode("utf-8") or "{}"
    except urllib.error.HTTPError:
        raise  # la procesa el llamador con el cuerpo, sin la clave
    try:
        datos = json.loads(crudo)
    except ValueError:
        raise RuntimeError(f"Mailtrap respondió algo no esperado: {crudo[:300]}")
    if not datos.get("success"):
        raise RuntimeError(f"Mailtrap respondió sin éxito: "
                           f"{json.dumps(datos, ensure_ascii=False)[:300]}")
    ids = datos.get("message_ids") or []
    return ids[0] if ids else "(sin id)"


def _previsualizar(asunto, cuerpo_html, cuerpo_texto, destinatario, remitente,
                   png=None):
    """--dry-run: escribe la vista previa (fuera del control de versiones) e
    imprime los datos del envío simulado + el comienzo del texto. NUNCA
    envía ni pide credenciales. El PNG del gráfico (si lo hay) va junto a la
    vista previa; si no lo hay, se limpia cualquier PNG viejo."""
    os.makedirs(os.path.dirname(PREVIEW_HTML), exist_ok=True)
    with open(PREVIEW_HTML, "w", encoding="utf-8") as f:
        f.write(cuerpo_html)
    with open(PREVIEW_TXT, "w", encoding="utf-8") as f:
        f.write(cuerpo_texto)
    if png:
        with open(PREVIEW_PNG, "wb") as f:
            f.write(png)
    elif os.path.exists(PREVIEW_PNG):
        os.remove(PREVIEW_PNG)
    print("Vista previa generada (sin envío):")
    print(f"  Asunto: {asunto}")
    print(f"  Destinatario (simulado): {destinatario}")
    print(f"  Remitente: {remitente}")
    print(f"  HTML: {PREVIEW_HTML} ({os.path.getsize(PREVIEW_HTML) / 1024:.1f} KB)")
    print(f"  Texto: {PREVIEW_TXT} ({os.path.getsize(PREVIEW_TXT) / 1024:.1f} KB)")
    if png:
        print(f"  Imagen: {PREVIEW_PNG} "
              f"({os.path.getsize(PREVIEW_PNG) / 1024:.1f} KB)")
    else:
        print("  Imagen: (sin PNG — matplotlib no disponible o semana sin "
              "cortes)")
    print()
    print("\n".join(cuerpo_texto.splitlines()[:24]))
    return 0


def _config_env():
    """(destinatario, remitente, prueba) desde el entorno. RESUMEN_TEST_PARA,
    si está definida, anula al destinatario y activa el modo prueba."""
    prueba = (os.environ.get("RESUMEN_TEST_PARA") or "").strip()
    destinatario = prueba or (os.environ.get("RESUMEN_DESTINATARIO") or "").strip() \
        or DESTINATARIO_DEFECTO
    remitente = (os.environ.get("RESUMEN_REMITENTE") or "").strip() \
        or REMITENTE_DEFECTO
    return destinatario, remitente, bool(prueba)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Resumen semanal del servicio eléctrico por email (Mailtrap)")
    parser.add_argument("--dry-run", action="store_true",
                        help="renderiza la vista previa SIN enviar (sin red, "
                             "sin credenciales)")
    args = parser.parse_args(argv)

    catalogo_doc = _leer_json(CATALOGO_FILE)
    catalogo = catalogo_doc.get("circuitos") or []
    horas = _leer_json(HORAS_FILE)
    estado = _leer_json(ESTADO_FILE)
    partes = _leer_json(PARTES_FILE)
    conteo_usuario = _leer_json(CONTEO_FILE, obligatorio=False)
    canal = (_leer_json(CANAL_FILE, obligatorio=False) or {}).get("filas") or {}
    if not catalogo:
        sys.exit(f"Error: {CATALOGO_FILE} no trae circuitos")

    res = agregar(catalogo_doc, horas, estado, partes, canal, conteo_usuario)
    # La imagen del gráfico se genera UNA vez: el mismo PNG va a la vista
    # previa, al attachment inline del envío y decide si el HTML muestra la
    # imagen (cid) o la nota textual (matplotlib ausente / semana sin cortes).
    png = generar_grafico_deficit(res.get("grafico_deficit"))
    cuerpo_html = render_html(res, png)
    cuerpo_texto = render_texto(res)
    destinatario, remitente, prueba = _config_env()
    asunto = asunto_de(res, prueba)

    if args.dry_run:
        return _previsualizar(asunto, cuerpo_html, cuerpo_texto,
                              destinatario, remitente, png)

    api_key = (os.environ.get("MAILTRAP_API_KEY") or "").strip()
    if not api_key:
        print("Error: falta MAILTRAP_API_KEY en el entorno — sin la clave no "
              "se envía nada (crea el secret del sistema de CI o expórtala "
              "localmente; jamás la escribas en archivos)", file=sys.stderr)
        return 1
    adjuntos = None
    if png:
        adjuntos = [{
            "filename": GRAFICO_FILENAME,
            "content": base64.b64encode(png).decode("ascii"),
            "type": "image/png",
            "disposition": "inline",
            "content_id": GRAFICO_CID,
        }]
    try:
        id_envio = enviar_mailtrap(asunto, cuerpo_html, cuerpo_texto,
                                   remitente, destinatario, api_key,
                                   adjuntos)
    except urllib.error.HTTPError as e:
        print(f"Error {e.code} de Mailtrap: "
              f"{e.read().decode('utf-8', 'replace')[:500]}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"Error de red al contactar Mailtrap: {e.reason}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"Enviado a {destinatario} — ID de envío: {id_envio}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
