import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
RUTA = SCRIPTS / "partes_llm.py"
SPEC = importlib.util.spec_from_file_location("partes_llm", RUTA)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class ValidacionPartesLlmTest(unittest.TestCase):
    def test_arranque_de_cte_no_restablece_circuitos(self):
        texto = (
            "Actualización sobre el restablecimiento SEN.\n"
            "Iniciando el arranque de unidades de las CTE Ernesto Guevara, "
            "Máximo Gómez y Carlos Manuel de Céspedes.\n"
            "Preparadas para arrancar unidades de Diez de Octubre, Felton y "
            "Antonio Guiteras."
        )
        extraccion = {
            "tipo": "restablecimiento",
            "circuitos": [
                {"codigo": None, "calles": "Máximo Gómez", "estado": "con servicio"},
                {"codigo": None, "calles": "Carlos Manuel de Céspedes", "estado": "con servicio"},
                {"codigo": None, "calles": "Diez de Octubre", "estado": "con servicio"},
                {"codigo": None, "calles": "Antonio Guiteras", "estado": "con servicio"},
            ],
        }

        resultado = MOD.validar(extraccion, texto)

        self.assertEqual(resultado["circuitos"], [])

    def test_coincidencia_por_calles_no_cambia_estado(self):
        extraccion = {
            "tipo": "restablecimiento",
            "circuitos": [
                {"codigo": None, "calles": "Máximo Gómez", "estado": "con servicio"},
            ],
        }

        resultado = MOD.validar(extraccion, "Se informa una actualización general.")
        item = resultado["circuitos"][0]

        self.assertEqual(item["codigos_estado"], [])
        self.assertIsNone(item["estado"])

    def test_codigo_explicito_si_puede_cambiar_estado(self):
        extraccion = {
            "tipo": "restablecimiento",
            "circuitos": [
                {"codigo": "AL53", "calles": None, "estado": "con servicio"},
            ],
        }

        resultado = MOD.validar(
            extraccion, "Queda restablecido el servicio eléctrico del circuito AL53."
        )
        item = resultado["circuitos"][0]

        self.assertEqual(item["codigos_estado"], ["AL53"])
        self.assertEqual(item["estado"], "con servicio")


if __name__ == "__main__":
    unittest.main()
