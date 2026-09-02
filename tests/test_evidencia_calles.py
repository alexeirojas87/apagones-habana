"""Chequeo 10 ('punto lejos de sus propias calles', sin red) y la guardia del
atajo LUGARES_MANUAL: evidencia sintética en memoria, cero red.

Cubren la clase raíz auditada en manual: hits 'circ|' envenenados por POIs
homónimos que el control cruzado de estado.py nunca re-valida (CCP20) y las
trampas que el gate automático no debe morder: familias de un solo POI
('El Trébol' / 'Embalse La Coca'), twins que se acusan entre sí, y geometría
envenenada alrededor de un punto respaldado por autoridad (L317/PZ13).
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("verificar_datos", SCRIPTS / "verificar_datos.py")
VD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VD)
import evidencia_calles as evc  # noqa: E402


def circ(codigo, lat, lon, calles):
    return {"codigo": codigo, "lat": lat, "lon": lon, "calles": calles}


def entrada(i, nombre, lat, lon):
    """Hermano cacheado: clave 'circ|' con las dos calles testigo + relleno."""
    return (f"circ|{nombre}, Callehermana Uno, Callehermana Dos",
            {"lat": lat, "lon": lon, "match": f"POI {nombre}"})


# Geometría sintética: punto pintado lejos, evidencia buena en 'la zona'.
PUNTO_LEJOS = (23.0700, -82.5180)   # Playa/Santa Fe
PUNTO_BUENO = (23.1350, -82.3550)   # Habana Vieja/Cerro
CALLES = "Callehermana Uno desde Veintidos hasta Treinta, Callehermana Dos"
SIN_AUT = lambda c: []


def hermanos_al_zona(n, prefijo="Vecino"):
    return dict(entrada(i, f"{prefijo} {i}", *PUNTO_BUENO) for i in range(n))


class TokensTest(unittest.TestCase):
    def test_ignoran_numero_generico_anio_y_parentesis(self):
        toks = evc.tokens_distintivos(
            "calle 9na desde 76 hasta 84; Ave La Pequera desde 2024, "
            "Egido hasta Oficio (Almendares)")
        self.assertIn("ave la pequera", toks)
        self.assertIn("egido", toks)
        for basura in ("calle 9na", "76", "84", "2024", "almendares"):
            self.assertNotIn(basura, toks)

    def test_regla_alamar(self):
        self.assertTrue(evc.ALAMAR.search("Zonas: 1, 2 y 3 del Alamar"))


class VeredictosTest(unittest.TestCase):
    def test_racimo_mayoritario_niega_el_punto_y_purga_el_hit(self):
        cache = dict(hermanos_al_zona(5))
        clave = evc.clave_cache(CALLES)
        cache[clave] = {"lat": PUNTO_LEJOS[0], "lon": PUNTO_LEJOS[1],
                        "match": "Metro Taxis Santa Fe - Terminal de trenes"}
        probs, pg, pl, pi = VD.chequeo_evidencia_calles(
            [circ("TGT", *PUNTO_LEJOS, CALLES)], SIN_AUT, cache, {})
        self.assertEqual([t for t, _ in probs], ["punto lejos de sus calles"])
        self.assertEqual(pg, {"TGT"})   # el hit guardado es el veneno
        self.assertEqual((pl, pi), (set(), set()))

    def test_sin_clave_circ_no_hay_purga_reportable(self):
        # PG940: el punto viene de un atajo, no de la caché -> se reporta solo.
        cache = dict(hermanos_al_zona(5))
        probs, pg, pl, pi = VD.chequeo_evidencia_calles(
            [circ("TGT", *PUNTO_LEJOS, CALLES)], SIN_AUT, cache, {})
        self.assertEqual(len(probs), 1)
        self.assertEqual(pg, set())

    def test_una_sola_familia_de_poi_no_convence(self):
        # D836/'El Trébol': 4 hermanos lejanos comparten UN POI homónimo.
        cache = dict(hermanos_al_zona(4))
        probs, pg, *_ = VD.chequeo_evidencia_calles(
            [circ("TGT", *PUNTO_LEJOS, CALLES)], SIN_AUT, cache, {})
        self.assertEqual((probs, pg), ([], set()))

    def test_la_mayoria_coincidente_con_el_punto_gana(self):
        # R456/PG980: el racimo que coincide con el punto es más grande que el
        # contrario -> silencio (los twins no se purgan entre sí).
        cache = dict(hermanos_al_zona(5))
        cache.update({
            f"circ|Coincidente {i}, Callehermana Uno, Callehermana Dos":
                {"lat": PUNTO_LEJOS[0], "lon": PUNTO_LEJOS[1], "match": ""}
            for i in range(6)})
        probs, pg, *_ = VD.chequeo_evidencia_calles(
            [circ("TGT", *PUNTO_LEJOS, CALLES)], SIN_AUT, cache, {})
        self.assertEqual((probs, pg), ([], set()))

    def test_lineas_propias_negando_sin_autoridad_purgan_el_punto(self):
        # L323: hit-POI en el punto malo, geometría real lejos, sin autoridad.
        lineas = {"TGT": [[[PUNTO_BUENO[1] + d, PUNTO_BUENO[0] + e]
                           for d, e in ((0, 0), (0.001, 0), (0, 0.001))]]}
        cache = {evc.clave_cache(CALLES): {"lat": PUNTO_LEJOS[0],
                                           "lon": PUNTO_LEJOS[1],
                                           "match": "Almendares"}}
        probs, pg, pl, pi = VD.chequeo_evidencia_calles(
            [circ("TGT", *PUNTO_LEJOS, CALLES)], SIN_AUT, cache, lineas)
        self.assertEqual([t for t, _ in probs], ["punto lejos de sus calles"])
        self.assertEqual(pg, {"TGT"})
        self.assertEqual((pl, pi), (set(), set()))

    def test_hit_bare_no_dispara_la_regla_de_lineas(self):
        # SF584/C11: mismo cuadro pero el hit no tiene match-POI (legacy) ->
        # report-only jamás; ni se reporta (gate conservador).
        lineas = {"TGT": [[[PUNTO_BUENO[1] + d, PUNTO_BUENO[0] + e]
                           for d, e in ((0, 0), (0.001, 0), (0, 0.001))]]}
        cache = {evc.clave_cache(CALLES): {"lat": PUNTO_LEJOS[0], "lon": PUNTO_LEJOS[1]}}
        probs, pg, *_ = VD.chequeo_evidencia_calles(
            [circ("TGT", *PUNTO_LEJOS, CALLES)], SIN_AUT, cache, lineas)
        self.assertEqual((probs, pg), ([], set()))

    def test_punto_con_autoridad_purga_lineas_e_intentos(self):
        # L317/PZ13: el punto manda (autoridad); la geometría en caché es la
        # envenenada -> se purga línea + contador de intentos, no el punto.
        lineas = {"TGT": [[[PUNTO_BUENO[1] + d, PUNTO_BUENO[0] + e]
                           for d, e in ((0, 0), (0.001, 0), (0, 0.001))]]}
        cache = {evc.clave_cache(CALLES): {"lat": PUNTO_LEJOS[0],
                                           "lon": PUNTO_LEJOS[1],
                                           "match": "Almendares"}}
        probs, pg, pl, pi = VD.chequeo_evidencia_calles(
            [circ("TGT", *PUNTO_LEJOS, CALLES)], lambda c: ["Playa"], cache, lineas)
        self.assertEqual([t for t, _ in probs], ["líneas de caché lejos del punto"])
        self.assertEqual(pl, {"TGT"})
        self.assertEqual(pi, {"TGT"})
        self.assertEqual(pg, set())

    def test_alamar_y_sin_punto_quedan_silenciosos(self):
        probs, pg, pl, pi = VD.chequeo_evidencia_calles(
            [circ("AL", *PUNTO_LEJOS, "Zonas: 1, 2 y 3"),
             circ("SIN", None, None, CALLES),
             circ("VACIO", *PUNTO_LEJOS, "")], SIN_AUT,
            dict(hermanos_al_zona(5)), {})
        self.assertEqual((probs, pg, pl, pi), ([], set(), set(), set()))


class GuardiaManualTest(unittest.TestCase):
    def test_tres_concordantes_lejanos_contradicen_el_manual(self):
        entradas = evc.entradas_hermanas(hermanos_al_zona(3))
        self.assertTrue(evc.contradice_evidencia(
            PUNTO_LEJOS, CALLES, entradas, {},
            minimo=evc.MIN_CONCORDANTES_GUARDIA))

    def test_dos_no_bastan_y_coincidente_nada(self):
        delgados = evc.entradas_hermanas(hermanos_al_zona(2))
        self.assertFalse(evc.contradice_evidencia(
            PUNTO_LEJOS, CALLES, delgados, {},
            minimo=evc.MIN_CONCORDANTES_GUARDIA))
        # el respaldo propio del candidato (hermanos que caen EN su punto) es
        # cluster coincidente y no puede negarlo.
        cerca = {"circ|Cerca, Callehermana Uno, Callehermana Dos":
                 {"lat": PUNTO_LEJOS[0] + 0.001, "lon": PUNTO_LEJOS[1]}}
        self.assertFalse(evc.contradice_evidencia(
            PUNTO_LEJOS, CALLES, evc.entradas_hermanas(cerca), {},
            minimo=evc.MIN_CONCORDANTES_GUARDIA))


class FlujoEstadoTest(unittest.TestCase):
    """geocodificar_averias con el atajo manual: la pista entre paréntesis pasa
    sin gate; el primer nombre se anula si la evidencia lo contradice; las
    averías (solo_lugar=False) no cambian de comportamiento."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache_path = os.path.join(self.tmp.name, "geocache.json")
        self.lin_path = os.path.join(self.tmp.name, "inexistente.json")
        import estado
        self.E = estado
        self._manual = estado.LUGARES_MANUAL
        estado.LUGARES_MANUAL = {"fingido": {"lat": 23.0700, "lon": -82.5180}}
        self.addCleanup(setattr, estado, "LUGARES_MANUAL", self._manual)
        self._patches = [
            mock.patch.object(estado, "CACHE_AVERIAS", self.cache_path),
            mock.patch.object(evc, "CACHE_GEO", self.cache_path),
            mock.patch.object(evc, "CACHE_LINEAS", self.lin_path),
            mock.patch.object(estado, "nominatim",
                              lambda q, caja: {"lat": 23.1350, "lon": -82.3550,
                                               "match": "evidencia test"}),
            mock.patch("time.sleep", lambda s: None),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)
        json.dump(hermanos_al_zona(3), open(self.cache_path, "w"))

    def _geo(self, it, solo_lugar):
        return self.E.geocodificar_averias([it], {}, solo_lugar=solo_lugar)[0]

    def test_primer_nombre_contradicho_cae_al_flujo_circ(self):
        it = {"municipio": "X", "direccion":
              "Fingido, Callehermana Uno, Callehermana Dos"}
        out = self._geo(it, True)
        self.assertEqual((out["lat"], out["lon"]), PUNTO_BUENO,
                         "el atajo manual no debe pisar la evidencia")
        cache = json.load(open(self.cache_path))
        guardado = cache[evc.clave_cache("Fingido, Callehermana Uno, Callehermana Dos")]
        self.assertEqual((guardado["lat"], guardado["lon"]), PUNTO_BUENO)

    def test_pista_parentetica_pasa_sin_gate(self):
        it = {"municipio": "X", "direccion":
              "Callehermana Uno, Callehermana Dos (Fingido)"}
        out = self._geo(it, True)
        self.assertEqual((out["lat"], out["lon"]), PUNTO_LEJOS,
                         "la intención del autor manda en la pista del autor")

    def test_averias_sin_solo_lugar_no_cambian(self):
        it = {"municipio": "X", "direccion":
              "Fingido, Callehermana Uno, Callehermana Dos"}
        out = self._geo(it, False)
        self.assertEqual((out["lat"], out["lon"]), PUNTO_LEJOS,
                         "solo_lugar=False conserva el comportamiento original")


