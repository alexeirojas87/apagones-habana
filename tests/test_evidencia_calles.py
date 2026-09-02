"""Chequeo 10 ('punto lejos de sus propias calles', sin red) y la guardia del
atajo LUGARES_MANUAL: evidencia sintética en memoria, cero red.

Cubren la clase raíz auditada en manual: hits 'circ|' envenenados por POIs
homónimos que el control cruzado de estado.py nunca re-valida (CCP20) y las
trampas que el gate automático no debe morder: familias de un solo POI
('El Trébol' / 'Embalse La Coca'), twins que se acusan entre sí, y geometría
envenenada alrededor de un punto respaldado por autoridad (L317/PZ13).

Y los dos huecos que dejaron los casos vivos del 2-sep: la pista entre
paréntesis ganadora pasaba sin control cruzado (SG316, validación en
estado.py), y las N variantes del mismo parte geocodificadas por el MISMO
POI se contaban como N evidencias concordantes (la familia 'Centro Hispano'
de SG316, dedup por match en evidencia_calles).
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


# ---------------------------------------------------------------------------
# Gap B (SG316): auto-certificación de familias. Mismas variantes del mismo
# parte geocodificadas por el MISMO POI homónimo comparten el match y valen
# UNA evidencia, no N. Literales recortados de data/geocache_averias.json.
# ---------------------------------------------------------------------------

CH = (23.1449818, -82.3590703)          # POI 'Centro Hispano' (Lídice/Cerro)
SANTIAGO = (23.1334108, -82.3704317)    # hit 'Santiago' (independiente)
ANTON = (23.1184833, -82.3076517)       # hit 'Antón Rocío' (independiente)
RINCON = (22.9522601, -82.4148372)      # POI 'Rincón' (campo Boyeros, deep-south)
CATALINA = (23.09430, -82.36938)        # 'Santa Catalina y ...' (zona real SG313)

_F316 = [
    "Villanueva, Caridad, Sierra Maestra, Revolución, Naroca, Rocío, Alrededores de calles 3ra (La Unión), calle 202 (calzada del Rincón) hasta avenida 411, calle 194 desde avenida 411 hasta avenida 405 y avenida 407 desde calle 198 hasta calle 180",
    "Repartos Villanueva, Caridad, Sierra Maestra, Revolución, Naroca, Rocío. Alrededores de calles 3ra (La Unión), calle 202 calzada del Rincón) hasta avenida 411, calle 194 desde avenida 411 hasta avenida 405 y avenida 407 desde calle 198 hasta calle 180",
    "Repartos Villanueva, Caridad, Sierra Maestra, Revolución, Naroca, Rocío Alrededores de calles 3ra (La Unión), calle 202 (calzada del Rincón) hasta avenida 411, calle 194 desde avenida 411 hasta avenida 405 y avenida 407 desde calle 198 hasta calle 180",
    "Repartos Villanueva, Caridad, Sierra Maestra, Revolución, Naroca, Rocío Alrededores de calles 3ra (La Unión), calle 202 (calzada del Rincón) hasta Avenida 411, calle 194 desde Avenida 411 hasta Avenida 405 y Avenida 407 desde calle 198 hasta calle 180",
    "Repartos Villanueva, Caridad, Sierra Maestra, Revolución, Naroca, Rocío. Alrededores de calles 3ra (La Unión), calle 202 (calzada del Rincón) hasta avenida 411, calle 194 desde avenida 411 hasta avenida 405 y avenida 407 desde calle 198 hasta calle 180",
    "Repartos Villanueva, Caridad, Sierra Maestra, Revolución, Naroca, Rocío. Alrededores de calles 3ra (La Unión), calle 202 (calzada del Rincón) hasta Avenida 411, calle 194 desde Avenida 411 hasta Avenida 405 y Avenida 407 desde calle 198 hasta calle 180",
]
# seis literales reales de la familia SG316: todas las claves, mismas coords,
# MISMO match 'Centro Hispano' (la caché purgada hoy tenía doce).
FAMILIA_316 = {f"circ|{_t}": {"lat": CH[0], "lon": CH[1],
                              "match": "Centro Hispano-Americano de la Cultura"}
               for _t in _F316}

HERMANOS_314 = {  # SG314 y los hermanos que lo corroboran CON matches distintos
    "circ|Santiago, La Especial, El Tessie, La Unión, Lídice, Cenpalab, Cacahual, La Chata, Santa Susana":
        {"lat": SANTIAGO[0], "lon": SANTIAGO[1], "match": "Santiago"},
    "circ|Santiago, Villanueva, La Caridad, Sierra Maestra, La Unión, Comunidad Revolución, Naroca, Rocío":
        {"lat": SANTIAGO[0], "lon": SANTIAGO[1], "match": "Santiago"},
    "circ|Santiago, Villanueva, Caridad, Sierra Maestra, La Unión, Comunidad Revolucion, Lídice, Naroca, Rocío":
        {"lat": ANTON[0], "lon": ANTON[1], "match": "Antón Rocío"},
    "circ|Repartos La Especial, Tessie, La Tabernita, Lídice, Cenpalab, Cacahual, La Chata y Santa Susana. Alrededores de calles 3ra (La Unión)":
        {"lat": CH[0], "lon": CH[1], "match": "Centro Hispano-Americano de la Cultura"},
}

CALLES_316 = list(FAMILIA_316)[0][5:]
CALLES_314 = "Santiago, La Especial, El Tessie, La Unión, Lídice, Cenpalab, Cacahual, La Chata, Santa Susana.🚨"

FAMILIA_313 = {
    "circ|La Catalina, La Vigirita, La Castellana, Palmarito, Fructuoso Rodríguez, La China, La Majagua, Angelita, Lomo Tendido, Rincón.🚨":
        {"lat": RINCON[0], "lon": RINCON[1], "match": "Rincón"},
    "circ|La Catalina, La Vigirita, La Castellana, PalmaritoFructuoso Rodríguez, La China, La Majagua, Angelita, Lomo Tendido, Rincón":
        {"lat": RINCON[0], "lon": RINCON[1], "match": "Rincón"},
    "circ|La Catalina, La Vigirita, La Castellana, Palmarito, Fructuoso Rodríguez, La China, La Majagua, Angelita, Lomo Tendido":
        {"lat": CATALINA[0], "lon": CATALINA[1],
         "match": "Santa Catalina y Jose de la Luz y Caballero"},
    "circ|Alrededores de calles avenida 409 y calle 212 (La Catalina). Repartos Palmarito, Fructuoso Rodríguez, La China y La Majagua.🚨":
        {"lat": 23.09449, "lon": -82.36938,
         "match": "Santa Catalina y José de la Luz y Caballero"},
}
CALLES_313 = list(FAMILIA_313)[0][5:]


class DedupFamiliaTest(unittest.TestCase):
    def _fam(self, punto, n):
        return {f"circ|Clon{i}, Callehermana Uno, Callehermana Dos":
                {"lat": punto[0], "lon": punto[1], "match": "POI Clon Homónimo"}
                for i in range(n)}

    def test_ocho_copias_del_mismo_poi_valen_una_evidencia(self):
        entradas = evc.entradas_hermanas(self._fam(PUNTO_LEJOS, 8))
        ev = evc.evidencia_de_calles(None, CALLES, PUNTO_BUENO, entradas, {})
        self.assertEqual(ev["n"], 1)
        self.assertEqual(ev["n_hermanos"], 1)
        # un solo POI clonado no puede negar el punto pintado NI con la guardia
        self.assertFalse(evc.contradice_evidencia(PUNTO_BUENO, CALLES, entradas, {},
                                                  minimo=evc.MIN_CONCORDANTES))

    def test_familia_mas_corroboracion_independiente_pasa_el_gate(self):
        cache = self._fam(PUNTO_LEJOS, 8)
        cache.update({f"circ|Testigo{i}, Callehermana Uno, Callehermana Dos":
                      {"lat": PUNTO_LEJOS[0], "lon": PUNTO_LEJOS[1],
                       "match": f"POI Testigo {i}"} for i in range(4)})
        entradas = evc.entradas_hermanas(cache)
        ev = evc.evidencia_de_calles(None, CALLES, PUNTO_BUENO, entradas, {})
        self.assertEqual(ev["n"], 5)  # 1 familia + 4 matches distintos
        self.assertTrue(evc.contradice_evidencia(PUNTO_BUENO, CALLES, entradas, {},
                                                 minimo=evc.MIN_CONCORDANTES))

    def test_matches_genericos_no_se_deduplican(self):
        # mediana/barrio local/centroide/None las geocodificó CADA clave por su
        # cuenta: son triangulación independiente, no huella de familia.
        cache = {f"circ|Propio{i}, Callehermana Uno, Callehermana Dos":
                 {"lat": PUNTO_LEJOS[0], "lon": PUNTO_LEJOS[1], "match": m}
                 for i, m in enumerate((None, "mediana de calles",
                                        "mediana de calles (descarta POI lejano)",
                                        "barrio local", "centro municipio"))}
        del cache["circ|Propio0, Callehermana Uno, Callehermana Dos"]["match"]
        entradas = evc.entradas_hermanas(cache)
        ev = evc.evidencia_de_calles(None, CALLES, PUNTO_BUENO, entradas, {})
        self.assertEqual(ev["n"], 5)


class TrioRealSG316Test(unittest.TestCase):
    """Regresión con los literales reales del trío reportado el 2-sep."""

    def test_familia_sg316_no_se_defiende_sola(self):
        cache = dict(FAMILIA_316)
        cache.update(HERMANOS_314)
        entradas = evc.entradas_hermanas(cache)
        ev = evc.evidencia_de_calles("SG316", CALLES_316, CH, entradas, {})
        # 6 claves de la familia comparten 'Centro Hispano' -> UN voto; los
        # hermanos 'Santiago' y 'Antón Rocío' (matches independientes) suman.
        self.assertLessEqual(ev["n_hermanos"], 3)
        self.assertLess(ev["n"], evc.MIN_CONCORDANTES,
                        "sin dedup la familia sumaba 6+ y se auto-certificaba")

    def test_familia_sg316_no_condena_el_punto_servido_de_sg316(self):
        # Antes del dedup, las 6 copias del POI ('Centro Hispano', a 6 km del
        # punto servido en 'Antón Rocío') superaban el gate y condenaban el
        # punto servido. Con dedup el racimo contrario queda en 2 < 5: silencio.
        cache = dict(FAMILIA_316)
        cache.update(HERMANOS_314)
        servido = {"codigo": "SG316", "lat": ANTON[0], "lon": ANTON[1],
                   "calles": "Santiago, VillaNueva, Caridad, Sierra Maestra, "
                             "La Unión, Comunidad Revolucion, Lídice, Naroca, Roció.🚨"}
        probs, pg, pl, pi = VD.chequeo_evidencia_calles([servido], SIN_AUT, cache, {})
        self.assertEqual((probs, pg, pl, pi), ([], set(), set(), set()))

    def test_sg314_conserva_corroboracion_independiente(self):
        cache = dict(FAMILIA_316)
        cache.update(HERMANOS_314)
        entradas = evc.entradas_hermanas(cache)
        ev = evc.evidencia_de_calles("SG314", CALLES_314, CH, entradas, {})
        self.assertGreaterEqual(ev["n"], 2,
                                "'Santiago' y 'Centro Hispano' son matches "
                                "distintos: la corrobación real sobrevive al dedup")
        # y el auditor no lo condena: el racimo que coincide con su punto ES el
        # mayoritario (n_coincide == n) — no hay racimo contrario que gane.
        self.assertEqual(ev["n_coincide"], ev["n"])
        servido = {"codigo": "SG314", "lat": CH[0], "lon": CH[1], "calles": CALLES_314}
        probs, pg, *_ = VD.chequeo_evidencia_calles([servido], SIN_AUT, cache, {})
        self.assertEqual(probs, [])
        self.assertEqual(pg, set())

    def test_sg313_la_familia_rincon_tambien_colapsa(self):
        entradas = evc.entradas_hermanas(FAMILIA_313)
        ev = evc.evidencia_de_calles("SG313", CALLES_313, RINCON, entradas, {})
        # las dos claves 'Rincón' valen UN voto en el campo de Boyeros; las
        # hermanas buenas (dos variantes de match por la tilde) ganan el
        # racimo: la evidencia apunta a la zona real de Cerro, no al POI.
        self.assertAlmostEqual(ev["centro"][0], 23.0944, delta=0.005)
        self.assertAlmostEqual(ev["centro"][1], -82.3694, delta=0.005)
        self.assertEqual(ev["n_coincide"], 1)
        self.assertLess(ev["n"], evc.MIN_CONCORDANTES,
                        "con n<5 el auditor calla: la purga de las claves 'Rincón' "
                        "es la reparación, el gate no debe disparar sobre 2 votos")


class ValidacionPistaGanadoraTest(unittest.TestCase):
    """Gap A (SG316): el control cruzado 'descarta POI lejano' solo corría
    cuando ganaba la última consulta (primer nombre). Cuando gana la PISTA
    entre paréntesis el hit pasaba sin validar — y un POI homónimo ('La
    Unión' -> 'Centro Hispano-Americano de la Cultura', 7 km al norte de las
    calles 194-202 × av 405-411) pintaba mal el circuito. La validación ahora
    corre sin importar qué consulta ganó; sin evidencia que la contradiga, la
    intención del autor queda en pie."""

    RESP_OK = {
        "Pistola, La Habana, Cuba": {"lat": PUNTO_LEJOS[0], "lon": PUNTO_LEJOS[1],
                                     "match": "Centro Farsante"},
        "Calle Callehermana Uno, La Habana, La Habana, Cuba":
            {"lat": PUNTO_BUENO[0], "lon": PUNTO_BUENO[1]},
        "Calle Callehermana Dos, La Habana, La Habana, Cuba":
            {"lat": PUNTO_BUENO[0], "lon": PUNTO_BUENO[1]},
    }

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache_path = os.path.join(self.tmp.name, "geocache.json")
        self.lin_path = os.path.join(self.tmp.name, "inexistente.json")
        json.dump({}, open(self.cache_path, "w"))
        import estado
        self.E = estado
        self.resp = dict(self.RESP_OK)
        for p in (
            mock.patch.object(estado, "CACHE_AVERIAS", self.cache_path),
            mock.patch.object(evc, "CACHE_GEO", self.cache_path),
            mock.patch.object(evc, "CACHE_LINEAS", self.lin_path),
            mock.patch.object(estado, "nominatim",
                              lambda q, caja: self.resp.get(q)),
            mock.patch("time.sleep", lambda s: None),
        ):
            p.start()
            self.addCleanup(p.stop)

    DIR = "Aldea Falsa, Callehermana Uno, Callehermana Dos (Pistola)"

    def _geo(self):
        it = {"municipio": "X", "direccion": self.DIR}
        out = self.E.geocodificar_averias([it], {}, solo_lugar=True)[0]
        return out, json.load(open(self.cache_path))[evc.clave_cache(self.DIR)]

    def test_pista_ganadora_validada_por_las_calles(self):
        out, guardado = self._geo()
        self.assertEqual((out["lat"], out["lon"]), PUNTO_BUENO,
                         "la evidencia de las calles gana a la pista homónima")
        self.assertEqual(guardado["match"], "mediana de calles (descarta pista lejana)")

    def test_pista_ganadora_sin_evidencia_que_la_contradiga_pasa(self):
        del self.resp["Calle Callehermana Uno, La Habana, La Habana, Cuba"]
        del self.resp["Calle Callehermana Dos, La Habana, La Habana, Cuba"]
        out, guardado = self._geo()
        self.assertEqual((out["lat"], out["lon"]), PUNTO_LEJOS,
                         "si las calles no resuelven, la intención del autor manda")
        self.assertEqual(guardado["match"], "Centro Farsante")

    def test_pista_ganadora_con_calles_cercanas_pasa(self):
        # <5 km no es contradicción: el circuito puede traer su barrio junto a
        # sus calles y el hit conserva la precisión del POI del autor.
        cerca = {"lat": PUNTO_LEJOS[0] + 0.005, "lon": PUNTO_LEJOS[1] + 0.004}
        for k in ("Calle Callehermana Uno, La Habana, La Habana, Cuba",
                  "Calle Callehermana Dos, La Habana, La Habana, Cuba"):
            self.resp[k] = dict(cerca)
        out, guardado = self._geo()
        self.assertEqual((out["lat"], out["lon"]), PUNTO_LEJOS)
        self.assertEqual(guardado["match"], "Centro Farsante")


if __name__ == "__main__":
    unittest.main()
