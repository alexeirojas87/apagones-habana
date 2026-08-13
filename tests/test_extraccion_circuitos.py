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
