"""Resumen semanal del servicio eléctrico — La Habana, por email (Mailtrap).

Lee los JSON que el pipeline deja en el árbol (catálogo de circuitos,
histórico de horas confirmadas, estado del sistema, partes clasificados y
señales de usuario), agrega los últimos 7 días calendario y envía el resumen
por la API de Mailtrap (HTML + texto plano). Todo el cálculo es OFFLINE y
determinista — el reloj es el sello `generado` de los datos, nunca el reloj
de la corrida —; la red solo se usa para el envío final.

La clasificación de ciclo de vida (sin/con_vecinos/desconocido/con/asum)
reusa la MISMA regla que publica el sitio: importa build_seo (biblioteca
estándar pura, importable sin efectos) y llama a su `_vigencia`, para que el
email y la web nunca diverjan. El flag `reportado_con` que esa regla lee se
monta aquí con un espejo exacto de la regla de build_circuitos.py (no se
importa ese módulo porque arrastra dependencias de red que no existen en el
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
"""

import argparse
import html
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
# publica el sitio; orden de gravedad).
ETIQUETAS_VIGENCIA = (
    ("sin", "sin servicio"),
    ("con_vecinos", "con servicio (según vecinos)"),
    ("desconocido", "estado desconocido"),
    ("con", "con servicio"),
    ("asum", "asumido"),
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


def top_sin_por_municipio(catalogo, horas, dia0, dia1, menciones, top=3):
    """Municipio → top N de circuitos con MÁS horas confirmadas sin corriente
    en la ventana (solo los que registran corte), con horas y # de partes que
    lo mencionaron en la semana."""
    res = {}
    for c in catalogo:
        m = municipio_de(c)
        cod = c.get("codigo")
        if not m or not cod:
            continue
        hs = horas_sin_ventana(horas, cod, dia0, dia1)
        if hs <= 0:
            continue
        res.setdefault(m, []).append((cod, hs, menciones.get(cod, 0)))
    for m, lst in res.items():
        lst.sort(key=lambda t: (-t[1], t[0]))
        res[m] = lst[:top]
    return {m: res[m] for m in sorted(res)}


def top_con_por_municipio(catalogo, horas, dia0, dia1, top=3):
    """Municipio → top N de circuitos con MÁS horas con corriente en la
    ventana, ENTRE LOS QUE TIENEN MEDICIÓN (criterio: un circuito sin
    registros de horas no prueba corriente constante, no entra). Horas con
    corriente = 168 h menos las confirmadas sin corriente, piso 0."""
    res = {}
    for c in catalogo:
        m = municipio_de(c)
        cod = c.get("codigo")
        if not m or not cod or not con_datos_horas(horas, cod):
            continue
        con = max(0.0, HORAS_SEMANA - horas_sin_ventana(horas, cod, dia0, dia1))
        res.setdefault(m, []).append((cod, con))
    for m, lst in res.items():
        lst.sort(key=lambda t: (-t[1], t[0]))
        res[m] = lst[:top]
    return {m: res[m] for m in sorted(res)}


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
    """Circuitos por estado del ciclo de vida, en el orden de gravedad."""
    vigencias = vigencias_de(catalogo, gen, evento_nacional)
    conteo = Counter(vigencias.values())
    return {clave: conteo.get(clave, 0) for clave, _ in ETIQUETAS_VIGENCIA}


def no_afectados_por_municipio(catalogo, horas, vigencias, dia0, dia1):
    """Municipio → códigos que NO se están afectando: sin una sola hora de
    corte registrada en la ventana Y vigencia 'con' (con servicio
    confirmado). Los desconocidos, asumidos y con_vecinos quedan fuera — de
    ellos no sabemos — igual que los 'sin' y los que registran horas."""
    res = {}
    for c in catalogo:
        m = municipio_de(c)
        cod = c.get("codigo")
        if not m or not cod or vigencias.get(cod) != "con":
            continue
        if horas_sin_ventana(horas, cod, dia0, dia1) > 0:
            continue
        res.setdefault(m, []).append(cod)
    for lst in res.values():
        lst.sort()
    return {m: res[m] for m in sorted(res)}


def roturas_ventana(partes, canal, desde, hasta, nombres_municipio=None):
    """Partes de avería de la ventana: total de PARTES, conteo por
    (municipio, tipo clasificado del texto original) y una fila por circuito
    del parte (fecha, municipio, tipo, dirección `calles` del parte), en
    orden cronológico. El tipo sale del texto del canal por message_id; si
    el mensaje ya no está en el caché, 'Otra avería'. El municipio del parte
    se mapea al nombre canónico del catálogo (`nombres_municipio`)."""
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
    filas.sort(key=lambda r: (r["fecha"], r["municipio"], r["tipo"]))
    return {"total": total, "conteo": conteo, "filas": filas}


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
        "top_sin": top_sin_por_municipio(catalogo, horas, dia0, dia1,
                                         menciones_ventana(partes, desde, gen)),
        "top_con": top_con_por_municipio(catalogo, horas, dia0, dia1),
        "no_afectados": no_afectados_por_municipio(catalogo, horas, vigencias,
                                                   dia0, dia1),
        "roturas": roturas_ventana(partes, canal, desde, gen, nombres_municipio),
        "distribucion": distribucion_vigencia(catalogo, gen, evento),
        "mw": mw if isinstance(mw, (int, float)) and mw > 0 else None,
    }


