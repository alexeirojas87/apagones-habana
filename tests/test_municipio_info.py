"""S8-S16 del delta spec municipios-info: la sección de rotación se reemplaza
con datos por municipio realmente útiles — catálogo completo de circuitos,
ranking "N de 15" con población estimada, reincidentes por `veces` con aviso de
antigüedad y averías recientes desde analitica.json.

Fixtures: Playa trae los cuatro grupos de vigencia (2 sin frescos + 1 más = 3
sin, 1 nd a 30 h, 2 con, 1 asumido a 51 h) y 7 valores de `veces` disparados.
"""

import json
import math
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_seo  # noqa: E402

MOD = test_seo.MOD
RAIZ = test_seo.RAIZ


def _filas(pagina):
    """Códigos del catálogo, en el orden en que salen."""
    m = re.search(r'<ul class="circ-filas">(.*?)</ul>', pagina, re.DOTALL)
    return None if m is None else re.findall(r'href="/circuitos\.html\?c=([^"]+)"', m.group(1))


def _fila_de(pagina, codigo):
    for li in re.findall(r'<li class="circ-fila">.*?</li>', pagina, re.DOTALL):
        if ("?c=%s\"" % codigo) in li:
            return li
    return None


class CatalogoTest(test_seo.BaseArbol):
    """S8/S9/S10 (U-C)."""

    def setUp(self):
        test_seo.BaseArbol.setUp(self)
        self.correr()

    def pagina(self, nombre):
        return self._leer("municipio", MOD.slug(nombre), "index.html")

    def test_s8_playa_lista_los_siete_con_estado_causa_y_hora(self):
        p = self.pagina("Playa")
        self.assertIn("<h2>Catálogo completo de circuitos</h2>", p)
        self.assertEqual(_filas(p), ["B246", "PG940", "A1443",     # caídos, más nuevo antes
                                     "B123",                        # sin noticias (30 h)
                                     "B789", "L315",                 # con servicio
                                     "B456"])                        # asumido (>48 h)
        b246 = _fila_de(p, "B246")
        self.assertIn('<a class="circ-cod" href="/circuitos.html?c=B246">B246</a>', b246)
        self.assertIn('<span class="circ-est sin">sin servicio</span>', b246)
        self.assertIn("Causa: corte programado", b246)
        self.assertIn("desde 09:10 (La Habana)", b246)   # 13:10 UTC -4
        self.assertIn('<span class="circ-est nd">sin noticias</span>', _fila_de(p, "B123"))
        self.assertIn('<span class="circ-est con">con servicio</span>', _fila_de(p, "L315"))
        self.assertIn('<span class="circ-est asum">asumido</span>', _fila_de(p, "B456"))
        # L315 no tiene causa publicada: la fila no fuerza el campo
        self.assertNotIn("Causa", _fila_de(p, "L315"))
        # la sección de rotación ya no existe en ninguna página
        self.assertNotIn("Rotación", p)

    def test_s9_parcidad_longitud_del_catalogo_con_el_de_m(self):
        """S9: el catálogo cubre EXACTAMENTE los circuitos que cuentan la cabecera
        de la hija y la tarjeta del hub (recorrido compartido ⇒ por construcción)."""
        hub = self._leer("municipios", "index.html")
        for nombre in test_seo.MUNICIPIOS_15:
            p = self.pagina(nombre)
            n_filas = len(_filas(p) or [])
            m_hub = re.search(r'href="/municipio/%s/".*?(\d+) <small>de (\d+)'
                              % MOD.slug(nombre), hub, re.DOTALL)
            self.assertEqual(n_filas, int(m_hub.group(2)), "%s: catálogo vs hub" % nombre)
            m_hija = re.search(r"(\d+) de (\d+) circuitos", p)
            if m_hija:  # párrafo de estado presente (municipios con catálogo)
                self.assertEqual(n_filas, int(m_hija.group(2)), "%s: catálogo vs hija" % nombre)

    def test_s10_nada_de_circuitos_no_deja_la_pagina_en_blanco(self):
        p = self.pagina("Boyeros")  # ningún circuito catalogado
        self.assertIn("Catálogo completo de circuitos", p)
        self.assertIn("Sin circuitos catalogados", p)
        # el relleno de rotación lo sustituyen catálogo + ranking, no un vacío:
        self.assertIn("5 de 15 municipios más afectados hoy", p)
        self.assertTrue(len(p) > 600)
        m = self.pagina("Marianao")  # solo con servicio
        self.assertEqual(_filas(m), ["C8"])
        self.assertIn("sin afectaciones registradas", m)


