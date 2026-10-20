"""Estructura de la capa de geometrías de circuitos.

Verifica el GeoJSON generado por scripts/build_circuitos_geometrias.py: forma
FeatureCollection, códigos normalizados, polígonos válidos dentro del bbox de
La Habana, coordenadas con 6 decimales y base Info UNE suficiente. También
guarda contra la desaparición accidental del render de polígonos en app.js.

Se ejecuta con la stdlib: python3 tests/test_geometrias_circuitos.py
"""

import json
import os
import unittest
from decimal import Decimal

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEOJSON = os.path.join(RAIZ, "web", "data", "circuitos_geometrias.geojson")
APP_JS = os.path.join(RAIZ, "web", "app.js")

LON_MIN, LON_MAX = -82.60, -82.00
LAT_MIN, LAT_MAX = 22.90, 23.30
MAX_DECIMALES = 6


def _posiciones(geometria):
    """Aplana las posiciones [lon, lat(, ...)] de cualquier Polygon/MultiPolygon."""
    tipo = geometria.get("type")
    coords = geometria.get("coordinates")
    if tipo == "Polygon":
        anillos = coords
    elif tipo == "MultiPolygon":
        anillos = [anillo for poligono in coords for anillo in poligono]
    else:
        return []
    return [pos for anillo in anillos for pos in anillo]


def _decimales(valor):
    """Cantidad de decimales significativos del número tal como se serializó."""
    exponente = Decimal(str(valor)).as_tuple().exponent
    return max(0, -exponente)


class GeometriasCircuitosTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(GEOJSON, encoding="utf-8") as fh:
            cls.datos = json.load(fh)

    def test_es_featurecollection(self):
        self.assertEqual(self.datos.get("type"), "FeatureCollection")
        self.assertIsInstance(self.datos.get("features"), list)

    def test_cantidad_de_features_y_codigos(self):
        features = self.datos["features"]
        codigos = {
            c for f in features for c in f["properties"]["codigos"]
        }
        self.assertGreaterEqual(len(features), 190)
        self.assertGreaterEqual(len(codigos), 190)

    def test_propiedades_normalizadas(self):
        for f in self.datos["features"]:
            props = f["properties"]
            codigos = props.get("codigos")
            self.assertIsInstance(codigos, list, f["geometry"]["type"])
            self.assertTrue(codigos, "feature sin códigos")
            for c in codigos:
                self.assertIsInstance(c, str)
                self.assertEqual(c, c.strip().upper())
                self.assertTrue(c)
            self.assertEqual(props.get("codigo"), codigos[0])
            self.assertTrue(props.get("fuente"))
            self.assertIsInstance(props.get("provisional"), bool)

    def test_geometrias_poligonales_en_la_habana(self):
        for f in self.datos["features"]:
            geom = f["geometry"]
            self.assertIn(geom["type"], ("Polygon", "MultiPolygon"))
            posiciones = _posiciones(geom)
            self.assertTrue(posiciones, "polígono sin posiciones")
            for pos in posiciones:
                lon, lat = pos[0], pos[1]
                self.assertIsInstance(lon, (int, float))
                self.assertIsInstance(lat, (int, float))
                self.assertGreaterEqual(lon, LON_MIN)
                self.assertLessEqual(lon, LON_MAX)
                self.assertGreaterEqual(lat, LAT_MIN)
                self.assertLessEqual(lat, LAT_MAX)

    def test_coordenadas_con_seis_decimales_como_maximo(self):
        for f in self.datos["features"]:
            for pos in _posiciones(f["geometry"]):
                for valor in pos[:2]:
                    self.assertLessEqual(
                        _decimales(valor), MAX_DECIMALES, f"{valor} en {pos}"
                    )

    def test_base_info_une_suficiente(self):
        base = [f for f in self.datos["features"]
                if not f["properties"]["provisional"]]
        self.assertGreaterEqual(len(base), 180)

    def test_app_js_renderiza_los_poligonos(self):
        with open(APP_JS, encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn("circuitos_geometrias.geojson", js)
        self.assertIn("porCodigo", js)


if __name__ == "__main__":
    unittest.main()
