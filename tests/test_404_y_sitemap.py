"""U2 — 404.html real (el sitemap extensionless ya lo cubre test_seo.py).

Spec R6/S8 (parte estática) y D3: la página 404 es un shell independiente que
usa los tokens compartidos (/style.css), la nav de la fuente única con su
comentario de regeneración y el script sin destello antes del CSS. El
sitemap.xml NO vive commiteado (gitignored, 100% generado en el deploy) y el
generador ya emite URLs extensionless (test_sitemap_con_22_urls_extensionless).
Offline, stdlib, py3.9.
"""

import importlib.util
import re
import unittest
from pathlib import Path

RAIZ = Path(__file__).parents[1]
RUTA = RAIZ / "scripts" / "build_seo.py"
SPEC = importlib.util.spec_from_file_location("build_seo", RUTA)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

# Escaneo de emoji-as-icon (rango de la estrategia de pruebas del design):
# 1F300–1FAFF + 2600–27BF. La nav lleva etiquetas con emoji hasta U5 (sprite),
# así que los casos de esta fase excluyen el bloque <nav> del conteo.
EMOJI = re.compile(u"[\\U0001F300-\\U0001FAFF\\u2600-\\u27BF]")
MARCA_NAV = "nav: generado por build_seo.py nav_tabs() — regenerar, no editar"

RUTA_404 = RAIZ / "web" / "404.html"


def cuerpo_sin_nav(html):
    """El 404 sin su bloque nav (las etiquetas con emoji mueren en U5)."""
    return re.sub(r"<!-- nav:.*?</nav>", "", html, flags=re.DOTALL)


def nav_de(html, fuente):
    navs = re.findall(r'<nav class="tabs">.*?</nav>', html, re.DOTALL)
    assert len(navs) == 1, "%s: %d navs" % (fuente, len(navs))
    return navs[0]


class Pagina404Test(unittest.TestCase):
    def setUp(self):
        self.assertTrue(RUTA_404.exists(), "falta web/404.html")
        self.html = RUTA_404.read_text(encoding="utf-8")

    def test_existe_como_shell_independiente(self):
        self.assertTrue(self.html.startswith("<!DOCTYPE html>"))
        self.assertIn('<html lang="es">', self.html)

    def test_consumes_los_tokens_compartidos_sin_css_propio(self):
        self.assertIn('<link rel="stylesheet" href="/style.css">', self.html)
        self.assertNotIn("vendor/", self.html)  # sin Leaflet: shell standalone
        sin_nav = cuerpo_sin_nav(self.html)
        hexes = re.findall(r"#[0-9a-fA-F]{3,8}\b", sin_nav)
        self.assertEqual(hexes, [], "colores fuera de tokens: %r" % hexes)
        self.assertNotIn("<script src=", self.html)  # sin JS más allá del no-flash

    def test_la_nav_es_la_fuente_unica_con_su_comentario(self):
        self.assertIn(MARCA_NAV, self.html)
        # el 404 no pertenece a ningún destino: nav_tabs(None) enlaza los 7
        self.assertEqual(nav_de(self.html, "404"), MOD.nav_tabs(None))

    def test_cero_emoji_fuera_de_la_nav(self):
        sobrantes = EMOJI.findall(cuerpo_sin_nav(self.html))
        self.assertEqual(sobrantes, [], "emojis: %r" % sobrantes)

    def test_script_sin_destello_va_antes_del_stylesheet(self):
        guion = self.html.find('localStorage.getItem("tema")')
        css = self.html.find('<link rel="stylesheet" href="/style.css">')
        self.assertGreater(guion, -1, "falta el script sin destello")
        self.assertLess(guion, css, "el script sin destello debe ir antes del CSS")
        self.assertIn("prefers-color-scheme", self.html)

    def test_copia_de_no_encontrada_con_enlace_a_casa(self):
        self.assertIn("no encontrada", self.html.lower())
        enlaces = re.findall(r'<a href="(/[^"]*)">', self.html)
        self.assertIn("/", enlaces, "falta el enlace a la portada")
        self.assertIn("/preguntas-frecuentes/", enlaces)


class SitemapExtensionlessTest(unittest.TestCase):
    """Guardia D3: la fuente única del sitemap no emite .html (el snapshot
    local está gitignored; esto cubre el generador, no un artefacto)."""

    def test_las_urls_canonicas_del_generador_no_llevan_html(self):
        nombres = MOD.nombres_de_geojson(str(RAIZ / "web"))
        for u, _ in MOD.urls_del_sitemap(nombres, None):
            self.assertNotIn(".html", u)


if __name__ == "__main__":
    unittest.main()
