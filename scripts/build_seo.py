"""Genera la superficie SEO estática del despliegue (nunca se commitea).

Lo ejecuta ingest.yml justo antes del deploy de wrangler con `|| echo`
(best-effort): si revienta, se despliega el último HTML bueno. Escribe en el
árbol de trabajo web/:

  - rellena la región <!-- SEO:HEAD:INICIO/FIN --> de las 7 páginas estáticas
    (canonical, Open Graph, Twitter, favicon, theme-color y JSON-LD del index,
    del hub y del FAQ), la región <!-- SEO:INICIO/FIN --> del body de index.html
    con una instantánea del estado, la del hub web/municipios/index.html con la
    grilla de tarjetas por municipio y la del FAQ
    web/preguntas-frecuentes/index.html con las preguntas frecuentes, y deja
    las regiones de HEAD de las 4 restantes;
  - genera web/municipio/<slug>/index.html para los 15 municipios;
  - emite web/sitemap.xml (paridad exacta con las páginas generadas) y
    web/robots.txt (esta última también vive commiteada; la corrida solo la
    reescribe si cambia SITE_BASE).

Decisiones fijadas por el spec:
  - SITE_BASE es la ÚNICA fuente del host absoluto: cambiarla aquí actualiza a
    la vez canonical, OG, sitemap y robots (dominio propio = una línea). Se lee
    en cada llamada, no se congela en cadenas de módulo.
  - Todo timestamp de la salida deriva de los JSON de entrada (estado.generado),
    nunca del reloj: dos corridas con los mismos datos producen bytes idénticos.
  - Hora de Cuba: offset fijo −4. Desde 2026 La Habana no atrasa el reloj, y es
    el mismo respaldo que usa build_analitica.py: sin depender de tzdata.
"""

import json
import math
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

RAIZ = os.path.join(os.path.dirname(__file__), "..")
WEB = os.path.join(RAIZ, "web")
DATOS = os.path.join(WEB, "data")

SITE_BASE = "https://apagones-habana.pages.dev"
HORA_CUBA = timezone(timedelta(hours=-4))  # La Habana, horario único desde 2026

# Pares de marcadores de las regiones inyectables (vacías en el git commiteado).
MARCA_INICIO = "<!-- SEO:INICIO -->"
MARCA_FIN = "<!-- SEO:FIN -->"
MARCA_HEAD_INICIO = "<!-- SEO:HEAD:INICIO -->"
MARCA_HEAD_FIN = "<!-- SEO:HEAD:FIN -->"

# Páginas estáticas commiteadas: archivo -> (ruta de URL, título, descripción).
# El título/descripción reales viven en el <head> de cada HTML; este mapa solo
# alimenta los tags absolutos (og:title/og:description) y testea la paridad.
PAGINAS = {
    "index.html": ("", "Apagones en La Habana hoy — estado y horario por municipio",
                   "Mapa y estado del servicio eléctrico en La Habana hoy: circuitos sin corriente según los partes de la Empresa Eléctrica (UNE), su horario y los apagones por municipio."),
    "analitica.html": ("analitica", "Horario de apagones en La Habana — análisis histórico",
                       "Análisis de los cortes eléctricos y apagones en La Habana: horario por hora del día, circuitos con más afectaciones, causas y municipios más golpeados."),
    "partes.html": ("partes", "Partes oficiales de apagones — UNE / La Habana hoy",
                    "Los partes oficiales de la Empresa Eléctrica de La Habana (UNE) sobre el corte de luz y el horario de afectaciones de hoy, en un solo lugar."),
    "circuitos.html": ("circuitos", "Circuitos eléctricos de La Habana sin servicio — causas",
                       "Catálogo de circuitos eléctricos de La Habana sin corriente: estado actual (sin servicio o restablecidos), calles que abarca, causa y horas afectadas."),
    "sugerencias.html": ("sugerencias", "Sugerencias y bugs — Apagones La Habana",
                         "Envía sugerencias de mejoras o reporta errores de la web de apagones en La Habana."),
    "municipios/index.html": ("municipios/", "Apagones por municipio en La Habana — los 15 municipios",
                              "Cuántos circuitos hay sin servicio en cada municipio de La Habana según el último parte, con enlace a la página de apagones de cada municipio y al mapa interactivo."),
    "preguntas-frecuentes/index.html": ("preguntas-frecuentes/",
                                        "Preguntas frecuentes sobre los apagones en La Habana — FAQ",
                                        "Respuestas a las preguntas frecuentes sobre los apagones en La Habana: qué es este sitio, de dónde salen los datos de la UNE, cómo ver si tu circuito está sin servicio, cada cuánto se actualiza y por qué hay cortes."),
}


def site_url(camino=""):
    """URL absoluta bajo SITE_BASE; el camino ya trae su forma final (con /)."""
    return SITE_BASE + ("/" + camino.lstrip("/") if camino else "/")


