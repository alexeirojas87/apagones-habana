"""Pruebas herméticas del generador SEO (scripts/build_seo.py).

Usan SOLO fixtures de tests/fixtures y directorios temporales: nunca leen los
JSON generados del repo ni escriben fuera de tmp. Offline, stdlib, py3.9.
Cada prueba referencia código de producción de build_seo (import por ruta,
mismo patrón que test_daf_oficial.py).
"""

import hashlib
import importlib.util
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).parents[1]
RUTA = RAIZ / "scripts" / "build_seo.py"
SPEC = importlib.util.spec_from_file_location("build_seo", RUTA)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

FIXTURES = Path(__file__).parent / "fixtures"

# Nombres canónicos de los 15 municipios (autoridad: properties.municipio de
# web/data/municipios.geojson; «Plaza», no «Plaza de la Revolución»).
MUNICIPIOS_15 = [
    "10 de Octubre", "Arroyo Naranjo", "Boyeros", "Centro Habana", "Cerro",
    "Cotorro", "Guanabacoa", "Habana Vieja", "Habana del Este", "La Lisa",
    "Marianao", "Playa", "Plaza", "Regla", "San Miguel del Padrón",
]


def fixture(nombre):
    with open(FIXTURES / nombre, encoding="utf-8") as f:
        return json.load(f)


def coleccion():
    """(estado, circuitos, bloques) desde los fixtures mini."""
    return fixture("mini_estado.json"), fixture("mini_circuitos.json"), fixture("mini_bloques.json")


