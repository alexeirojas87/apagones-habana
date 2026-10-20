"""Alineación del catálogo con la capa de geometrías (regresión de P15 y GC12)."""

import json
import sys
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "scripts"))

import alinear_circuitos_geometria as A  # noqa: E402


class AlinearCircuitosTest(unittest.TestCase):
    def test_p15_sale_del_malecon(self):
        circuitos = [{
            "codigo": "P15", "municipio": "Plaza",
            "lat": 23.12478, "lon": -82.39148,
        }]
        stats = A.alinear(circuitos)
        self.assertEqual(stats["manual"], 1)
        self.assertEqual((circuitos[0]["lat"], circuitos[0]["lon"]), (23.116, -82.3915))
        self.assertEqual(circuitos[0]["municipio"], "Plaza")

    def test_centroide_de_poligono_y_municipio(self):
        with open(RAIZ / "web" / "data" / "circuitos_geometrias.geojson", encoding="utf-8") as f:
            features = json.load(f)["features"]
        gc12 = [f for f in features if "GC12" in f["properties"]["codigos"]]
        self.assertTrue(gc12)
        circuitos = [{
            "codigo": "GC12", "municipio": "Playa",
            "lat": 23.10, "lon": -82.40,
        }]
        A.alinear(circuitos)
        self.assertEqual(circuitos[0]["municipio"], gc12[0]["properties"]["municipio"])
        rings = [r for f in gc12 for r in A.anillos(f)]
        self.assertTrue(any(A.en_poly(circuitos[0]["lat"], circuitos[0]["lon"], r) for r in rings))

    def test_alias_elimina_duplicados(self):
        circuitos = [
            {"codigo": "GC12", "municipio": "Habana del Este", "lat": 23.15, "lon": -82.30},
            {"codigo": "CG12", "municipio": "Habana del Este", "lat": 23.16, "lon": -82.29,
             "estado": "con servicio"},
            {"codigo": "PZ15", "municipio": "Plaza", "lat": 23.12, "lon": -82.39},
            {"codigo": "P15", "municipio": "Plaza", "lat": 23.12, "lon": -82.39},
            {"codigo": "R453", "municipio": "Regla", "lat": 23.13, "lon": -82.33},
            {"codigo": "R543", "municipio": "Regla", "lat": 23.13, "lon": -82.33},
        ]
        A.alinear(circuitos)
        codigos = {c["codigo"] for c in circuitos}
        self.assertIn("GC12", codigos)
        self.assertIn("PZ15", codigos)
        self.assertIn("R453", codigos)
        self.assertNotIn("CG12", codigos)
        self.assertNotIn("P15", codigos)
        self.assertNotIn("R543", codigos)

    def test_sin_poligono_no_se_toca(self):
        circuitos = [{"codigo": "NO_EXISTE", "municipio": "Plaza", "lat": 23.1, "lon": -82.4}]
        A.alinear(circuitos)
        self.assertEqual((circuitos[0]["lat"], circuitos[0]["lon"]), (23.1, -82.4))


if __name__ == "__main__":
    unittest.main()
