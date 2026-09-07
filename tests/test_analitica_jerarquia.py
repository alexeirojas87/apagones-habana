"""U9 — Jerarquía de analítica (D11 trazado del spec R12/S16).

Spec R12/S16 estático: KPIs con énfasis por --text-strong y escala tipográfica,
sin estilos en línea en analitica.html (clases de token), paneles agrupados por
sección con clases, y respaldo de datos legible sin JavaScript en cada gráfico
(enlace al JSON mismo-origen). La jerarquía visual fina es checklist manual.
Offline, stdlib, py3.9.
"""

import re
import unittest
from pathlib import Path

RAIZ = Path(__file__).parents[1]
HTML = (RAIZ / "web" / "analitica.html").read_text(encoding="utf-8")
CSS = (RAIZ / "web" / "style.css").read_text(encoding="utf-8")

# id del gráfico -> clase de sección que lo agrupa
GRUPOS = {
    "c-circ-horas": "sec-circuitos", "c-circ-freq": "sec-circuitos",
    "c-records": "sec-records",
    "c-mw": "sec-deficit", "c-semanas": "sec-deficit",
    "c-dias": "sec-deficit", "c-horas": "sec-deficit",
    "c-tipos": "sec-averias", "c-averias": "sec-averias",
    "c-causas": "sec-avisos", "c-municipios": "sec-avisos",
    "c-lugares": "sec-reportes",
}


class SinEstilosEnLineaTest(unittest.TestCase):
    def test_cero_atributos_style_en_analitica(self):
        self.assertNotIn("style=", HTML)

    def test_los_paneles_anchos_usan_clase_de_token(self):
        self.assertRegex(HTML, r'class="panel[^"]*panel-ancho"')
        self.assertRegex(CSS, r"\.panel-ancho\s*\{[^}]*grid-column:\s*1\s*/\s*-1")


class GruposDeSeccionTest(unittest.TestCase):
    def test_cada_panel_lleva_su_clase_de_grupo(self):
        for cid, grupo in GRUPOS.items():
            m = re.search(r'<div id="%s"' % cid, HTML)
            self.assertIsNotNone(m, "falta el gráfico %s" % cid)
            # el <section class="panel ..."> que lo contiene lleva el grupo
            seccion = re.search(r'<section class="(panel[^"]*)"[^>]*>(?:(?!</section>).)*?<div id="%s"' % cid,
                                HTML, re.DOTALL)
            self.assertIsNotNone(seccion, cid)
            self.assertIn(grupo, seccion.group(1), "%s: %s" % (cid, grupo))

    def test_los_grupos_usan_colores_de_token_en_el_css(self):
        for grupo in set(GRUPOS.values()):
            self.assertRegex(CSS, r"\.%s[^{]*\{[^}]*var\(--" % grupo)


class RespaldoDeDatosTest(unittest.TestCase):
    def test_cada_grafico_tiene_respaldo_legible_sin_js(self):
        for cid in GRUPOS:
            cuerpo = re.search(r'<div id="%s"[^>]*>(.*?)</div>' % cid, HTML, re.DOTALL)
            self.assertIsNotNone(cuerpo, cid)
            self.assertIn('class="chart-respaldo"', cuerpo.group(1), cid)
            self.assertIn('href="data/analitica.json"', cuerpo.group(1), cid)

    def test_los_kpis_tambien_tienen_respaldo(self):
        kpis = re.search(r'<section class="kpis" id="kpis">(.*?)</section>', HTML, re.DOTALL)
        self.assertIsNotNone(kpis)
        self.assertIn('class="chart-respaldo"', kpis.group(1))


class JerarquiaKpiTest(unittest.TestCase):
    def test_los_kpis_se_enfatizan_con_text_strong_y_escala(self):
        regla = re.search(r"\.kpi \.num\s*\{[^}]*\}", CSS)
        self.assertIsNotNone(regla, "falta .kpi .num")
        cuerpo = regla.group(0)
        self.assertIn("var(--text-strong)", cuerpo)
        self.assertIn("800", cuerpo)  # peso de la escala tipográfica
        m = re.search(r"font-size:\s*([\d.]+)rem", cuerpo)
        self.assertIsNotNone(m)
        self.assertGreaterEqual(float(m.group(1)), 1.8)


if __name__ == "__main__":
    unittest.main()