class SlugTest(unittest.TestCase):
    def test_piega_acentos_espacios_y_conserva_digitos(self):
        self.assertEqual(MOD.slug("San Miguel del Padrón"), "san-miguel-del-padron")
        self.assertEqual(MOD.slug("10 de Octubre"), "10-de-octubre")
        self.assertEqual(MOD.slug("Habana del Este"), "habana-del-este")
        self.assertEqual(MOD.slug("Plaza"), "plaza")
        # plegado genérico de acentos (el spec cita también la forma larga):
        self.assertEqual(MOD.slug("Plaza de la Revolución"), "plaza-de-la-revolucion")

    def test_los_15_nombres_son_estables_y_sin_colisiones(self):
        primera = [MOD.slug(n) for n in MUNICIPIOS_15]
        segunda = [MOD.slug(n) for n in MUNICIPIOS_15]
        self.assertEqual(primera, segunda)  # dos corridas = mismos slugs
        self.assertEqual(len(set(primera)), 15)  # mapa libre de colisiones
        self.assertTrue(all(re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", s) for s in primera))


class SiteBaseTest(unittest.TestCase):
    def test_valor_por_defecto(self):
        self.assertEqual(MOD.SITE_BASE, "https://apagones-habana.pages.dev")

    def test_una_linea_cambia_canonical_og_sitemap_y_robots_junto(self):
        original = MOD.SITE_BASE
        try:
            MOD.SITE_BASE = "https://dominio-propio.example"
            head = MOD.etiquetas_head(MOD.site_url("analitica.html"), "T", "D")
            sitemap = MOD.sitemap_xml([MOD.site_url("municipio/playa/")])
            robots = MOD.robots_txt()
            self.assertIn('href="https://dominio-propio.example/analitica.html"', head)
            self.assertIn('content="https://dominio-propio.example/og.png"', head)
            self.assertIn("https://dominio-propio.example/municipio/playa/", sitemap)
            self.assertIn("Sitemap: https://dominio-propio.example/sitemap.xml", robots)
        finally:
            MOD.SITE_BASE = original
        # y al restaurar la constante vuelven todas a una vez (sin estado oculto):
        self.assertIn(MOD.SITE_BASE, MOD.etiquetas_head(MOD.site_url(""), "T", "D"))


class RegionMarcadoresTest(unittest.TestCase):
    def test_rellena_entre_marcadores_y_los_conserva(self):
        texto = "A\n<!-- SEO:INICIO -->\n<!-- SEO:FIN -->\nB"
        salida = MOD.reescribir_region(texto, MOD.MARCA_INICIO, MOD.MARCA_FIN, "<p>hola</p>")
        self.assertIn("<!-- SEO:INICIO -->\n<p>hola</p>\n<!-- SEO:FIN -->", salida)
        self.assertTrue(salida.startswith("A\n") and salida.endswith("\nB"))

    def test_dos_rellenados_con_el_mismo_contenido_son_identicos(self):
        texto = "X\n<!-- SEO:INICIO -->\n<!-- SEO:FIN -->\nY"
        una = MOD.reescribir_region(texto, MOD.MARCA_INICIO, MOD.MARCA_FIN, "<p>15:10</p>")
        dos = MOD.reescribir_region(una, MOD.MARCA_INICIO, MOD.MARCA_FIN, "<p>15:10</p>")
        self.assertEqual(una, dos)  # idempotencia byte a byte sobre el archivo vivo

    def test_reemplaza_contenido_previo_sin_duplicar_marcadores(self):
        texto = "A\n<!-- SEO:INICIO -->\n<p>viejo</p>\n<!-- SEO:FIN -->\nB"
        salida = MOD.reescribir_region(texto, MOD.MARCA_INICIO, MOD.MARCA_FIN, "<p>nuevo</p>")
        self.assertNotIn("viejo", salida)
        self.assertEqual(salida.count(MOD.MARCA_INICIO), 1)


class JsonLdTest(unittest.TestCase):
    def test_guion_del_index_parsea_con_stdlib(self):
        doc = json.loads(MOD.guion_ld(MOD.ld_index()))
        tipos = [x.get("@type") for x in doc]
        self.assertIn("WebSite", tipos)
        self.assertIn("Dataset", tipos)

    def test_guion_de_municipio_enlaza_a_index(self):
        doc = json.loads(MOD.guion_ld(MOD.ld_municipio("Playa")))
        self.assertEqual(doc["@type"], "Service")
        self.assertEqual(doc["isPartOf"]["url"], MOD.site_url(""))

    def test_etiquetas_de_cierre_de_script_quedan_escapadas(self):
        guion = MOD.guion_ld({"x": "</script><b>"})
        self.assertNotIn("</", guion)  # nunca rompe el <script> que lo envuelve
        self.assertEqual(json.loads(guion)["x"], "</script><b>")


class CapaMetadatosTest(unittest.TestCase):
    """Fase 2: frases SERP en las 5 páginas y región de HEAD generable."""

    def _head(self, archivo):
        html = (RAIZ / "web" / archivo).read_text(encoding="utf-8")
        return re.search(r"<title>(.*?)</title>", html).group(1), \
            re.search(r'<meta name="description" content="(.*?)">', html).group(1), html

    def test_index_fraseo_serp_sin_tocar_lang(self):
        titulo, desc, html = self._head("index.html")
        self.assertRegex(titulo.lower(), r"apagones.*habana.*hoy")
        self.assertRegex(desc.lower(), r"apagones|servicio eléctrico")
        self.assertIn('<html lang="es">', html)

    def test_analitica_y_partes_promocionan_horario(self):
        for archivo in ("analitica.html", "partes.html"):
            titulo, desc, _ = self._head(archivo)
            self.assertIn("horario", (titulo + " " + desc).lower(), archivo)

    def test_titulos_unicos_y_pares_con_el_mapa_del_generador(self):
        vistos = {}
        for archivo in MOD.PAGINAS:
            titulo, desc, html = self._head(archivo)
            esperado_t, esperado_d = MOD.PAGINAS[archivo][1], MOD.PAGINAS[archivo][2]
            self.assertEqual((titulo, desc), (esperado_t, esperado_d),
                             "%s: el head difiere del mapa del generador" % archivo)
            vistos[archivo] = titulo
        self.assertEqual(len(set(vistos.values())), 5)  # títulos únicos

    def test_las_cinco_paginas_tienen_pares_de_marcadores_de_head(self):
        for archivo in MOD.PAGINAS:
            html = (RAIZ / "web" / archivo).read_text(encoding="utf-8")
            self.assertEqual(html.count(MOD.MARCA_HEAD_INICIO), 1, archivo)
            self.assertEqual(html.count(MOD.MARCA_HEAD_FIN), 1, archivo)

    def test_region_de_head_generada_por_pagina(self):
        etiquetas = MOD.head_estaticas("circuitos.html")
        self.assertIn('<link rel="canonical" href="%s/circuitos.html">' % MOD.SITE_BASE, etiquetas)
        self.assertIn('property="og:type" content="website"', etiquetas)
        self.assertIn('content="es_CU"', etiquetas)
        self.assertIn('name="twitter:card" content="summary_large_image"', etiquetas)
        self.assertNotIn("ld+json", etiquetas)  # JSON-LD solo en index y municipios
        idx = MOD.head_estaticas("index.html")
        self.assertIn('href="%s/"' % MOD.SITE_BASE, idx)
        self.assertIn("application/ld+json", idx)


class MarcadoresComiteadosTest(unittest.TestCase):
    def test_index_comiteado_contiene_el_par_de_marcadores_de_cuerpo(self):
        html = (RAIZ / "web" / "index.html").read_text(encoding="utf-8")
        self.assertEqual(html.count(MOD.MARCA_INICIO), 1)
        self.assertEqual(html.count(MOD.MARCA_FIN), 1)
        self.assertLess(html.index(MOD.MARCA_INICIO), html.index(MOD.MARCA_FIN))


class BaseArbol(unittest.TestCase):
    """Árbol web/ temporal con las páginas commiteadas y datos de fixtures."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.web = os.path.join(self.tmp, "web")
        os.makedirs(os.path.join(self.web, "data"))
        for archivo in MOD.PAGINAS:
            shutil.copyfile(str(RAIZ / "web" / archivo), os.path.join(self.web, archivo))

    def correr(self, coleccion_datos=None):
        MOD.generar(self.web, coleccion_datos or coleccion())

    def _leer(self, *partes):
        with open(os.path.join(self.web, *partes), encoding="utf-8") as f:
            return f.read()

    def _arbol(self):
        firmas = {}
        for raiz_dir, _, archivos in os.walk(self.web):
            for a in archivos:
                p = os.path.join(raiz_dir, a)
                with open(p, "rb") as f:
                    firmas[os.path.relpath(p, self.web)] = hashlib.sha256(f.read()).hexdigest()
        return firmas


class CorridaCompletaTest(BaseArbol):
    """generar() sobre un árbol temporal alimentado con fixtures."""

    def test_dos_corridas_con_los_mismos_fixtures_son_byte_identicas(self):
        self.correr()
        primera = self._arbol()
        self.correr()
        self.assertEqual(primera, self._arbol())  # idempotencia total del árbol

    def test_region_de_head_rellenada_sin_duplicar_marcadores(self):
        self.correr()
        idx = self._leer("index.html")
        self.assertIn('<link rel="canonical" href="%s/">' % MOD.SITE_BASE, idx)
        self.assertEqual(idx.count(MOD.MARCA_HEAD_INICIO), 1)
        # la region del cuerpo también queda rellena y sigue habiendo un solo par
        self.assertIn('id="seo-resumen"', idx)
        self.assertEqual(idx.count(MOD.MARCA_INICIO), 1)

    def test_instantanea_del_index_trae_estado_y_estampado(self):
        self.correr()
        idx = self._leer("index.html")
        self.assertIn("3 de 5 circuitos", idx)  # datos de mini_circuitos.json
        self.assertIn("datos al 15:10 (UTC) · 11:10 hora de La Habana", idx)
        self.assertIn('<a href="/municipio/playa/">', idx)

    def test_estado_anticuado_usa_su_propio_estampado_y_no_falla(self):
        estado, circ, bloques = coleccion()
        estado["generado"] = "2020-01-01T00:00:00+00:00"  # snapshot vieja a propósito
        self.correr((estado, circ, bloques))
        self.assertIn("datos al 00:00 (UTC) · 20:00 hora de La Habana", self._leer("index.html"))

    def test_generado_invalido_no_rompe_la_corrida(self):
        estado, circ, bloques = coleccion()
        estado["generado"] = "no-es-fecha"
        self.correr((estado, circ, bloques))  # no lanza; el stamp degrada legible
        self.assertIn("datos al", self._leer("index.html"))


class PaginasMunicipioTest(BaseArbol):
    """Fase 4: las 15 páginas de municipio en forma de directorio."""

    @classmethod
    def setUpClass(cls):
        cls.slugs15 = sorted(MOD.slug(n) for n in MUNICIPIOS_15)

    def setUp(self):
        BaseArbol.setUp(self)
        self.correr()

    def pagina(self, nombre):
        return self._leer("municipio", MOD.slug(nombre), "index.html")

    def test_15_paginas_en_dir_form_desde_la_unica_corrida(self):
        self.assertEqual(sorted(os.listdir(os.path.join(self.web, "municipio"))), self.slugs15)
        for s in self.slugs15:  # cada slug es directorio con su index.html
            self.assertTrue(os.path.isfile(os.path.join(self.web, "municipio", s, "index.html")))

    def test_contenido_contrato_de_playa(self):
        p = self.pagina("Playa")
        self.assertIn("<h1>Apagones en Playa hoy</h1>", p)
        self.assertIn("2 de 3 circuitos", p)  # estado actual desde mini_circuitos
        self.assertIn("A1443", p)  # tabla de circuitos sin servicio
        self.assertIn("Kohly", p)  # calles + rotación por bloque
        self.assertIn('href="/?municipio=Playa"', p)  # deep link al mapa
        self.assertIn('<link rel="canonical" href="%s/municipio/playa/">' % MOD.SITE_BASE, p)
        self.assertIn("datos al 15:10 (UTC) · 11:10", p)

    def test_nombre_original_viaja_codificado_en_el_deep_link(self):
        from urllib.parse import quote
        self.assertIn("/?municipio=" + quote("San Miguel del Padrón"),
                      self.pagina("San Miguel del Padrón"))

    def test_historial_del_municipio_aparece_en_su_pagina(self):
        p = self.pagina("Playa")
        self.assertIn("Historial", p)
        self.assertIn("restablecimiento", p)
        self.assertIn("09:45", p)  # 13:45 UTC convertido a hora de La Habana (-4)

    def test_municipio_sin_afectaciones_nunca_queda_en_blanco(self):
        for nombre in ("Boyeros", "Marianao"):  # sin circuitos / solo con servicio
            p = self.pagina(nombre)
            self.assertIn("sin afectaciones registradas", p)
            self.assertIn("<h1>Apagones en " + nombre + " hoy</h1>", p)
            self.assertTrue(len(p) > 600, "%s quedó demasiado flaco" % nombre)
        self.assertIn("Reparto Sierra", self.pagina("Boyeros"))  # la rotación salva

    def test_jsonld_de_todas_las_paginas_parsea_y_apunta_al_index(self):
        for s in self.slugs15:
            p = self._leer("municipio", s, "index.html")
            m = re.search(r'<script type="application/ld\+json">(.*?)</script>', p, re.DOTALL)
            self.assertIsNotNone(m, s)
            doc = json.loads(m.group(1))
            self.assertEqual(doc["@type"], "Service")
            self.assertEqual(doc["isPartOf"]["url"], MOD.site_url(""))
            self.assertIn('content="es_CU"', p)  # metadata completa en cada página


class EndpointsCrawlingTest(BaseArbol):
    """Fase 5: robots.txt y sitemap.xml con paridad bidireccional."""

    def setUp(self):
        BaseArbol.setUp(self)
        self.correr()

    def _urls(self):
        return set(re.findall(r"<loc>(.*?)</loc>", self._leer("sitemap.xml")))

    def test_robots_permitir_denegar_y_sitemap_bajo_site_base(self):
        r = self._leer("robots.txt")
        self.assertIn("User-agent: *", r)
        self.assertIn("Allow: /", r)
        self.assertIn("Disallow: /api/", r)
        self.assertIn("Sitemap: %s/sitemap.xml" % MOD.SITE_BASE, r)

    def test_sitemap_con_20_urls_absolutas(self):
        urls = self._urls()
        self.assertEqual(len(urls), 20)  # 5 páginas estáticas + 15 municipios
        self.assertIn(MOD.site_url(""), urls)
        self.assertIn(MOD.site_url("analitica.html"), urls)
        self.assertIn(MOD.site_url("municipio/san-miguel-del-padron/"), urls)  # dir form
        for u in urls:
            self.assertTrue(u.startswith(MOD.SITE_BASE + "/"), u)

    def test_parcidad_sitemap_paginas_en_ambas_direcciones(self):
        # toda URL del sitemap apunta a un archivo generado que existe...
        for u in self._urls():
            rel = u[len(MOD.SITE_BASE):].lstrip("/")
            if rel == "":
                archivo = "index.html"
            elif rel.endswith("/"):
                archivo = os.path.join(rel, "index.html")
            else:
                archivo = rel
            self.assertTrue(os.path.isfile(os.path.join(self.web, archivo)), u)
        # ... y toda página del árbol (index + estáticas + municipios) está listada
        esperadas = {MOD.site_url(p[0]) for p in MOD.PAGINAS.values()}
        for s in sorted(os.listdir(os.path.join(self.web, "municipio"))):
            esperadas.add(MOD.site_url("municipio/%s/" % s))
        self.assertEqual(self._urls(), esperadas)

    def test_robots_comiteado_coincide_con_el_emisor(self):
        # paridad con el único estático commiteado de esta fase: si SITE_BASE
        # cambia, quien actualiza web/robots.txt es el mantenedor en el mismo commit
        with open(RAIZ / "web" / "robots.txt", encoding="utf-8") as f:
            self.assertEqual(f.read(), MOD.robots_txt())


if __name__ == "__main__":
    unittest.main()
