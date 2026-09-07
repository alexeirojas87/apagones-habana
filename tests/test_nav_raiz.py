"""U1 — Routing: nav raíz-relativa en el generador y en los 7 shells commiteados.

Spec R5/S6: todo href de la nav empieza por /; cero subidas ../, cero nombres
de archivo sueltos, cero URLs absolutas de pages.dev; la nav de las páginas de
municipio lleva las 7 pestañas incluida Preguntas y es byte-idéntica a la
fuente única nav_tabs(). Offline, stdlib, py3.9.
"""

import importlib.util
import json
import re
import unittest
from pathlib import Path

RAIZ = Path(__file__).parents[1]
RUTA = RAIZ / "scripts" / "build_seo.py"
SPEC = importlib.util.spec_from_file_location("build_seo", RUTA)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

SHELLS = [
    "index.html",
    "analitica.html",
    "partes.html",
    "circuitos.html",
    "sugerencias.html",
    "municipios/index.html",
    "preguntas-frecuentes/index.html",
]

MARCA_NAV = "nav: generado por build_seo.py nav_tabs() — regenerar, no editar"


def nav_de(html, fuente):
    """El único bloque <nav class="tabs"> del documento."""
    navs = re.findall(r'<nav class="tabs">.*?</nav>', html, re.DOTALL)
    assert len(navs) == 1, "%s: %d navs" % (fuente, len(navs))
    return navs[0]


def hrefs_de(nav):
    """Los href de la nav, sin contar el span activo (no lleva href)."""
    return re.findall(r'<a href="([^"]+)">', nav)


def con_fixtures():
    with open(RAIZ / "tests" / "fixtures" / "mini_estado.json", encoding="utf-8") as f:
        estado = json.load(f)
    with open(RAIZ / "tests" / "fixtures" / "mini_circuitos.json", encoding="utf-8") as f:
        circ = json.load(f)
    return estado, circ


class NavTabsRaizRelativaTest(unittest.TestCase):
    """La fuente única nav_tabs() solo emite hrefs raíz-relativos (D3)."""

    def test_toda_salida_de_nav_tabs_usa_hrefs_raiz_relativos(self):
        for activo, _, _ in MOD.DESTINOS_NAV:
            nav = MOD.nav_tabs(activo)
            hrefs = hrefs_de(nav)
            self.assertEqual(len(hrefs), 6, activo)  # 7 pestañas - 1 activa
            for href in hrefs:
                self.assertTrue(href.startswith("/"), "%s: %s" % (activo, href))
                self.assertNotIn("..", href, activo)
                self.assertFalse(href.endswith(".html"), "%s: %s" % (activo, href))

    def test_nav_tabs_no_menciona_pages_dev_ni_urls_absolutas(self):
        for activo, _, _ in MOD.DESTINOS_NAV:
            nav = MOD.nav_tabs(activo)
            self.assertNotIn("pages.dev", nav, activo)
            self.assertNotIn('href="http', nav, activo)

    def test_el_mapa_enlaza_a_la_raiz_del_sitio(self):
        # triangulación: el destino "" (Mapa) se convierte en href="/" y no en "".
        nav = MOD.nav_tabs("analitica")
        self.assertRegex(nav, r'<a href="/"><svg class="ico" aria-hidden="true"><use href="/icons\.svg#icon-map"></use></svg> Mapa</a>')
        self.assertRegex(nav, r'<a href="/preguntas-frecuentes/"><svg[^>]*><use href="/icons\.svg#icon-help"></use></svg> Preguntas</a>')


class ShellsNavRaizRelativaTest(unittest.TestCase):
    """Los 7 shells commiteados llevan la nav regenerada, raíz-relativa,
    con el comentario de regeneración que ata el bloque a su fuente única."""

    def test_cada_shell_tiene_comentario_de_regeneracion_y_nav(self):
        for archivo in SHELLS:
            html = (RAIZ / "web" / archivo).read_text(encoding="utf-8")
            self.assertIn(MARCA_NAV, html, archivo)

    def test_los_hrefs_de_los_shells_son_raiz_relativos(self):
        for archivo in SHELLS:
            html = (RAIZ / "web" / archivo).read_text(encoding="utf-8")
            nav = nav_de(html, archivo)
            hrefs = hrefs_de(nav)
            self.assertEqual(len(hrefs), 6, archivo)
            for href in hrefs:
                self.assertTrue(href.startswith("/"), "%s: %s" % (archivo, href))
                self.assertNotIn("..", href, archivo)
                self.assertFalse(href.endswith(".html"), "%s: %s" % (archivo, href))

    def test_los_shells_no_tienen_urls_de_pages_dev_en_la_nav(self):
        for archivo in SHELLS:
            html = (RAIZ / "web" / archivo).read_text(encoding="utf-8")
            nav = nav_de(html, archivo)
            self.assertNotIn("pages.dev", nav, archivo)
            self.assertNotIn('href="http', nav, archivo)

    def test_la_nav_de_cada_shell_es_byte_identica_a_nav_tabs(self):
        activos = {
            "index.html": "",
            "analitica.html": "analitica",
            "partes.html": "partes",
            "circuitos.html": "circuitos",
            "sugerencias.html": "sugerencias",
            "municipios/index.html": "municipios/",
            "preguntas-frecuentes/index.html": "preguntas-frecuentes/",
        }
        for archivo, activo in activos.items():
            html = (RAIZ / "web" / archivo).read_text(encoding="utf-8")
            self.assertEqual(nav_de(html, archivo), MOD.nav_tabs(activo), archivo)


class NavMunicipioTest(unittest.TestCase):
    """Las páginas hijas /municipio/<slug>/ llevan las 7 pestañas (incluida
    Preguntas) y la misma nav byte-idéntica de la fuente única."""

    def test_pagina_municipio_incluye_nav_de_7_pestanas_con_preguntas(self):
        estado, circ = con_fixtures()
        html = MOD.pagina_municipio("Playa", estado, circ, ["Playa"])
        nav = nav_de(html, "municipio/playa/")
        tabs = re.findall(
            r'<span class="activo">(?:<svg.*?</svg>)?\s*([^<]+)</span>'
            r'|<a href="([^"]+)">(?:<svg.*?</svg>)?\s*([^<]+)</a>', nav)
        self.assertEqual(len(tabs), 7, nav)
        etiquetas = [t[0] or t[2] for t in tabs]
        self.assertIn("Preguntas", etiquetas)
        for href in hrefs_de(nav):
            self.assertTrue(href.startswith("/"), href)

    def test_la_nav_de_municipio_es_la_misma_fuente_unica(self):
        estado, circ = con_fixtures()
        html = MOD.pagina_municipio("Playa", estado, circ, ["Playa"])
        self.assertEqual(nav_de(html, "municipio/playa/"), MOD.nav_tabs("municipios/"))


if __name__ == "__main__":
    unittest.main()