def slug(nombre):
    """slug determinista: minúsculas, acentos plegados, no alfanuméricos -> '-'."""
    plegado = unicodedata.normalize("NFD", nombre.strip().lower())
    plegado = "".join(c for c in plegado if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", plegado).strip("-")


def reescribir_region(texto, marca_ini, marca_fin, contenido):
    """Sustituye lo que hay entre los marcadores; idempotente sobre el archivo."""
    patron = re.compile(re.escape(marca_ini) + r".*?" + re.escape(marca_fin), re.DOTALL)
    nuevo = marca_ini + "\n" + contenido + "\n" + marca_fin
    salida, n = patron.subn(lambda _m: nuevo, texto)
    if n == 0:
        raise ValueError("faltan los marcadores %s / %s" % (marca_ini, marca_fin))
    return salida


def guion_ld(objeto):
    """Serializa JSON-LD seguro de incrustar en <script> (escape de '</')."""
    return json.dumps(objeto, ensure_ascii=False).replace("</", "<\\/")


def etiquetas_head(url_absoluta, titulo, descripcion, ld=None):
    """Tags SEO absolutos de una página; todo host sale de site_url()."""
    img = site_url("og.png")
    lineas = [
        '<link rel="canonical" href="%s">' % url_absoluta,
        '<meta property="og:type" content="website">',
        '<meta property="og:locale" content="es_CU">',
        '<meta property="og:site_name" content="Apagones La Habana">',
        '<meta property="og:title" content="%s">' % titulo,
        '<meta property="og:description" content="%s">' % descripcion,
        '<meta property="og:url" content="%s">' % url_absoluta,
        '<meta property="og:image" content="%s">' % img,
        '<meta name="twitter:card" content="summary_large_image">',
        '<meta name="twitter:title" content="%s">' % titulo,
        '<meta name="twitter:description" content="%s">' % descripcion,
        '<meta name="twitter:image" content="%s">' % img,
        '<link rel="icon" href="favicon.ico">',
        '<meta name="theme-color" content="#0c1322">',
    ]
    # og.png puede no existir en el despliegue: la etiqueta se queda válida e
    # inerte (los compartir solo no muestran imagen); nunca es error de build.
    if ld is not None:
        lineas.append('<script type="application/ld+json">%s</script>' % guion_ld(ld))
    return "\n".join(lineas)


def head_estaticas(archivo, nombres=None, generado=None):
    """Contenido que build_seo inyecta en la región HEAD de una página estática:
    los tags con host derivan de SITE_BASE; el <title> y la description viven a
    mano en el HTML (y la prueba de paridad evita deriva con el mapa PAGINAS)."""
    ruta, titulo, desc = PAGINAS[archivo]
    if archivo == "index.html":
        ld = ld_index(generado)
    elif archivo == "municipios/index.html":
        ld = ld_municipios(nombres or [])
    elif archivo == "preguntas-frecuentes/index.html":
        ld = ld_faq()
    else:
        ld = None
    return etiquetas_head(site_url(ruta), titulo, desc, ld=ld)


def ld_index(generado=None):
    """WebSite + Dataset + Organization del index (spec: todos presentes y
    siempre parseables). dateModified deriva del generado de entrada (nunca
    del reloj); sin fecha verificable el campo se omite en ambos bloques."""
    fecha = fecha_sitemap(generado)
    return [
        {"@context": "https://schema.org", "@type": "WebSite",
         "name": "Apagones La Habana", "inLanguage": "es",
         "url": site_url(""), "description": PAGINAS["index.html"][1],
         **({"dateModified": fecha} if fecha else {})},
        {"@context": "https://schema.org", "@type": "Dataset",
         "name": "Afectaciones del servicio eléctrico en La Habana",
         "description": "Series de partes oficiales de la UNE y estado por circuito, actualizadas cada ~25 minutos.",
         "inLanguage": "es", "isAccessibleForFree": True,
         **({"dateModified": fecha} if fecha else {}),
         "creator": {"@type": "Organization", "name": "Apagones La Habana (proyecto comunitario)"},
         "distribution": [
             {"@type": "DataDownload", "encodingFormat": "application/json", "contentUrl": site_url("data/estado.json")},
             {"@type": "DataDownload", "encodingFormat": "application/json", "contentUrl": site_url("data/circuitos.json")},
         ]},
        {"@context": "https://schema.org", "@type": "Organization",
         "name": "Apagones La Habana", "url": site_url(""),
         "logo": site_url("og.png"),
         "sameAs": ["https://t.me/EmpresaElectricaDeLaHabana"]},
    ]


def ld_municipio(nombre):
    """Bloque Service enlazado al index vía isPartOf (spec: JSON-LD del municipio)."""
    return {"@context": "https://schema.org", "@type": "Service",
            "name": "Estado del servicio eléctrico en %s" % nombre,
            "inLanguage": "es",
            "areaServed": {"@type": "AdministrativeArea", "name": nombre,
                           "containedInPlace": {"@type": "City", "name": "La Habana"}},
            "isPartOf": {"@type": "WebSite", "url": site_url("")}}


def ld_municipios(nombres):
    """ItemList de las 15 páginas de municipio para el head del hub."""
    return {"@context": "https://schema.org", "@type": "ItemList",
            "name": "Municipios de La Habana con apagones", "inLanguage": "es",
            "itemListElement": [
                {"@type": "ListItem", "position": i + 1,
                 "name": "Apagones en %s hoy" % n,
                 "url": site_url("municipio/%s/" % slug(n))}
                for i, n in enumerate(nombres)]}


def robots_txt():
    """Robots bajo SITE_BASE: permite las páginas, reserva /api/ para el worker."""
    return "\n".join([
        "User-agent: *",
        "Allow: /",
        "Disallow: /api/",
        "",
        "Sitemap: " + site_url("sitemap.xml"),
        "",
    ])


def sitemap_xml(pares):
    """sitemap de protocolo: <url><loc>[<lastmod>]</url> por par, orden estable.

    Cada elemento es un par (url, lastmod|None); el <lastmod> se emite solo
    cuando hay fecha. Se acepta también la URL a secas (equivale a None).
    """
    cuerpo = "".join(
        "  <url><loc>%s</loc>%s</url>\n"
        % (u, "<lastmod>%s</lastmod>" % f if f else "")
        for u, f in ((p if isinstance(p, tuple) else (p, None)) for p in pares))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + cuerpo + "</urlset>\n")


