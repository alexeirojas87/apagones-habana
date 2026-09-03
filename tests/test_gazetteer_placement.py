"""Ubicación de circuitos por gaceta de barrios (sin red) y jubilación del
centroide 'centro municipio'.

El explore simuló que un gazetteer-first ingenuo pinta PEOR que los centroides
('reparto'→Reparto Inav, 'santa'→Santa María del Mar, 'monte'→Monterrey). Por
eso cada hit tiene que pasar dos gates: normalización de stopwords/frases con
rechazo de genéricos, y la caja/dentro de autoridad del circuito (D1050: el
texto dice 'Agromar', pero Agromar está en Habana del Este y el circuito es de
Guanabacoa -> nunca se pinta). Sin polys de autoridad no hay gaceta: 'sin
ubicar' honesto reemplaza al centroide (decisión 1 del owner).
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).parents[1]
SCRIPTS = RAIZ / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("verificar_datos", SCRIPTS / "verificar_datos.py")
VD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VD)
import evidencia_calles as evc  # noqa: E402

# Polígonos oficiales REALES (web/data/municipios.geojson, commiteado): la
# autoridad se gates por municipio como en producción.
_GJ = json.load(open(RAIZ / "web" / "data" / "municipios.geojson"))
MUNIS = {}
for _f in _GJ["features"]:
    _g = _f["geometry"]
    MUNIS[_f["properties"]["municipio"]] = (
        [_g["coordinates"][0]] if _g["type"] == "Polygon"
        else [p[0] for p in _g["coordinates"]])


def dentro_de(*nombres):
    anillos = [a for n in nombres for a in MUNIS[n]]
    return lambda la, lo: any(VD._en_poly(la, lo, a) for a in anillos)


# (texto del parte, municipio de autoridad, canónico esperado) — los 9 textos
# de la spec 'Normalized gazetteer matching'.
TABLA_GACETA = [
    ("Cojímar: calles 32 hasta Victoria y desde Calixto García", "Habana del Este",
     "cojimar"),
    # GC15: las entradas commiteadas ('Chibás' y 'Reparto Chibás') caen del lado
    # Guanabacoa de la frontera municipal (por eso existe el alias
    # lugares_manual, decisión 3). Aquí se prueba el mecanismo bajo su propia
    # autoridad; el archivo trae primero el punto 'Chibás', que gana por orden.
    ("Cojímar y Comunidad Guamá, Bahía", "Habana del Este", "cojimar"),
    ("Reparto Chibás: calles 224 desde Cruz del Río hasta Panamericana",
     "Guanabacoa", "chibas"),
    ("El Globo, La Chivera", "Boyeros", "el globo"),
    ("Mulgoba, Jesús Menéndez, Ampliación Mulgoba, parte de Sierra Maestra",
     "Boyeros", "mulgoba"),
    ("Punta Brava, El Guatao y Estrella Roja.", "La Lisa", "punta brava"),
    ("XX Aniversario", "La Lisa", "xx aniversario"),
    ("Altura de Lotería.", "Cotorro", "loteria"),
    ("Alturas de Lotería", "Cotorro", "loteria"),
    # CCP20: el PUNTO 'Atarés' cae en Cerro y el polígono del reparto en Habana
    # Vieja -> el ring interior gana solo cuando el punto falla el gate.
    ("Terminal de Trenes, Ensenada de Atare, Ave La Pequera desde Alambique Luz, "
     "Egido hasta Oficio, Desamparado hasta Acosta", "Habana Vieja", "atares"),
]


class TablaGacetaTest(unittest.TestCase):
    def test_los_textos_de_la_spec_resuelven_en_su_municipio(self):
        for texto, muni, esperado in TABLA_GACETA:
            with self.subTest(texto=texto[:40]):
                hit = evc._lugar_gazetteer(texto, dentro_de(muni))
                self.assertIsNotNone(hit, "la gaceta debía resolver")
                self.assertEqual(hit["candidato"], esperado)
                self.assertEqual(hit["match"], "gaceta de barrios")
                la, lo = hit["lat"], hit["lon"]
                self.assertTrue(dentro_de(muni)(la, lo),
                                "el punto devuelto cae dentro de la autoridad")


class GateAutoridadTest(unittest.TestCase):
    def test_gc15_bajo_autoridad_he_el_punto_no_cruza(self):
        # La misma entrada, con la autoridad correcta de GC15 (Habana del Este):
        # el punto commiteado cae en Guanabacoa -> el gate lo rechaza y no se
        # pinta. GC15 se resuelve en producción por el alias manual, no por aquí.
        self.assertIsNone(evc._lugar_gazetteer(
            "Reparto Chibás", dentro_de("Habana del Este")))

    def test_agromar_fuera_de_autoridad_es_sin_ubicar(self):
        # D1050 (Guanabacoa) menciona 'Agromar', cuyo punto está en Habana del
        # Este: jamás se pinta fuera de la autoridad.
        self.assertIsNone(evc._lugar_gazetteer("Agromar", dentro_de("Guanabacoa")))
        hit = evc._lugar_gazetteer("Agromar", dentro_de("Habana del Este"))
        self.assertIsNotNone(hit, "triangulación: con su autoridad sí resuelve")
        self.assertEqual(hit["candidato"], "agromar")

    def test_sin_polys_no_hay_gaceta(self):
        # Circuito sin municipio oficial: no hay a qué gatear -> None (decisión 1).
        self.assertIsNone(evc._lugar_gazetteer("Cojímar: calles 32 hasta Victoria", None))


class GenericosTest(unittest.TestCase):
    def test_tokens_genericos_no_resuelven_nada(self):
        # La simulación del explore: un gazetteer-first ingenuo pintaba estos
        # cinco en Reparto Inav, Santa María del Mar, Monterrey y Centro Habana.
        for texto in ("Reparto", "Santa", "Monte", "Centro", "Montes"):
            with self.subTest(texto=texto):
                self.assertIsNone(evc._lugar_gazetteer(texto, dentro_de(
                    "Habana del Este", "Cotorro", "San Miguel del Padrón",
                    "Centro Habana", "La Lisa")))

    def test_prado_desde_monte_nunca_es_monterrey(self):
        # 'monte' es genérico y no puede prestar su prefijo a 'Monterrey'
        # (el trap que el explore contó cinco veces).
        hit = evc._lugar_gazetteer("Prado desde Monte hasta Malecón",
                                   dentro_de("San Miguel del Padrón"))
        self.assertIsNone(hit)


class AmbiguedadTest(unittest.TestCase):
    def test_un_mismo_nombre_partido_lejos_no_elige(self):
        # Hermetico con indice inyectado: dos entradas de canonicos DISTINTOS
        # comparten la variante 'prueba' y sus puntos caen a 14 km (Loteria en
        # Cotorro, Lutgardita en Boyeros) dentro de la autoridad combinada.
        # Una mencion que podria ser cualquiera de los dos es ambiguedad.
        real = evc.gaceta_entries()
        orig = evc._GACETA
        self.addCleanup(setattr, evc, "_GACETA", orig)
        lot = (23.0375981, -82.2517666)
        lut = (23.0013757, -82.3855819)
        evc._GACETA = {
            "loteria": {"variantes": {"prueba"}, "puntos": [lot], "anillo": None},
            "reparto lutgardita": {"variantes": {"prueba"}, "puntos": [lut],
                                   "anillo": None},
        }
        self.assertIsNone(evc._lugar_gazetteer(
            "Prueba", dentro_de("Cotorro", "Boyeros")))
        evc._GACETA = real

    def test_multiples_nombres_la_primera_mencion_manda(self):
        # A1440 dice 'Mulgoba, ...' y el parte cubre tambien 'parte de Sierra
        # Maestra' (otro barrio a 3.1 km): no es moneda, es la convencion del
        # parte — el primer nombre es el referente. GC7 arriba prueba lo mismo.
        hit = evc._lugar_gazetteer("Altura de Lotería, Reparto Lutgardita",
                                   dentro_de("Cotorro", "Boyeros"))
        self.assertEqual(hit["candidato"], "loteria")

    def test_cada_uno_de_los_dos_por_separado_si_resuelve(self):
        self.assertEqual(
            evc._lugar_gazetteer("Altura de Lotería", dentro_de("Cotorro"))["candidato"],
            "loteria")
        self.assertEqual(
            evc._lugar_gazetteer("Reparto Lutgardita",
                                 dentro_de("Boyeros"))["candidato"],
            "reparto lutgardita")

    def test_exacta_gana_al_prefijo_xx_aniversario(self):
        # 'XX Aniversario' exacto no puede arrastrar a 'XX Aniversario del
        # Granma' (otro barrio 2.6 km al este): la coincidencia exacta cierra
        # el token antes de los prefijos.
        hit = evc._lugar_gazetteer("XX Aniversario", dentro_de("La Lisa"))
        self.assertEqual(hit["candidato"], "xx aniversario")


class PuntoAntesQueAnilloTest(unittest.TestCase):
    def test_el_punto_osm_gana_al_centro_del_anillo(self):
        hit = evc._lugar_gazetteer("Reparto Chibás", dentro_de("Guanabacoa"))
        # El punto commiteado 'Chibás' (23.1348,-82.3084) no es el centro del
        # anillo de 'Reparto Chibás' (~23.1317,-82.3086): coordenadas distintas
        # prueban que el punto OSM manda sobre el interior del polígono.
        self.assertAlmostEqual(hit["lat"], 23.1347602, delta=1e-4)
        self.assertAlmostEqual(hit["lon"], -82.3083861, delta=1e-4)


class EtiquetaTest(unittest.TestCase):
    def test_gaceta_de_barrios_es_match_generica(self):
        self.assertTrue(evc.match_generica("gaceta de barrios"),
                        "derivada del propio texto: cuenta como evidencia independiente")
        self.assertFalse(evc.match_generica("Santiago"), "un POI concreto no es genérica")


class EstadoSinCentroideTest(unittest.TestCase):
    """El bloque del centroide (L468-471) se jubila: sin hit de pistas ni gaceta,
    la clave se cachea None ('sin ubicar'); con gaceta, 'gaceta de barrios'."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache_path = os.path.join(self.tmp.name, "geocache.json")
        json.dump({}, open(self.cache_path, "w"))
        import estado
        self.E = estado
        for p in (
            mock.patch.object(estado, "CACHE_AVERIAS", self.cache_path),
            mock.patch.object(estado, "nominatim", lambda q, caja: None),
            mock.patch("time.sleep", lambda s: None),
        ):
            p.start()
            self.addCleanup(p.stop)

    def _geo(self, direccion, muni):
        it = {"municipio": muni, "direccion": direccion, "polys": MUNIS[muni]}
        out = self.E.geocodificar_averias([it], {}, solo_lugar=True)[0]
        return out, json.load(open(self.cache_path))[evc.clave_cache(direccion)]

    def test_resuelto_por_gaceta_etiqueta_gaceta_de_barrios(self):
        out, guardado = self._geo("Altura de Lotería.", "Cotorro")
        self.assertEqual(guardado["match"], "gaceta de barrios")
        self.assertEqual((out["lat"], out["lon"]), (23.0375981, -82.2517666))

    def test_sin_resolucion_cachea_none_sin_centroide(self):
        # VC100 'Managua, Molinet y El Volcán': nombres sin fila en la gaceta
        # (decisión 4). Antes caía al centroide AN; ahora 'sin ubicar'.
        out, guardado = self._geo("Managua, Molinet y El Volcán", "Arroyo Naranjo")
        self.assertIsNone(guardado, "None cacheado es la representación de 'sin ubicar'")
        self.assertNotIn("lat", out)
        self.assertNotIn("centro municipio", [guardado])


if __name__ == "__main__":
    unittest.main()
