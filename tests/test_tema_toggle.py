"""U4 — Botón de tema (emitido por nav_tabs) + Leaflet con tokens.

Spec R2 (S2/S3 estático: el botón existe con nombre accesible y el flavor del
basemap sale del tema resuelto; el comportamiento en vivo es checklist manual),
R1/R3 vía D8: las islas claras de Leaflet (#f5f5f5/#222/#666/#b06060/#dfe8ef)
se sustituyen por selectores con tokens. Offline, stdlib, py3.9.
"""

import importlib.util
import re
import unittest
from pathlib import Path

RAIZ = Path(__file__).parents[1]
RUTA_SEO = RAIZ / "scripts" / "build_seo.py"
SPEC = importlib.util.spec_from_file_location("build_seo", RUTA_SEO)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

CSS = (RAIZ / "web" / "style.css").read_text(encoding="utf-8")
APP_JS = (RAIZ / "web" / "app.js").read_text(encoding="utf-8")

SHELLS = [
    "index.html", "analitica.html", "partes.html", "circuitos.html",
    "sugerencias.html", "municipios/index.html",
    "preguntas-frecuentes/index.html", "404.html",
]

ACTIVOS = {
    "index.html": "",
    "analitica.html": "analitica",
    "partes.html": "partes",
    "circuitos.html": "circuitos",
    "sugerencias.html": "sugerencias",
    "municipios/index.html": "municipios/",
    "preguntas-frecuentes/index.html": "preguntas-frecuentes/",
    "404.html": None,
}


def nav_de(html, fuente):
    navs = re.findall(r'<nav class="tabs">.*?</nav>', html, re.DOTALL)
    assert len(navs) == 1, "%s: %d navs" % (fuente, len(navs))
    return navs[0]


class BotonTemaTest(unittest.TestCase):
    def test_nav_tabs_emite_el_boton_con_nombre_accesible(self):
        for activo, _, _ in MOD.DESTINOS_NAV:
            nav = MOD.nav_tabs(activo)
            m = re.search(r'<button id="boton-tema"[^>]*>', nav)
            self.assertIsNotNone(m, activo)
            self.assertIn('type="button"', m.group(0))
            self.assertRegex(m.group(0), r'aria-label="[^"]{3,}"')
            self.assertIn("Cambiar", m.group(0))

    def test_nav_tabs_none_tambien_trae_el_boton(self):
        # el 404 no pertenece a ningún destino y también lleva el botón
        self.assertIn('id="boton-tema"', MOD.nav_tabs(None))

    def test_el_boton_gira_el_estado_en_linea(self):
        nav = MOD.nav_tabs("analitica")
        self.assertIn("dataset.theme", nav)          # voltea el atributo del DOM
        self.assertIn("localStorage.setItem('tema'", nav)  # persiste la elección
        self.assertIn("cambio-tema", nav)            # avisa a la página (mapa)

    def test_las_8_superficies_llevan_el_boton(self):
        for archivo in SHELLS:
            html = (RAIZ / "web" / archivo).read_text(encoding="utf-8")
            self.assertEqual(html.count('id="boton-tema"'), 1, archivo)

    def test_las_8_navs_siguen_byte_identicas_a_la_fuente(self):
        for archivo, activo in ACTIVOS.items():
            html = (RAIZ / "web" / archivo).read_text(encoding="utf-8")
            self.assertEqual(nav_de(html, archivo), MOD.nav_tabs(activo), archivo)


class FlavorDesdeTemaTest(unittest.TestCase):
    def test_app_js_no_fija_flavor_light(self):
        self.assertNotIn('flavor: "light"', APP_JS)
        self.assertNotIn("flavor:'light'", APP_JS)

    def test_el_flavor_sale_del_tema_resuelto(self):
        self.assertIn("dataset.theme", APP_JS)
        m = re.search(r"flavor:\s*([A-Za-z_][A-Za-z0-9_]*)", APP_JS)
        self.assertIsNotNone(m, "el flavor debe venir de una función del tema")
        self.assertNotEqual(m.group(1), '"light"')

    def test_el_cambio_de_tema_repuso_el_basemap_sin_recrear_el_mapa(self):
        self.assertIn('"cambio-tema"', APP_JS)  # escucha el evento del botón
        self.assertIn("removeLayer", APP_JS)    # quita la capa base
        self.assertIn("addTo(mapa)", APP_JS)    # y la repone


class LeafletConTokensTest(unittest.TestCase):
    """D8: nada de islas claras; todo selector Leaflet sale de tokens."""

    ISLAS = ("#f5f5f5", "#dfe8ef", "#b06060")

    def test_sin_islas_claras_en_el_css(self):
        for hex_isla in self.ISLAS:
            self.assertNotIn(hex_isla.lower(), CSS.lower(), hex_isla)
        self.assertNotIn("color: #222", CSS)
        self.assertNotIn("color: #666", CSS)

    def test_popup_leyenda_y_controles_usan_tokens(self):
        wrapper = re.search(r"\.leaflet-popup-content-wrapper[^{]*\{[^}]*\}", CSS)
        self.assertIsNotNone(wrapper)
        self.assertIn("var(--surface)", wrapper.group(0))
        self.assertIn("var(--text)", wrapper.group(0))
        self.assertIn(".leaflet-popup-tip", CSS)
        self.assertRegex(CSS, r"\.leyenda\s*\{[^}]*var\(--")
        self.assertIn(".leaflet-control-attribution", CSS)
        self.assertIn(".leaflet-bar a", CSS)
        self.assertIn(".leaflet-container", CSS)  # fondo del mapa con token
        mapa = re.search(r"#mapa\s*\{[^}]*\}", CSS).group(0)
        self.assertIn("var(--bg)", mapa)