def _circ_sin(circ):
    return [c for c in (circ or {}).get("circuitos", []) if c.get("estado") == "sin servicio"]


def circuitos_del_municipio(nombre, circ):
    """Recorrido canónico de circuitos de un municipio: ÚNICA fuente para las
    cuentas de la página hija y de la tarjeta del hub (paridad por construcción)."""
    return [c for c in (circ or {}).get("circuitos", [])
            if nombre in (c.get("municipios") or
                          ([c["municipio"]] if c.get("municipio") else []))]


def conteo_municipio(nombre, circ):
    """(sin_servicio, total) de un municipio según el recorrido canónico."""
    del_muni = circuitos_del_municipio(nombre, circ)
    return sum(1 for c in del_muni if c.get("estado") == "sin servicio"), len(del_muni)


def esc_html(texto):
    """Escape mínimo del texto de datos que se interpola en las páginas."""
    return (str(texto).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def hora_estampado(generado):
    """'datos al HH:MM (UTC) · HH:MM hora de La Habana' desde estado.generado.

    Se deriva SOLO del JSON de entrada (nunca del reloj de la corrida) para que
    dos corridas con los mismos datos escriban bytes idénticos; un generado
    antiguo o inválido degrada a una marca legible sin abortar.
    """
    try:
        dt = datetime.fromisoformat(generado)
    except (TypeError, ValueError):
        return "datos al --:-- (sin marca de tiempo válida)"
    utc = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return "datos al %s (UTC) · %s hora de La Habana" % (
        utc.strftime("%H:%M"), utc.astimezone(HORA_CUBA).strftime("%H:%M"))


def fecha_sitemap(generado):
    """'YYYY-MM-DD' (UTC) desde estado.generado para el <lastmod> del sitemap.

    Se deriva SOLO del JSON de entrada (nunca del reloj): dos corridas con los
    mismos datos escriben el mismo lastmod. Un generado ausente o inválido
    devuelve None y la entrada sale sin <lastmod>, sin abortar la corrida.
    """
    try:
        dt = datetime.fromisoformat(generado)
    except (TypeError, ValueError):
        return None
    utc = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return utc.strftime("%Y-%m-%d")


def instantanea_index(estado, circ):
    """Contenido de la región del body de index: estado visible sin JS."""
    total = len((circ or {}).get("circuitos", []))
    sin = _circ_sin(circ)
    por_muni = {}
    for c in sin:
        for m in c.get("municipios") or ([c["municipio"]] if c.get("municipio") else []):
            por_muni[m] = por_muni.get(m, 0) + 1
    if not sin:
        parrafo = "<p>La ciudad aparece <b>sin afectaciones registradas</b> en el último parte.</p>"
        lista = ""
    else:
        parrafo = ("<p>Según el último parte, <b>%d de %d circuitos</b> "
                   "están sin servicio en La Habana.</p>" % (len(sin), total))
        orden = sorted(por_muni.items(), key=lambda kv: (-kv[1], slug(kv[0])))
        lista = "<ul>" + "".join(
            '<li><a href="/municipio/%s/">Apagones en %s hoy</a> — %d circuito(s) sin corriente</li>'
            % (slug(m), esc_html(m), n) for m, n in orden) + "</ul>"
    return ('<div id="seo-resumen">\n'
            "<h2>Apagones en La Habana hoy (instantánea del despliegue)</h2>\n"
            + parrafo + "\n" + lista + "\n"
            + '<p class="stamp">' + esc_html(hora_estampado(estado.get("generado"))) + "</p>\n"
            "</div>")


def nombres_de_geojson(dir_web):
    """Los 15 nombres canónicos: la autoridad es properties.municipio del geojson
    commiteado (web/data/municipios.geojson), que el pipeline usa literalmente."""
    try:
        geo = _cargar(os.path.join(dir_web, "data", "municipios.geojson"))
    except (OSError, ValueError):
        return None
    nombres = {(f.get("properties") or {}).get("municipio") for f in geo.get("features", [])}
    nombres = sorted((n for n in nombres if n), key=slug)
    return nombres or None


def municipios_de(estado, circ):
    """Respaldo de nombres: unión de los que aparecen en los JSON de pipeline,
    en orden estable por slug (se usa solo si falta el geojson canónico)."""
    nombres = set((estado or {}).get("municipios", {}))
    for c in (circ or {}).get("circuitos", []):
        nombres.update(c.get("municipios") or ([c["municipio"]] if c.get("municipio") else []))
    return sorted((n for n in nombres if n), key=slug)


def _hora_cuba(iso):
    """HH:MM fijo (-4, sin tzdata) de un timestamp ISO de los datos; '—' si no hay."""
    dt = _dt(iso)
    if dt is None:
        return "—"
    utc = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return utc.astimezone(HORA_CUBA).strftime("%H:%M")


def _dt(iso):
    """datetime desde ISO de los datos o None (nunca lanza; nunca reloj real)."""
    try:
        return datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


# Umbrales de vigencia compartidos con la regla del catálogo en web/app.js
# (circuitoVigente): el reloj del builder es estado.generado, no la hora local.
_UMBRAL_ND_H, _UMBRAL_ASUM_H = 24.0, 48.0
_ESTADO_FILA = {"sin": ("sin", "sin servicio"), "nd": ("nd", "sin noticias"),
                "con": ("con", "con servicio"), "asum": ("asum", "asumido")}
_GRUPO = {"sin": 0, "nd": 1, "con": 2, "asum": 3}


def _vigencia(c, gen):
    """Clasificación estática del circuito (sin/nd/con/asum) con la antigüedad
    medida contra `gen` (el generado de estado.json): sin noticias a 24 h,
    asumido-con-corriente (silencio = evidencia de retorno) a 48 h."""
    if c.get("estado") == "con servicio":
        return "con"
    if c.get("estado") != "sin servicio":
        return "asum"
    t = _dt(c.get("estado_fecha"))
    h = (gen - t).total_seconds() / 3600.0 if (gen and t) else 0.0
    if h > _UMBRAL_ASUM_H:
        return "asum"
    if h > _UMBRAL_ND_H:
        return "nd"
    return "sin"


def _nf_es(n):
    """Entero con separador de miles español: 71123 -> '71.123'."""
    return "{:,}".format(n).replace(",", ".")


def _estimado_afectados(nombre, estado, circ):
    """Personas sin corriente en el municipio con el MISMO método del header de
    la portada (resumenCircuitos en web/app.js): cifra oficial cuando el parte
    del Capitalino la trae; si no, fracción de circuitos no-nd del municipio ×
    su población (estado.poblacion_municipio, fuente única U-B), y promedio de
    ciudad cuando tiene menos de 2 circuitos atribuibles. El redondeo replica
    Math.round (floor(x + 0.5)). None = no estimable (sin población o sin datos).
    """
    p = ((estado or {}).get("poblacion_municipio") or {}).get(nombre)
    if not p:
        return None
    of = (estado or {}).get("poblacion") or {}
    if of.get("fuente") == "oficial" and of.get("sin_pct") is not None:
        return int(math.floor(p * of["sin_pct"] / 100.0 + 0.5))
    cat = (circ or {}).get("circuitos", [])
    if not cat:
        return None
    gen = _dt((estado or {}).get("generado"))
    nsin = sum(1 for c in cat if _vigencia(c, gen) == "sin")
    sin_city = nsin / float(len(cat))
    atribuibles = [c for c in circuitos_del_municipio(nombre, circ)
                   if _vigencia(c, gen) != "nd"]
    if len(atribuibles) >= 2:
        fraccion = sum(1 for c in atribuibles if _vigencia(c, gen) == "sin") / float(len(atribuibles))
    else:
        fraccion = sin_city
    return int(math.floor(fraccion * p + 0.5))


def _fecha_corta(iso):
    """'2026-01-10T09:00...' -> '10/01/2026' (formato corto español); '' si no hay."""
    s = (iso or "")[:10]
    return "%s/%s/%s" % (s[8:10], s[5:7], s[0:4]) if len(s) == 10 else ""


def reincidentes_circuitos(nombre, estado, circ):
    """Top 5 circuitos por `veces` (S13), desempate alfabético por código: cada
    fila dice «caído N veces desde <primera>» y, si el último parte del circuito
    tiene más de 24 h al momento del build, un aviso «sin noticias hace D días»
    con D = días completos (S14). La fecha de referencia es estado.generado: el
    builder no usa el reloj de la corrida."""
    del_muni = [c for c in circuitos_del_municipio(nombre, circ)
                if isinstance(c.get("veces"), int) and c["veces"] > 0]
    if not del_muni:
        return ""
    gen = _dt((estado or {}).get("generado"))
    top = sorted(del_muni, key=lambda c: (-c["veces"], c["codigo"]))[:5]
    filas = []
    for c in top:
        desde = " desde %s" % _fecha_corta(c.get("primera")) if c.get("primera") else ""
        aviso = ""
        t = _dt(c.get("estado_fecha"))
        if gen and t:
            horas = (gen - t).total_seconds() / 3600.0
            if horas > _UMBRAL_ND_H:
                dias = int(horas // 24)
                aviso = (' <span class="circ-b">sin noticias hace %s</span>'
                         % ("1 día" if dias == 1 else "%d días" % dias))
        filas.append('<li><a class="circ-cod" href="/circuitos?c=%s">%s</a>'
                     ' — caído %d veces%s%s</li>'
                     % (esc_html(c["codigo"]), esc_html(c["codigo"]), c["veces"], desde, aviso))
    return ('<h2>Circuitos más reincidentes</h2>\n<ul class="reinc">'
            + "".join(filas) + "</ul>")


def ranking_poblacion(nombre, estado, circ, nombres):
    """Líneas de contexto del municipio: puesto 'N de M' por circuitos sin
    servicio del parte (empates, orden alfabético por slug — S12) y el estimado
    de personas afectadas compartido con el header (S11)."""
    puestos = sorted(nombres, key=lambda n: (
        -sum(1 for c in circuitos_del_municipio(n, circ)
             if c.get("estado") == "sin servicio"), slug(n)))
    puesto = puestos.index(nombre) + 1
    lineas = ["<p>📊 <b>%d de %d municipios más afectados hoy</b>, según los "
              "circuitos sin servicio del último parte.</p>" % (puesto, len(nombres))]
    est = _estimado_afectados(nombre, estado, circ)
    if est is not None:
        lineas.append("<p>👥 ~%s personas sin corriente (estimado).</p>" % _nf_es(est))
    return "\n".join(lineas)


def _averias_por_municipio(analitica):
    """Averías agrupadas por municipio exacto desde analitica.json (`averias` =
    [fecha, tipo, municipio], una entrada por avería física). Las «sin ubicación»
    (municipio vacío) se excluyen por contrato; orden más-nuevo-primero."""
    por_muni = {}
    for a in (analitica or {}).get("averias") or []:
        if not isinstance(a, (list, tuple)) or len(a) < 3 or not a[2]:
            continue
        por_muni.setdefault(a[2], []).append(a)
    for filas in por_muni.values():
        filas.sort(key=lambda a: (a[0], a[1]), reverse=True)
    return por_muni


def _fecha_hora_av(iso):
    """'2026-07-03T15:10' (UTC truncado del canal) -> '03/07 11:10' La Habana."""
    s = iso or ""
    hora = _hora_cuba(s)
    if len(s) < 10 or hora == "—":
        return None
    return "%s/%s %s" % (s[8:10], s[5:7], hora)


def averias_municipio(nombre, averias):
    """Hasta 8 averías recientes del municipio (S15); si no hay, estado vacío
    explícito (S16) — nunca un hueco silencioso donde antes había rotación."""
    filas = []
    for a in ((averias or {}).get(nombre) or [])[:8]:  # S15: máximas 8, más nuevas
        fh = _fecha_hora_av(a[0])
        if fh:
            filas.append('<li class="av-fila">%s · %s</li>' % (fh, esc_html(a[1])))
    cuerpo = ("<ul>%s</ul>" % "".join(filas)) if filas else \
        "<p>Sin averías registradas en el último parte.</p>"
    return "<h2>Averías recientes</h2>\n" + cuerpo


def catalogo_circuitos(nombre, estado, circ):
    """Catálogo COMPLETO del municipio (reemplaza a la retirada rotación): todos
    sus circuitos con su estado vigente, causa y hora, en el recorrido canónico
    compartido con el hub (paridad de longitud por construcción). Orden: caídos
    (más nuevo antes) -> sin noticias -> con servicio -> asumidos."""
    filas_html = []
    del_muni = circuitos_del_municipio(nombre, circ)
    if not del_muni:
        return ('<h2>Catálogo completo de circuitos</h2>\n'
                '<p>Sin circuitos catalogados en el último parte.</p>')
    gen = _dt((estado or {}).get("generado"))
    grupos = {}
    for c in del_muni:
        grupos.setdefault(_GRUPO[_vigencia(c, gen)], []).append(c)
    for g in sorted(grupos):  # cada grupo, del más reciente al más antiguo
        for c in sorted(grupos[g], key=lambda c: (c.get("estado_fecha") or "", c["codigo"]),
                        reverse=True):
            clase, etiqueta = _ESTADO_FILA[_vigencia(c, gen)]
            causa = " · Causa: %s" % esc_html(c["causa"]) if c.get("causa") else ""
            hora = _hora_cuba(c.get("estado_fecha"))
            desde = " · desde %s (La Habana)" % hora if hora != "—" else ""
            filas_html.append(
                '<li class="circ-fila">'
                '<a class="circ-cod" href="/circuitos?c=%s">%s</a> '
                '<span class="circ-est %s">%s</span>%s%s</li>'
                % (esc_html(c["codigo"]), esc_html(c["codigo"]), clase, etiqueta,
                   causa, desde))
    return ('<h2>Catálogo completo de circuitos</h2>\n'
            '<ul class="circ-filas">' + "".join(filas_html) + "</ul>")


def region_hub(estado, circ, nombres):
    """Contenido de la región SEO:INICIO del hub: grilla .rc-card, una tarjeta
    por municipio con nombre, cuenta (recorrido compartido con la hija), enlace
    a /municipio/<slug>/ y deep link ?municipio= al mapa."""
    tarjetas = []
    for nombre in nombres:
        s = slug(nombre)
        sin_n, total_n = conteo_municipio(nombre, circ)
        clase = "rc-card" if sin_n else "rc-card sin-afect"
        tarjetas.append(
            '<div class="%s">\n'
            '<a class="rc-card-cab" href="/municipio/%s/">%s</a>\n'
            '<span class="rc-card-h">%d <small>de %d circuitos sin servicio</small></span>\n'
            '<a class="rc-card-det" href="/?municipio=%s">🗺️ Ver en el mapa</a>\n'
            "</div>" % (clase, s, esc_html(nombre), sin_n, total_n, quote(nombre)))
    return ('<h2>Apagones por municipio</h2>\n'
            '<div class="rc-cards">' + "\n".join(tarjetas) + "</div>\n"
            '<p class="stamp">Instantánea del despliegue — %s; el estado en vivo '
            'se ve al abrir cada página.</p>' % esc_html(hora_estampado(estado.get("generado"))))


# Nav canónica: orden y destinos únicos de los 7 destinos del sitio, compartida
# por las 8 superficies de render (6 raíces commiteadas, hub y páginas hijas).
DESTINOS_NAV = (
    ("", "🗺 Mapa"),
    ("analitica", "📊 Análisis"),
    ("partes", "📢 Partes"),
    ("circuitos", "🔌 Circuitos"),
    ("municipios/", "🏘️ Municipios"),
    ("preguntas-frecuentes/", "❓ Preguntas"),
    ("sugerencias", "💡 Sugerencias"),
)


def nav_tabs(activo):
    """La nav canónica con hrefs absolutos (única fuente de las páginas
    generadas); el destino `activo` se renderiza <span class="activo">."""
    piezas = []
    for destino, etiqueta in DESTINOS_NAV:
        if destino == activo:
            piezas.append('<span class="activo">%s</span>' % etiqueta)
        else:
            piezas.append('<a href="%s">%s</a>' % (site_url(destino), etiqueta))
    return '<nav class="tabs">%s</nav>' % " ".join(piezas)


def pagina_municipio(nombre, estado, circ, nombres, averias=None):
    """Página estática completa de un municipio (forma /municipio/<slug>/)."""
    s = slug(nombre)
    url = site_url("municipio/%s/" % s)
    stamp = hora_estampado(estado.get("generado"))
    del_muni = circuitos_del_municipio(nombre, circ)
    sin = [c for c in del_muni if c.get("estado") == "sin servicio"]
    titulo = "Apagones en %s hoy — horario y estado actual" % nombre
    descripcion = ("Estado de los apagones en %s (La Habana) hoy: circuitos sin servicio según "
                   "el último parte, con su causa y horario. Actualización: %s." % (nombre, stamp))
    if sin:
        parrafo_estado = ("<p><b>%d de %d circuitos</b> del municipio están sin servicio "
                          "según el último parte.</p>" % (len(sin), len(del_muni)))
    elif del_muni:
        parrafo_estado = ("<p>%s aparece <b>sin afectaciones registradas</b> en el último parte: "
                          "sus %d circuitos catalogados tienen servicio.</p>" % (esc_html(nombre), len(del_muni)))
    else:
        parrafo_estado = ("<p>%s aparece <b>sin afectaciones registradas</b> en el último parte; "
                          "aún no tiene circuitos catalogados.</p>"
                          % esc_html(nombre))
    # Listado en tarjetas .circ (vocabulario del sitio, nunca <table>): una por
    # circuito sin servicio, ordenada por antigüedad del estado (más nuevo antes).
    def _tarjeta(c):
        chips = ""
        if c.get("causa"):
            chips += '<span class="circ-b">Causa: %s</span>' % esc_html(c["causa"])
        hora = _hora_cuba(c.get("estado_fecha"))
        meta = ("desde %s (La Habana)" % hora) if hora != "—" else "hora sin publicar"
        return ('<article class="circ">\n'
                '<div class="circ-cab">'
                '<a class="circ-cod" href="/circuitos?c=%s">%s</a>'
                '<span class="circ-est sin">sin servicio</span>%s'
                '<span class="circ-meta">%s</span>'
                '</div>\n'
                '<div class="circ-calles">%s</div>\n'
                '</article>' % (esc_html(c["codigo"]), esc_html(c["codigo"]), chips,
                                esc_html(meta),
                                esc_html((c.get("calles") or "Sin información de calles")[:120])))
    listado = ("<h2>Circuitos sin servicio ahora</h2>\n"
               + "".join(_tarjeta(c) for c in sorted(
                     sin, key=lambda c: (c.get("estado_fecha") or "", c["codigo"]), reverse=True))
               ) if sin else ""
    eventos = sorted((estado.get("municipios", {}).get(nombre) or {}).get("eventos", []),
                     key=lambda e: e.get("fecha") or "", reverse=True)[:8]
    icono = {"afectacion": "🔴", "restablecimiento": "✅"}
    hist = "".join(
        "<li>%s %s · %s · %s%s</li>" % (icono.get(e.get("tipo"), "•"), esc_html(e.get("tipo") or ""),
                                        esc_html(e.get("causa") or "—"), _hora_cuba(e.get("fecha")),
                                        (" — " + esc_html(" · ".join(e.get("zonas") or []))) if e.get("zonas") else "")
        for e in eventos)
    historial = ("<h2>Historial reciente (partes)</h2><ul>%s</ul>" % hist) if eventos else ""
    cuerpo = "\n".join(filter(None, [
        parrafo_estado, ranking_poblacion(nombre, estado, circ, nombres),
        listado, catalogo_circuitos(nombre, estado, circ),
        reincidentes_circuitos(nombre, estado, circ),
        averias_municipio(nombre, averias),
        '<p><a href="/?municipio=%s">Ver %s en el mapa interactivo</a></p>' % (quote(nombre), esc_html(nombre)),
        historial,
        '<p class="stamp">Instantánea del despliegue — %s; el mapa muestra el estado en vivo al cargar.</p>'
        % esc_html(stamp)]))
    return ("<!DOCTYPE html>\n<html lang=\"es\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "<title>%s</title>\n<meta name=\"description\" content=\"%s\">\n"
            "<link rel=\"stylesheet\" href=\"/style.css\">\n%s\n</head>\n<body>\n"
            "<header><h1>\u26a1 Apagones en %s hoy</h1>\n%s</header>\n"
            "<main class=\"pagina-municipio\">\n%s\n</main>\n"
            "<footer><p>Fuente: canal de Telegram de la <a href=\"https://t.me/EmpresaElectricaDeLaHabana\">"
            "Empresa Eléctrica de La Habana</a> y comentarios de usuarios. Datos no oficiales, "
            "pueden contener errores.</p></footer>\n</body>\n</html>\n"
            % (esc_html(titulo), esc_html(descripcion),
               etiquetas_head(url, titulo, descripcion, ld=ld_municipio(nombre)),
               esc_html(nombre), nav_tabs("municipios/"), cuerpo))


def urls_del_sitemap(nombres, generado=None):
    """Las 21 URLs canónicas (6 páginas + 15 municipios) como pares
    (url, lastmod): el lastmod es la fecha UTC del generado de entrada
    (nunca del reloj) y None cuando no hay fecha verificable."""
    ultima = fecha_sitemap(generado)
    pares = [(site_url(p[0]), ultima) for p in PAGINAS.values()]
    pares += [(site_url("municipio/%s/" % slug(n)), ultima) for n in nombres]
    return pares


# Las 8 preguntas frecuentes aprobadas (evergreen: sin fecha, sin estado
# vigente, sin códigos de circuito del día). Es la ÚNICA fuente del cuerpo del
# FAQ y del JSON-LD FAQPage: paridad por construcción (decisión D4 del design).
# Las respuestas admiten HTML de confianza mínimo con enlaces extensionless.
FAQ_PREGUNTAS = (
    ("¿Qué es Apagones La Habana?",
     "Es un sitio comunitario e independiente que reúne en un solo lugar la información pública sobre los apagones "
     "en La Habana: el estado por circuito según el último parte, los partes oficiales de la UNE y un análisis del "
     "horario de las afectaciones. No es un sitio oficial ni está afiliado a la Empresa Eléctrica de La Habana."),
    ("¿Son los datos oficiales?",
     "Los datos provienen del canal público de Telegram de la <a href=\"https://t.me/EmpresaElectricaDeLaHabana\">"
     "Empresa Eléctrica de La Habana</a> y se procesan de forma automatizada. No son una fuente oficial: pueden "
     "contener errores u omisiones, y el <a href=\"/partes\">detalle de cada parte</a> permite verificar el anuncio "
     "original."),
    ("¿Cómo sé si mi circuito está sin servicio hoy?",
     "En la pestaña de <a href=\"/circuitos\">circuitos</a> puedes buscar tu circuito por código, calle o municipio "
     "y ver su estado vigente. También puedes entrar a la página de tu <a href=\"/municipios/\">municipio</a> o "
     "buscar tu calle directamente en el <a href=\"/\">mapa</a>."),
    ("¿Cada cuánto se actualiza?",
     "El estado se actualiza de forma automática con cada nuevo parte publicado por la UNE (en general, cada pocos "
     "minutos). La hora de la última actualización aparece en la portada y en cada página de municipio."),
    ('¿Qué significan "afectación" y "restablecimiento"?',
     "Una afectación es un corte del servicio eléctrico que impide el suministro normal en un circuito; un "
     "restablecimiento es el retorno del servicio después de ese corte. Los partes de la UNE anuncian ambos eventos "
     "con su causa y las zonas involucradas."),
    ("¿Por qué mi circuito no aparece?",
     "El sitio solo publica lo que declaran los partes oficiales: si tu circuito no fue mencionado en el último "
     "parte, o aún no está catalogado en el sistema, no puede mostrarse su estado. Si detectas un circuito que "
     "falta, puedes reportarlo desde la página de sugerencias."),
    ("¿Puedo aportar información?",
     "Sí. La página de <a href=\"/sugerencias\">sugerencias</a> recibe correcciones de datos, circuitos sin "
     "catalogar, reportes de errores y propuestas de mejora del sitio."),
    ("¿Por qué hay apagones en La Habana?",
     "Los apagones responden a la situación del sistema eléctrico nacional: déficit de generación, averías en las "
     "plantas y fallas de transmisión o distribución. Este sitio no determina las causas: publica el horario y los "
     "circuitos que anuncia la UNE, y el <a href=\"/analitica\">análisis histórico</a> muestra los patrones por hora "
     "y municipio."),
)


def cuerpo_faq():
    """Cuerpo del FAQ: las 8 preguntas evergreen de FAQ_PREGUNTAS (única fuente
    compartida con el JSON-LD FAQPage — paridad por construcción)."""
    secciones = []
    for i, (pregunta, respuesta) in enumerate(FAQ_PREGUNTAS, 1):
        secciones.append('<section class="faq" id="p%d">\n<h2>%s</h2>\n%s\n</section>'
                         % (i, esc_html(pregunta), respuesta))
    return "\n".join(secciones)


def ld_faq():
    """FAQPage JSON-LD del FAQ: mainEntity sale de FAQ_PREGUNTAS, la misma
    fuente del cuerpo (spec R-faqpage: cero expectativa de rich results en
    Google; el valor es la coincidencia de contenido y otros consumidores)."""
    return {"@context": "https://schema.org", "@type": "FAQPage",
            "inLanguage": "es",
            "mainEntity": [{"@type": "Question", "name": pregunta,
                            "acceptedAnswer": {"@type": "Answer", "text": respuesta}}
                           for pregunta, respuesta in FAQ_PREGUNTAS]}


# Regiones del body que generar() rellena por archivo (las no listadas solo
# reciben su HEAD). Las dos primeras comparten firma (estado, circ, nombres).
REGIONES_CUERPO = {
    "index.html": lambda estado, circ, nombres: instantanea_index(estado, circ),
    "municipios/index.html": region_hub,
    "preguntas-frecuentes/index.html": lambda estado, circ, nombres: cuerpo_faq(),
}


def generar(dir_web, datos):
    """Regenera la superficie SEO dentro de dir_web a partir de los JSON.

    Idempotente por construcción: todo byte sale de `datos` (ni reloj ni
    aleatoriedad), así que dos corridas con las mismas entradas dan las mismas
    salidas. Nunca se commitea lo que escribe aquí.
    """
    estado, circ = datos
    nombres = nombres_de_geojson(dir_web) or municipios_de(estado, circ)
    try:
        averias = _averias_por_municipio(_cargar(os.path.join(dir_web, "data", "analitica.json")))
    except (OSError, ValueError):
        averias = {}  # sin histórico (p. ej. árbol de prueba sin analitica): estados vacíos
    for archivo in PAGINAS:
        ruta = os.path.join(dir_web, archivo)
        with open(ruta, encoding="utf-8") as f:
            texto = f.read()
        texto = reescribir_region(texto, MARCA_HEAD_INICIO, MARCA_HEAD_FIN,
                                  head_estaticas(archivo, nombres,
                                                 estado.get("generado")))
        relleno = REGIONES_CUERPO.get(archivo)
        if relleno is not None:
            texto = reescribir_region(texto, MARCA_INICIO, MARCA_FIN,
                                      relleno(estado, circ, nombres))
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(texto)
    for nombre in nombres:
        destino = os.path.join(dir_web, "municipio", slug(nombre))
        os.makedirs(destino, exist_ok=True)
        with open(os.path.join(destino, "index.html"), "w", encoding="utf-8") as f:
            f.write(pagina_municipio(nombre, estado, circ, nombres, averias))
    # Endpoints de rastreo. robots.txt vive TAMBIÉN commiteado (si esta corrida
    # revienta, el deploy del último-good conserva reglas); el re-emitir aquí
    # solo lo alinea cuando SITE_BASE cambió. sitemap.xml es 100% generado.
    with open(os.path.join(dir_web, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write(sitemap_xml(urls_del_sitemap(nombres, estado.get("generado"))))
    with open(os.path.join(dir_web, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(robots_txt())


def _cargar(ruta):
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def main():
    datos = (_cargar(os.path.join(DATOS, "estado.json")),
             _cargar(os.path.join(DATOS, "circuitos.json")))
    generar(WEB, datos)
    print("SEO regenerado en el árbol de trabajo (nada de esto se commitea)")


if __name__ == "__main__":
    main()
