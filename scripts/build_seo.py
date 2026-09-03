"""Genera la superficie SEO estática del despliegue (nunca se commitea).

Lo ejecuta ingest.yml justo antes del deploy de wrangler con `|| echo`
(best-effort): si revienta, se despliega el último HTML bueno. Escribe en el
árbol de trabajo web/:

  - rellena la región <!-- SEO:HEAD:INICIO/FIN --> de las 6 páginas estáticas
    (canonical, Open Graph, Twitter, favicon, theme-color y JSON-LD del index y
    del hub), la región <!-- SEO:INICIO/FIN --> del body de index.html con una
    instantánea del estado y la del hub web/municipios/index.html con la grilla
    de tarjetas por municipio, y deja las regiones de HEAD de las 4 restantes;
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
                   "Mapa y estado del servicio eléctrico en La Habana hoy: circuitos sin corriente según los partes de la Empresa Eléctrica (UNE), horarios de rotación por bloque y apagones por municipio."),
    "analitica.html": ("analitica.html", "Horario de apagones en La Habana — análisis histórico",
                       "Análisis de los apagones en La Habana: horario de los cortes por hora del día, bloques más afectados, causas, averías y municipios con más afectaciones."),
    "partes.html": ("partes.html", "Partes oficiales de apagones — UNE / La Habana hoy",
                    "Los partes oficiales de la Empresa Eléctrica de La Habana (UNE) sobre apagones y horario de afectaciones de hoy, en un solo lugar."),
    "circuitos.html": ("circuitos.html", "Circuitos eléctricos de La Habana sin servicio — causas",
                       "Catálogo de circuitos eléctricos de La Habana con su estado actual (sin servicio o restablecidos), calles que abarca, causa y horas afectadas."),
    "sugerencias.html": ("sugerencias.html", "Sugerencias y bugs — Apagones La Habana",
                         "Envía sugerencias de mejoras o reporta errores de la web de apagones en La Habana."),
    "municipios/index.html": ("municipios/", "Apagones por municipio en La Habana — los 15 municipios",
                              "Cuántos circuitos hay sin servicio en cada municipio de La Habana según el último parte, con enlace a la página de apagones de cada municipio y al mapa interactivo."),
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
        '<meta name="theme-color" content="#10141a">',
    ]
    # og.png puede no existir en el despliegue: la etiqueta se queda válida e
    # inerte (los compartir solo no muestran imagen); nunca es error de build.
    if ld is not None:
        lineas.append('<script type="application/ld+json">%s</script>' % guion_ld(ld))
    return "\n".join(lineas)


def head_estaticas(archivo, nombres=None):
    """Contenido que build_seo inyecta en la región HEAD de una página estática:
    los tags con host derivan de SITE_BASE; el <title> y la description viven a
    mano en el HTML (y la prueba de paridad evita deriva con el mapa PAGINAS)."""
    ruta, titulo, desc = PAGINAS[archivo]
    if archivo == "index.html":
        ld = ld_index()
    elif archivo == "municipios/index.html":
        ld = ld_municipios(nombres or [])
    else:
        ld = None
    return etiquetas_head(site_url(ruta), titulo, desc, ld=ld)


def ld_index():
    """WebSite + Dataset del index (spec: ambos presentes y siempre parseables)."""
    return [
        {"@context": "https://schema.org", "@type": "WebSite",
         "name": "Apagones La Habana", "inLanguage": "es",
         "url": site_url(""), "description": PAGINAS["index.html"][1]},
        {"@context": "https://schema.org", "@type": "Dataset",
         "name": "Afectaciones del servicio eléctrico en La Habana",
         "description": "Series de partes oficiales de la UNE, estado por circuito y rotación por bloque, actualizadas cada ~25 minutos.",
         "inLanguage": "es", "isAccessibleForFree": True,
         "creator": {"@type": "Organization", "name": "Apagones La Habana (proyecto comunitario)"},
         "distribution": [
             {"@type": "DataDownload", "encodingFormat": "application/json", "contentUrl": site_url("data/estado.json")},
             {"@type": "DataDownload", "encodingFormat": "application/json", "contentUrl": site_url("data/circuitos.json")},
             {"@type": "DataDownload", "encodingFormat": "application/json", "contentUrl": site_url("data/bloques_por_municipio.json")},
         ]},
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


def sitemap_xml(urls):
    """sitemap de protocolo: <urlset><url><loc> por URL absoluta, orden estable."""
    cuerpo = "".join("  <url><loc>%s</loc></url>\n" % u for u in urls)
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


def municipios_de(estado, circ, bloques):
    """Unión de nombres que aparecen en los tres JSON, en orden estable por slug.

    La autoridad de los 15 nombres canónicos es el geojson de municipios; los
    datos de_pipeline los usan literalmente, así que la unión los reproduce.
    """
    nombres = set((estado or {}).get("municipios", {}))
    for c in (circ or {}).get("circuitos", []):
        nombres.update(c.get("municipios") or ([c["municipio"]] if c.get("municipio") else []))
    nombres.update((bloques or {}).keys())
    return sorted((n for n in nombres if n), key=slug)


def _hora_cuba(iso):
    """HH:MM fijo (-4, sin tzdata) de un timestamp ISO de los datos; '—' si no hay."""
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return "—"
    utc = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return utc.astimezone(HORA_CUBA).strftime("%H:%M")


def region_hub(estado, circ, bloques):
    """Contenido de la región SEO:INICIO del hub: grilla .rc-card, una tarjeta
    por municipio con nombre, cuenta (recorrido compartido con la hija), enlace
    a /municipio/<slug>/ y deep link ?municipio= al mapa."""
    tarjetas = []
    for nombre in municipios_de(estado, circ, bloques):
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


# Nav canónica: orden y destinos únicos de los 6 destinos del sitio, compartida
# por las 7 superficies de render (5 raíces commiteadas, hub y páginas hijas).
DESTINOS_NAV = (
    ("", "🗺 Mapa"),
    ("analitica.html", "📊 Análisis"),
    ("partes.html", "📢 Partes"),
    ("circuitos.html", "🔌 Circuitos"),
    ("municipios/", "🏘️ Municipios"),
    ("sugerencias.html", "💡 Sugerencias"),
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


def pagina_municipio(nombre, estado, circ, bloques):
    """Página estática completa de un municipio (forma /municipio/<slug>/)."""
    s = slug(nombre)
    url = site_url("municipio/%s/" % s)
    stamp = hora_estampado(estado.get("generado"))
    del_muni = circuitos_del_municipio(nombre, circ)
    sin = [c for c in del_muni if c.get("estado") == "sin servicio"]
    titulo = "Apagones en %s hoy — horario y estado actual" % nombre
    descripcion = ("Estado de los apagones en %s (La Habana) hoy: circuitos sin servicio según "
                   "el último parte, rotación por bloque y horario. Actualización: %s." % (nombre, stamp))
    if sin:
        parrafo_estado = ("<p><b>%d de %d circuitos</b> del municipio están sin servicio "
                          "según el último parte.</p>" % (len(sin), len(del_muni)))
    elif del_muni:
        parrafo_estado = ("<p>%s aparece <b>sin afectaciones registradas</b> en el último parte: "
                          "sus %d circuitos catalogados tienen servicio.</p>" % (esc_html(nombre), len(del_muni)))
    else:
        parrafo_estado = ("<p>%s aparece <b>sin afectaciones registradas</b> en el último parte; "
                          "aún no tiene circuitos catalogados. La rotación por bloque sigue activa.</p>"
                          % esc_html(nombre))
    # Listado en tarjetas .circ (vocabulario del sitio, nunca <table>): una por
    # circuito sin servicio, ordenada por antigüedad del estado (más nuevo antes).
    def _tarjeta(c):
        chips = ""
        if c.get("bloque"):
            chips += '<span class="circ-b">Bloque %s</span>' % esc_html(c["bloque"])
        if c.get("causa"):
            chips += '<span class="circ-b">Causa: %s</span>' % esc_html(c["causa"])
        hora = _hora_cuba(c.get("estado_fecha"))
        meta = ("desde %s (La Habana)" % hora) if hora != "—" else "hora sin publicar"
        return ('<article class="circ">\n'
                '<div class="circ-cab">'
                '<a class="circ-cod" href="/circuitos.html?c=%s">%s</a>'
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
    rot = (bloques or {}).get(nombre) or {}
    bloques_html = "".join("<p><b>Bloque %s</b> — %s</p>" % (b, esc_html(" · ".join(z)))
                           for b, z in sorted(rot.items(), key=lambda kv: int(kv[0])))
    rotacion = ("<h2>Rotación por bloque</h2>"
                + (bloques_html or "<p>Sin rotación publicada para este municipio.</p>"))
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
        parrafo_estado, listado,
        '<p><a href="/?municipio=%s">Ver %s en el mapa interactivo</a></p>' % (quote(nombre), esc_html(nombre)),
        rotacion, historial,
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


def urls_del_sitemap(nombres):
    """Las 21 URLs canónicas (6 páginas + 15 municipios, forma de directorio)."""
    urls = [site_url(p[0]) for p in PAGINAS.values()]
    urls += [site_url("municipio/%s/" % slug(n)) for n in nombres]
    return urls


# Regiones del body que generar() rellena por archivo (las no listadas solo
# reciben su HEAD). Las dos comparten firma (estado, circ, bloques).
REGIONES_CUERPO = {
    "index.html": lambda estado, circ, bloques: instantanea_index(estado, circ),
    "municipios/index.html": region_hub,
}


def generar(dir_web, datos):
    """Regenera la superficie SEO dentro de dir_web a partir de los JSON.

    Idempotente por construcción: todo byte sale de `datos` (ni reloj ni
    aleatoriedad), así que dos corridas con las mismas entradas dan las mismas
    salidas. Nunca se commitea lo que escribe aquí.
    """
    estado, circ, bloques = datos
    nombres = municipios_de(estado, circ, bloques)
    for archivo in PAGINAS:
        ruta = os.path.join(dir_web, archivo)
        with open(ruta, encoding="utf-8") as f:
            texto = f.read()
        texto = reescribir_region(texto, MARCA_HEAD_INICIO, MARCA_HEAD_FIN,
                                  head_estaticas(archivo, nombres))
        relleno = REGIONES_CUERPO.get(archivo)
        if relleno is not None:
            texto = reescribir_region(texto, MARCA_INICIO, MARCA_FIN,
                                      relleno(estado, circ, bloques))
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(texto)
    for nombre in nombres:
        destino = os.path.join(dir_web, "municipio", slug(nombre))
        os.makedirs(destino, exist_ok=True)
        with open(os.path.join(destino, "index.html"), "w", encoding="utf-8") as f:
            f.write(pagina_municipio(nombre, estado, circ, bloques))
    # Endpoints de rastreo. robots.txt vive TAMBIÉN commiteado (si esta corrida
    # revienta, el deploy del último-good conserva reglas); el re-emitir aquí
    # solo lo alinea cuando SITE_BASE cambió. sitemap.xml es 100% generado.
    with open(os.path.join(dir_web, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write(sitemap_xml(urls_del_sitemap(nombres)))
    with open(os.path.join(dir_web, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(robots_txt())


def _cargar(ruta):
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def main():
    datos = (_cargar(os.path.join(DATOS, "estado.json")),
             _cargar(os.path.join(DATOS, "circuitos.json")),
             _cargar(os.path.join(DATOS, "bloques_por_municipio.json")))
    generar(WEB, datos)
    print("SEO regenerado en el árbol de trabajo (nada de esto se commitea)")


if __name__ == "__main__":
    main()