class RankingPoblacionTest(test_seo.BaseArbol):
    """S11/S12 (U-D)."""

    def setUp(self):
        test_seo.BaseArbol.setUp(self)
        self.correr()

    def pagina(self, nombre):
        return self._leer("municipio", MOD.slug(nombre), "index.html")

    def test_s12_el_puesto_usa_cuenta_de_sin_servicio_con_empate_alfabetico(self):
        # Playa domina con 5 caídos; Regla con 1. Los 13 municipios en cero se
        # ordenan alfabéticamente por slug: 10 de octubre cierra el lote.
        self.assertIn("1 de 15 municipios más afectados hoy", self.pagina("Playa"))
        self.assertIn("2 de 15 municipios más afectados hoy", self.pagina("Regla"))
        # empates a cero, en orden alfabético de slug:
        # 3=10-de-octubre, 4=arroyo-naranjo, 5=boyeros, 13=marianao
        self.assertIn("5 de 15 municipios más afectados hoy", self.pagina("Boyeros"))
        self.assertIn("13 de 15 municipios más afectados hoy", self.pagina("Marianao"))
        # toda página muestra el ranking sobre los 15:
        for nombre in test_seo.MUNICIPIOS_15:
            self.assertRegex(self.pagina(nombre), r"\d+ de 15 municipios más afectados hoy")

    def test_s11_la_estimacion_de_personas_igual_al_metodo_del_header(self):
        # Referencia en Python de la fórmula del header (resumenCircuitos en
        # web/app.js), con el reloj anclado en estado.generado (determinismo del
        # build): fracción de circuitos no-nd del municipio × su población, o
        # promedio de ciudad si tiene menos de 2 circuitos atribuibles.
        estado, circ = test_seo.coleccion()
        generado = "2026-07-03T15:10:50+00:00"
        tabla = estado["poblacion_municipio"]
        del_gen = MOD._dt(generado)

        def vige(c):
            if c.get("estado") == "con servicio":
                return "con"
            if c.get("estado") == "sin servicio":
                t = MOD._dt(c.get("estado_fecha"))
                h = (del_gen - t).total_seconds() / 3600.0 if (t and del_gen) else 0
                if h > 48: return "asum"
                if h > 24: return "nd"
                return "sin"
            return "asum"

        todos = circ["circuitos"]
        nsin = sum(1 for c in todos if vige(c) == "sin")
        sin_city = nsin / float(len(todos))
        esperados = {}
        for nombre, pob in tabla.items():
            del_m = [c for c in todos if nombre in (c.get("municipios") or [])]
            atribuibles = [c for c in del_m if vige(c) != "nd"]
            s = sum(1 for c in atribuibles if vige(c) == "sin")
            fraccion = (s / float(len(atribuibles))) if len(atribuibles) >= 2 else sin_city
            # Math.round del header == floor(x + 0.5): la página debe usar la misma regla
            esperados[nombre] = int(math.floor(fraccion * pob + 0.5))
        # y la página debe mostrar el MISMO número (~redondeo del header):
        self.assertEqual(esperados["Playa"], 71123)  # 3 sin de 6 no-nd × 142245
        for nombre, valor in esperados.items():
            p = self.pagina(nombre)
            con_puntos = "{:,}".format(valor).replace(",", ".")
            self.assertIn("~%s personas sin corriente (estimado)" % con_puntos, p, nombre)

    def test_oficial_gana_sobre_la_fraccion(self):
        # Precedencia del método del header: si estado.poblacion trae fuente
        # oficial, la cifra del municipio es la fracción oficial de su población.
        estado, circ = test_seo.coleccion()
        estado["poblacion"] = {"fuente": "oficial", "sin_pct": 40.0, "con_pct": 60.0,
                               "con_personas": 1049978, "sin_personas": 699986,
                               "fecha": "2026-07-03T14:00:00+00:00"}
        self.correr((estado, circ))
        self.assertIn("~56.898 personas sin corriente (estimado)",
                      self.pagina("Playa"))  # 142245 * 40%


