"""U11 — Auditoría de paridad: nav de la fuente única y suite en verde.

Spec R7/S9 y regla #1 del apply: la salida de nav_tabs() es byte-idéntica al
bloque <nav> commiteado en las 8 superficies (anclado al comentario de
regeneración), y una corrida completa del generador sobre el árbol commiteado
deja los marcadores SEO y los JSON-LD byte-idénticos entre corridas (lo único
que puede diferir entre la salida vieja y la nueva son las regiones intencionales:
nav/FAQ/sitemap/theme-color). Offline, stdlib, py3.9.
"""

import importlib.util
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).parents[1]
RUTA_SEO = RAIZ / "scripts" / "build_seo.py"
SPEC = importlib.util.spec_from_file_location("build_seo", RUTA_SEO)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

MARCA_NAV = "nav: generado por build_seo.py nav_tabs() — regenerar, no editar"

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


def nav_tras_marca(html, fuente):
    """El bloque <nav class="tabs">…</nav> que sigue al comentario de regeneración."""
    i = html.find("<!-- " + MARCA_NAV)
    assert i >= 0, "%s: falta el comentario de regeneración" % fuente
    m = re.compile(r'<nav class="tabs">.*?</nav>').search(html, i)
    assert m, "%s: no hay nav tras el comentario" % fuente
    return m.group(0)


class ParidadNavTest(unittest.TestCase):
    def test_las_8_navs_comiteadas_son_byte_identicas_a_la_fuente(self):
        for archivo, activo in ACTIVOS.items():
            html = (RAIZ / "web" / archivo).read_text(encoding="utf-8")
            self.assertEqual(nav_tras_marca(html, archivo), MOD.nav_tabs(activo), archivo)

    def test_cada_destino_aparece_una_vez_y_se_renderiza_igual(self):
        # en cada nav, cada destino es enlace (con el mismo render) o el span
        # activo: exactamente una de las dos formas, nunca duplicado
        activos = [a for a, _, _ in MOD.DESTINOS_NAV] + [None]
        navs = [MOD.nav_tabs(a) for a in activos]
        for destino, etiqueta, icono in MOD.DESTINOS_NAV:
            enlace = '<a href="/%s">%s %s</a>' % (destino, MOD.icono_svg(icono), etiqueta)
            span = '<span class="activo">%s %s</span>' % (MOD.icono_svg(icono), etiqueta)
            for nav in navs:
                formas = (nav.count(enlace), nav.count(span))
                self.assertEqual(sum(formas), 1, "%s: %r" % (destino, formas))
        # exactamente una pestaña activa por nav; el 404 (None) no tiene ninguna
        for activo, nav in zip(activos, navs):
            self.assertEqual(nav.count('class="activo"'), 0 if activo is None else 1)


class ParidadGeneradorTest(unittest.TestCase):
    """Corrida del generador sobre el árbol commiteado + fixtures: los marcadores
    y los JSON-LD quedan byte-idénticos entre dos corridas (S9)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.web = os.path.join(self.tmp, "web")
        os.makedirs(os.path.join(self.web, "data"))
        shutil.copyfile(str(RAIZ / "web" / "data" / "municipios.geojson"),
                        os.path.join(self.web, "data", "municipios.geojson"))
        for archivo in MOD.PAGINAS:
            destino = os.path.join(self.web, archivo)
            os.makedirs(os.path.dirname(destino) or self.web, exist_ok=True)
            shutil.copyfile(str(RAIZ / "web" / archivo), destino)
        with open(RAIZ / "tests" / "fixtures" / "mini_estado.json", encoding="utf-8") as f:
            estado = json.load(f)
        with open(RAIZ / "tests" / "fixtures" / "mini_circuitos.json", encoding="utf-8") as f:
            circ = json.load(f)
        MOD.generar(self.web, (estado, circ))
        # segunda corrida sobre el resultado de la primera (idempotencia)
        MOD.generar(self.web, (estado, circ))

    def _bloques(self, html):
        heads = re.findall(re.escape(MOD.MARCA_HEAD_INICIO) + r"(.*?)" +
                           re.escape(MOD.MARCA_HEAD_FIN), html, re.DOTALL)
        ld = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
        return heads, ld

    def test_marcadores_unicos_y_jsonld_estables_entre_corridas(self):
        for archivo in MOD.PAGINAS:
            with open(os.path.join(self.web, archivo), encoding="utf-8") as f:
                html = f.read()
            self.assertEqual(html.count(MOD.MARCA_HEAD_INICIO), 1, archivo)
            if archivo in MOD.REGIONES_CUERPO:  # solo 3 páginas tienen cuerpo SEO
                self.assertEqual(html.count(MOD.MARCA_INICIO), 1, archivo)
            # una segunda corrida sobre el mismo archivo no cambia ni head ni ld
            antes = self._bloques(html)
            MOD.generar(self.web, MOD_generar_datos())
            with open(os.path.join(self.web, archivo), encoding="utf-8") as f:
                html2 = f.read()
            self.assertEqual(antes, self._bloques(html2), archivo)

    def test_los_jsonld_parsean_y_son_deterministas(self):
        for archivo in MOD.PAGINAS:
            with open(os.path.join(self.web, archivo), encoding="utf-8") as f:
                html = f.read()
            for crudo in re.findall(r'<script type="application/ld\+json">(.*?)</script>',
                                    html, re.DOTALL):
                json.loads(crudo)  # parseable
            primera = self._bloques(html)
            MOD.generar(self.web, MOD_generar_datos())
            with open(os.path.join(self.web, archivo), encoding="utf-8") as f:
                html2 = f.read()
            self.assertEqual(primera, self._bloques(html2), archivo)


def MOD_generar_datos():
    with open(RAIZ / "tests" / "fixtures" / "mini_estado.json", encoding="utf-8") as f:
        return (json.load(f), json.load(open(RAIZ / "tests" / "fixtures" / "mini_circuitos.json",
                                             encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