class TestChivasVerdadLocal(unittest.TestCase):
    """GC15: el reparto Chivás (oeste de Habana del Este) queda envenenado por el
    fallback 'centroide municipio' 11 km al este. La verdad local de
    lugares_manual debe resolverlo sin red aunque la caché esté vacía, y sin
    que la guardia del primer segmento lo frene (aquí no hay evidencia que lo
    contradiga: el punto manual ES la evidencia)."""

    def setUp(self):
        import estado
        self.E = estado
        d = tempfile.mkdtemp()
        cache_path = os.path.join(d, "geocache.json")
        lin_path = os.path.join(d, "lineas.json")
        json.dump({}, open(cache_path, "w"))
        json.dump({}, open(lin_path, "w"))
        for p in (
            mock.patch.object(estado, "CACHE_AVERIAS", cache_path),
            mock.patch.object(evc, "CACHE_GEO", cache_path),
            mock.patch.object(evc, "CACHE_LINEAS", lin_path),
            mock.patch.object(estado, "nominatim",
                              lambda q, caja: (_ for _ in ()).throw(
                                  AssertionError("red prohibida en este test"))),
            mock.patch("time.sleep", lambda s: None),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_gc15_resuelve_por_lugares_manual(self):
        it = {"municipio": "Habana del Este",
              "direccion": "Chivás, Vía Blanca desde San Luis hasta Circunvalación."}
        out = self.E.geocodificar_averias([it], {}, solo_lugar=True)[0]
        self.assertEqual((out["lat"], out["lon"]), (23.13502, -82.3085),
                         "'Chivás' debe fijarse por lugares_manual, no por red")


if __name__ == "__main__":
    unittest.main()