class ReincidentesTest(test_seo.BaseArbol):
    """S13/S14 (U-E)."""

    def setUp(self):
        test_seo.BaseArbol.setUp(self)
        self.correr()

    def pagina(self, nombre):
        return self._leer("municipio", MOD.slug(nombre), "index.html")

    def _reincidentes(self, p):
        m = re.search(r'<h2>Circuitos más reincidentes</h2>\s*<ul class="reinc">(.*?)</ul>',
                      p, re.DOTALL)
        return None if m is None else m.group(1)

    def test_s13_top_5_por_veces_con_desempate_por_codigo(self):
        r = self._reincidentes(self.pagina("Playa"))
        self.assertIsNotNone(r, "falta la sección de reincidentes")
        codigos = re.findall(r'href="/circuitos\.html\?c=([^"]+)"', r)
        # 15, 12, 9, 7, 7 (B456 antes que PG940 por código) — fuera: L315(2), B246(1)
        self.assertEqual(codigos, ["B789", "B123", "A1443", "B456", "PG940"])
        self.assertIn("caído 15 veces desde 10/01/2026", r)
        self.assertNotIn("L315", r)
        # un municipio con un solo circuito muestra una sola fila
        self.assertEqual(_filas_reinc := re.findall(r"\?c=([^\"]+)", self._reincidentes(self.pagina("Regla"))),
                         ["H341"])

    def test_s14_avisos_de_antiguedad_hito_24h(self):
        # 23 h (A1443): sin aviso. 30 h (B123): «hace 1 día». 51 h (B456): «hace 2 días».
        r = self._reincidentes(self.pagina("Playa"))
        fila_a = re.search(r'\?c=A1443".*?</li>', r, re.DOTALL).group(0)
        fila_b = re.search(r'\?c=B123".*?</li>', r, re.DOTALL).group(0)
        fila_c = re.search(r'\?c=B456".*?</li>', r, re.DOTALL).group(0)
        self.assertNotIn("sin noticias hace", fila_a)
        self.assertIn("sin noticias hace 1 día", fila_b)
        self.assertIn("sin noticias hace 2 días", fila_c)


class AveriasTest(test_seo.BaseArbol):
    """S15/S16 (U-F) desde tests/fixtures/mini_analitica.json."""

    def setUp(self):
        test_seo.BaseArbol.setUp(self)
        with open(os.path.join(self.web, "data", "analitica.json"), "w", encoding="utf-8") as f:
            json.dump(test_seo.fixture("mini_analitica.json"), f)
        self.correr()

    def pagina(self, nombre):
        return self._leer("municipio", MOD.slug(nombre), "index.html")

    def test_s15_ocho_mas_recientes_sin_ubicacion_vacia(self):
        p = self.pagina("Playa")
        self.assertIn("<h2>Averías recientes</h2>", p)
        fechas = re.findall(r'<li class="av-fila">(\d{2}/\d{2} \d{2}:\d{2}) · ([^<]+)</li>', p)
        self.assertEqual(len(fechas), 8)
        self.assertEqual(fechas[0][0], "03/07 11:10")   # la más nueva primero
        # las 2 más antiguas de las 10 quedan fuera, y las 2 «sin ubicación» nunca
        self.assertNotIn("Subestación", p)              # los tipos descartados por old
        self.assertNotIn("sin ubicación", p)
        tipos = [t for _, t in fechas]
        self.assertNotIn("Avería sin municipio", tipos)

    def test_s16_sin_averias_estado_vacio_explícito(self):
        p = self.pagina("Boyeros")
        self.assertIn("<h2>Averías recientes</h2>", p)
        self.assertIn("Sin averías registradas", p)


class InvariantesFormaTest(unittest.TestCase):
    """Invariante transversal del spec: los fixtures reflejan la FORMA de salida
    actual de los productores (el JSON commiteado va a la zaga, los fixtures no
    pueden inventar claves ni perder las que el builder consume)."""

    def _clave_salida(self, script, marca):
        """Claves literales del dict de salida del productor (AST sin ejecutar)."""
        import ast
        with open(os.path.join(RAIZ, script), encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=script)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "salida" for t in node.targets):
                keys = {k.value for k in node.value.keys}
                assert marca in keys, "%s cambió su forma (%s fuera)" % (script, marca)
                return keys
        self.fail("no encontré el dict `salida` en %s" % script)

    def test_mini_analitica_cabe_la_forma_de_build_analitica(self):
        forma = self._clave_salida("scripts/build_analitica.py", "circuitos_partes")
        fixture = test_seo.fixture("mini_analitica.json")
        self.assertTrue(set(fixture) <= forma)
        # S5 borró los payloads muertos pero lo vivo del join sigue en la forma:
        self.assertTrue({"averias", "circuitos_partes", "eventos"} <= forma)

    def test_mini_estado_cabe_la_forma_de_estado_py(self):
        forma = self._clave_salida("scripts/estado.py", "poblacion_municipio")
        fixture = test_seo.fixture("mini_estado.json")
        self.assertTrue(set(fixture) <= forma)
        self.assertIn("poblacion_municipio", fixture)  # emisión SIEMPRE presente

    def test_mini_circuitos_usa_campos_reales_del_catalogo(self):
        # Claves que la página consume: todas deben existir en el catálogo real
        # (el JSON commiteado puede ir a la zaga en VALORES, nunca en FORMATO).
        with open(os.path.join(RAIZ, "web", "data", "circuitos.json"),
                  encoding="utf-8") as f:
            circ = json.load(f)["circuitos"]
        reales = set().union(*[set(c) for c in circ[:80]])
        for c in test_seo.fixture("mini_circuitos.json")["circuitos"]:
            faltan = set(c) - reales
            self.assertFalse(faltan, "%s: claves inventadas %s" % (c["codigo"], faltan))


if __name__ == "__main__":
    unittest.main()