# --------------------------------------------------------------------------
# Render (HTML con estilos inline + texto plano)
# --------------------------------------------------------------------------

_T_TABLE = "border-collapse:collapse;width:100%;font-size:13px;"
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
    return (f'<table style="{_T_TABLE}">'
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


def render_html(res):
    """Correo en HTML: tablas simples con estilos inline, sin JavaScript, sin
    imágenes externas. Cero referencias fuera del propio contenido."""
    gen, desde = _cuba(res["generado"]), _cuba(res["desde"])
    encabezado = (
        '<h1 style="font-size:20px;margin:0 0 4px;color:#111;">'
        "Resumen semanal del servicio eléctrico — La Habana</h1>"
        f'<p style="color:#555;font-size:13px;margin:0 0 8px;">'
        f"Del {desde.strftime('%d/%m')} al {gen.strftime('%d/%m')} · "
        f"Generado el {gen.strftime('%d/%m/%Y %H:%M')} (hora de Cuba)</p>"
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

    filas_top = []
    for m, lst in res["top_sin"].items():
        for cod, hs, menciones in lst:
            filas_top.append([html.escape(m), html.escape(cod),
                              _num(hs), _ent(menciones)])
    tabla_top_sin = (_tabla(["Municipio", "Circuito", "Horas sin corriente",
                             "Partes que lo mencionaron"],
                            filas_top, numericas={2, 3})
                     if filas_top else "<p>No hubo circuitos afectados en la semana.</p>")

    filas_top_con = []
    for m, lst in res["top_con"].items():
        for cod, con in lst:
            filas_top_con.append([html.escape(m), html.escape(cod), _num(con)])
    tabla_top_con = (_tabla(["Municipio", "Circuito", "Horas con corriente"],
                            filas_top_con, numericas={2})
                     if filas_top_con else
                     "<p>No hay mediciones suficientes para este ranking.</p>")

    parrafos_no = []
    for m, codigos in res["no_afectados"].items():
        parrafos_no.append(
            f"<p><strong>{html.escape(m)}</strong> ({len(codigos)}): "
            f"{html.escape(', '.join(codigos))}</p>")
    cuerpo_no = ("".join(parrafos_no) if parrafos_no
                 else "<p>Sin circuitos en esa condición esta semana.</p>")

    rot = res["roturas"]
    filas_rot = [
        [html.escape(_cuba(r["fecha"]).strftime("%d/%m %H:%M")),
         html.escape(r["municipio"]), html.escape(r["tipo"]),
         html.escape(r["calles"] or "—")]
        for r in rot["filas"]
    ]
    tabla_rot = (_tabla(["Fecha", "Municipio", "Tipo", "Dirección"],
                        filas_rot)
                 if filas_rot else "<p>No se reportaron roturas en la semana.</p>")
    conteo_rot = ", ".join(
        f"{html.escape(m)} — {html.escape(tipo)}: {_num(n)}"
        for (m, tipo), n in sorted(rot["conteo"].items()))
    p_rot = (f'<p style="margin:0 0 8px;">Partes de avería en la semana: '
             f"<strong>{_ent(rot['total'])}</strong>"
             + (f" · {conteo_rot}" if conteo_rot else "") + "</p>")

    filas_dist = [
        [html.escape(etiqueta), _ent(res["distribucion"].get(clave, 0))]
        for clave, etiqueta in ETIQUETAS_VIGENCIA
    ]
    extras = _tabla(["Estado del ciclo de vida", "Circuitos"],
                    filas_dist, numericas={1})
    if res["mw"]:
        extras += (f'<p style="margin:8px 0 0;">Déficit de generación '
                   f"estimado: <strong>{_num(res['mw'])} MW</strong></p>")

    secciones = (
        _seccion("Resumen por municipio", tabla_resumen,
                 "Horas confirmadas de corte; % del tiempo con corriente sobre "
                 "168 h semanales por circuito.")
        + _seccion("Circuitos con más horas sin corriente, por municipio",
                   tabla_top_sin,
                   "Top 3 por municipio dentro del periodo; solo circuitos con "
                   "corte confirmado.")
        + _seccion("Circuitos con más horas con corriente, por municipio",
                   tabla_top_con,
                   "Criterio: solo circuitos con medición de horas en la semana; "
                   "horas con corriente = 168 h menos las confirmadas sin "
                   "corriente. Los circuitos sin registros no se computan.")
        + _seccion("Circuitos que no se están afectando", cuerpo_no,
                   "Sin horas de corte en la semana y con servicio confirmado; "
                   "los circuitos en estado desconocido o asumido se omiten por "
                   "falta de certeza.")
        + _seccion("Roturas de la semana", p_rot + tabla_rot,
                   "Tipo según el parte original; dirección según el circuito "
                   "reportado en el parte.")
        + _seccion("Estado del sistema", extras,
                   "Las horas corresponden a cortes confirmados por parte "
                   "oficial.")
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
        "RESUMEN POR MUNICIPIO",
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

    lineas += ["", "CIRCUITOS CON MÁS HORAS SIN CORRIENTE, POR MUNICIPIO (top 3)"]
    if res["top_sin"]:
        for m, lst in res["top_sin"].items():
            detalle = " · ".join(
                f"{cod} ({hs:.1f} h, {n} parte{'s' if n != 1 else ''})"
                for cod, hs, n in lst)
            lineas.append(f"  {m}: {detalle}")
    else:
        lineas.append("  No hubo circuitos afectados en la semana.")

    lineas += ["", "CIRCUITOS CON MÁS HORAS CON CORRIENTE, POR MUNICIPIO (top 3)",
               "  Criterio: solo circuitos con medición de horas en la semana; "
               "horas con corriente = 168 h menos las confirmadas sin corriente."]
    if res["top_con"]:
        for m, lst in res["top_con"].items():
            detalle = " · ".join(f"{cod} ({con:.1f} h)" for cod, con in lst)
            lineas.append(f"  {m}: {detalle}")
    else:
        lineas.append("  No hay mediciones suficientes para este ranking.")

    lineas += ["", "CIRCUITOS QUE NO SE ESTÁN AFECTANDO",
               "  Sin horas de corte en la semana y con servicio confirmado; los "
               "desconocidos o asumidos se omiten por falta de certeza."]
    if res["no_afectados"]:
        for m, codigos in res["no_afectados"].items():
            lineas.append(f"  {m} ({len(codigos)}): {', '.join(codigos)}")
    else:
        lineas.append("  Sin circuitos en esa condición esta semana.")

    rot = res["roturas"]
    lineas += ["", "ROTURAS DE LA SEMANA",
               f"  Partes de avería: {rot['total']}"]
    if rot["conteo"]:
        lineas.append("  Por municipio y tipo: " + "; ".join(
            f"{m} — {tipo}: {n}" for (m, tipo), n in sorted(rot["conteo"].items())))
    if rot["filas"]:
        lineas.append("  Detalle (fecha · municipio · tipo · dirección):")
        for r in rot["filas"]:
            lineas.append(f"    {_cuba(r['fecha']).strftime('%d/%m %H:%M')} · "
                          f"{r['municipio']} · {r['tipo']} · {r['calles'] or '—'}")

    lineas += ["", "ESTADO DEL SISTEMA"]
    lineas.append("  " + " · ".join(
        f"{etiqueta}: {res['distribucion'].get(clave, 0)}"
        for clave, etiqueta in ETIQUETAS_VIGENCIA))
    if res["mw"]:
        lineas.append(f"  Déficit de generación estimado: {res['mw']:.0f} MW")
    lineas.append("  Nota: las horas corresponden a cortes confirmados por parte "
                  "oficial; los porcentajes se calculan sobre 168 h semanales "
                  "por circuito.")
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
                    api_key):
    """Envía por la API de Mailtrap y devuelve el ID de envío. La clave solo
    viaja en el encabezado Authorization: jamás en el cuerpo, en los errores
    ni en la salida."""
    cuerpo = {
        "from": {"email": remitente, "name": NOMBRE_REMITENTE},
        "to": [{"email": destinatario}],
        "subject": asunto,
        "html": cuerpo_html,
        "text": cuerpo_texto,
    }
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


def _previsualizar(asunto, cuerpo_html, cuerpo_texto, destinatario, remitente):
    """--dry-run: escribe la vista previa (fuera del control de versiones) e
    imprime los datos del envío simulado + el comienzo del texto. NUNCA
    envía ni pide credenciales."""
    os.makedirs(os.path.dirname(PREVIEW_HTML), exist_ok=True)
    with open(PREVIEW_HTML, "w", encoding="utf-8") as f:
        f.write(cuerpo_html)
    with open(PREVIEW_TXT, "w", encoding="utf-8") as f:
        f.write(cuerpo_texto)
    print("Vista previa generada (sin envío):")
    print(f"  Asunto: {asunto}")
    print(f"  Destinatario (simulado): {destinatario}")
    print(f"  Remitente: {remitente}")
    print(f"  HTML: {PREVIEW_HTML} ({os.path.getsize(PREVIEW_HTML) / 1024:.1f} KB)")
    print(f"  Texto: {PREVIEW_TXT} ({os.path.getsize(PREVIEW_TXT) / 1024:.1f} KB)")
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
    cuerpo_html = render_html(res)
    cuerpo_texto = render_texto(res)
    destinatario, remitente, prueba = _config_env()
    asunto = asunto_de(res, prueba)

    if args.dry_run:
        return _previsualizar(asunto, cuerpo_html, cuerpo_texto,
                              destinatario, remitente)

    api_key = (os.environ.get("MAILTRAP_API_KEY") or "").strip()
    if not api_key:
        print("Error: falta MAILTRAP_API_KEY en el entorno — sin la clave no "
              "se envía nada (crea el secret del sistema de CI o expórtala "
              "localmente; jamás la escribas en archivos)", file=sys.stderr)
        return 1
    try:
        id_envio = enviar_mailtrap(asunto, cuerpo_html, cuerpo_texto,
                                   remitente, destinatario, api_key)
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
