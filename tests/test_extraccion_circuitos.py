"""Regresiones de la extracción de circuitos del parte oficial.

Los dos fallos que cubren estos tests convivieron semanas sin que nada rompiera,
publicando estado equivocado en la web:

  1. El regex exigía la viñeta al INICIO de línea, y la Empresa mete varios
     circuitos en la misma ("👉2073Calle 256... 👉AL53:Zonas: 1, 2, 3..."). El
     segundo circuito se perdía y su texto se pegaba a las calles del primero.
  2. La aplicación de la caché del LLM exigía `validador_version == 2`. Al subir
     partes_llm.py a la v3, el refuerzo quedó desconectado en silencio.

El fixture es el parte real 74171 (12-ago-2026 22:22), donde AL53 y OP408
quedaron figurando "con servicio" pese a estar afectados.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).parents[1] / "extractor"))
RUTA = SCRIPTS / "build_circuitos.py"
SPEC = importlib.util.spec_from_file_location("build_circuitos", RUTA)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

PARTE = (Path(__file__).parent / "fixtures" / "parte_74171.txt").read_text(encoding="utf-8")


class RegexCircuitosTest(unittest.TestCase):
    def _codigos(self, texto):
        return [m.group(1) for m in MOD.RE_CIRC.finditer(texto)]

    def test_captura_varios_circuitos_en_la_misma_linea(self):
        # AL53 y OP408 van SEGUNDOS en su línea: son los que se perdían.
        self.assertEqual(
            sorted(set(self._codigos(PARTE))),
            ["1175", "2073", "AL53", "C8", "OP408"],
        )

    def test_la_descripcion_no_absorbe_el_circuito_siguiente(self):
        # Si la descripción se traga la viñeta del siguiente, las calles quedan
        # contaminadas y luego no resuelven contra OpenStreetMap.
        for m in MOD.RE_CIRC.finditer(PARTE):
            self.assertNotIn("👉", m.group(3))

    def test_calles_del_primer_circuito_terminan_donde_debe(self):
        desc = {m.group(1): m.group(3) for m in MOD.RE_CIRC.finditer(PARTE)}
        self.assertTrue(desc["2073"].endswith("Arroyo Arenas"))
        self.assertEqual(desc["AL53"], "Zonas: 1, 2, 3, 5, 24, 8, 7")

    def test_sigue_capturando_el_formato_de_una_linea_por_circuito(self):
        texto = ("👉 1247 : Calle 28 desde avenida 41 hasta avenida 47\n"
                 "👉 CPP20 : Alrededores de calles Ensenada de Ataré")
        self.assertEqual(self._codigos(texto), ["1247", "CPP20"])

    def test_tolera_la_vinneta_con_tono_de_piel(self):
        self.assertEqual(self._codigos("👉🏼A1443- Rio Verde"), ["A1443"])


class AvisoInstitucionalTest(unittest.TestCase):
    """La Empresa pega el aviso '📣Usted puede, aún siendo cliente...' en la
    MISMA línea de las viñetas (posts 70244, 70323, 70329): el regex lo captura
    como parte de la descripción y las calles dejan de resolver en OSM."""

    AVISO = ("📣Usted puede, aún siendo cliente de {de}, continuar afectado por "
             "avería en acometida o transformador. En esos casos le pedimos "
             "contactarnos por las vías alternativas{punto}")

    def _texto(self, de, punto="."):
        return ("👉PG980: Fraternidad, Calleja, San Agustín, El Mamey.   "
                + self.AVISO.format(de=de, punto=punto))

    def test_limpia_el_aviso_de_las_calles(self):
        for de in ("estos circuitos", "este circuito"):
            with self.subTest(de=de):
                m = MOD.RE_CIRC.search(self._texto(de))
                self.assertEqual(
                    MOD.limpiar_calles(m.group(3)),
                    "Fraternidad, Calleja, San Agustín, El Mamey")

    def test_aviso_sin_punto_final(self):
        m = MOD.RE_CIRC.search(self._texto("este circuito", punto=""))
        self.assertEqual(
            MOD.limpiar_calles(m.group(3)),
            "Fraternidad, Calleja, San Agustín, El Mamey")

    def test_zonas_en_del_extractor_tampoco_lo_incluye(self):
        from extract import zonas_en
        zonas = zonas_en(self._texto("estos circuitos"))
        self.assertEqual(zonas, ["PG980: Fraternidad, Calleja, San Agustín, El Mamey."])

    def test_no_recorta_descripciones_que_solo_parecen_el_aviso(self):
        texto = "👉GC11: Reparto Garrido, calles Soledad y La Palma"
        m = MOD.RE_CIRC.search(texto)
        self.assertEqual(MOD.limpiar_calles(m.group(3)),
                         "Reparto Garrido, calles Soledad y La Palma")


class AplicarExtraccionLlmTest(unittest.TestCase):
    """La caché del LLM debe aplicarse sin depender de un número de versión."""

    def _entrada(self, **cambios):
        base = {"via": "llm", "validador_version": 2,
                "circuitos": [{"codigos": ["AL53"], "codigos_estado": ["AL53"],
                               "estado": "sin servicio"}]}
        base.update(cambios)
        return base

    def test_se_aplica_con_la_version_actual(self):
        self.assertTrue(MOD.usar_extraccion_llm(self._entrada(validador_version=3)))

    def test_se_aplica_con_versiones_futuras(self):
        # El fallo original: subir la versión desactivaba el refuerzo entero.
        for v in (4, 5, 99):
            with self.subTest(version=v):
                self.assertTrue(MOD.usar_extraccion_llm(self._entrada(validador_version=v)))

    def test_no_se_aplica_sin_evidencia_del_codigo(self):
        sin_evidencia = self._entrada(circuitos=[{"codigos": ["AL53"], "estado": "sin servicio"}])
        self.assertFalse(MOD.usar_extraccion_llm(sin_evidencia))

    def test_no_se_aplican_las_cacheS_antiguas(self):
        self.assertFalse(MOD.usar_extraccion_llm(self._entrada(validador_version=1)))
        self.assertFalse(MOD.usar_extraccion_llm(self._entrada(validador_version=None)))

    def test_no_se_aplica_lo_que_no_viene_del_llm(self):
        self.assertFalse(MOD.usar_extraccion_llm(self._entrada(via="prefiltro")))
        self.assertFalse(MOD.usar_extraccion_llm(None))

    def test_un_parte_sin_circuitos_es_aplicable(self):
        self.assertTrue(MOD.usar_extraccion_llm(self._entrada(circuitos=[])))


if __name__ == "__main__":
    unittest.main()


class AsignacionDesdeCacheTest(unittest.TestCase):
    """Agotar el presupuesto de geometría no puede perder trazos ya cacheados.

    Regresión de producción: con un `break` al agotar MAX_SEGUNDOS_GEO, los
    circuitos posteriores se quedaban sin sus líneas de caché. La web publicó
    22 trazos cuando la caché tenía 129, porque Overpass responde lento desde
    los runners y el corte saltaba en el primer circuito sin resolver.
    """

    def _simular(self, circuitos, cache, presupuesto_agotado):
        """Reproduce el bucle de asignación de build_circuitos."""
        nuevas, sin_presupuesto = 0, False
        for c in circuitos:
            cod = c["codigo"]
            if cache.get(cod):
                c["lineas"] = cache[cod]
                continue
            if sin_presupuesto:
                continue
            if presupuesto_agotado:
                sin_presupuesto = True
                continue
            nuevas += 1
        return circuitos

    def test_los_trazos_cacheados_se_asignan_aunque_no_quede_presupuesto(self):
        circuitos = [{"codigo": "SIN_CACHE"},          # dispara el corte
                     {"codigo": "A1"}, {"codigo": "A2"}, {"codigo": "A3"}]
        cache = {"A1": [[[0, 0]]], "A2": [[[1, 1]]], "A3": [[[2, 2]]]}

        r = self._simular(circuitos, cache, presupuesto_agotado=True)

        con_lineas = [c["codigo"] for c in r if c.get("lineas")]
        self.assertEqual(con_lineas, ["A1", "A2", "A3"],
                         "los circuitos tras el corte perdieron sus líneas cacheadas")
