import importlib.util
import unittest
from pathlib import Path


RUTA = Path(__file__).parents[1] / "extractor" / "extract.py"
SPEC = importlib.util.spec_from_file_location("extract", RUTA)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class DesconexionSenTest(unittest.TestCase):
    def test_aviso_real_inicia_evento(self):
        texto = "22:43 || Ocurre una desconexión total del Sistema Electroenergético Nacional."
        self.assertTrue(MOD.es_inicio_desconexion_total(texto))
        eventos = MOD.extraer_de_post(texto)
        self.assertEqual(len(eventos), 6)

    def test_parte_de_recuperacion_no_reinicia_evento(self):
        texto = (
            "Informamos que, tras la desconexión total del SEN, se han "
            "restablecido 66 circuitos de distribución."
        )
        self.assertFalse(MOD.es_inicio_desconexion_total(texto))
        self.assertNotEqual(MOD.causa_en(texto), "desconexión total del SEN")
        eventos = MOD.extraer_de_post(texto)
        self.assertEqual([e["tipo"] for e in eventos], ["restablecimiento"])
        self.assertNotIn("desconexión total del SEN", {e["causa"] for e in eventos})


if __name__ == "__main__":
    unittest.main()
