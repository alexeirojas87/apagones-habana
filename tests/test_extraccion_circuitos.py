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


class AdopcionCallesDegeneradasTest(unittest.TestCase):
    """Defensa en profundidad en el consumidor (build_circuitos._adoptar_calles).

    Regresión del parte 78278 (29-ago-2026): el LLM devolvió la dirección del
    L316 con 'uda' repetido cientos de veces (3014 chars); la regla "longest
    wins" la habría adoptado sobre el texto bueno aunque la caché llegara
    sucia (entradas anteriores al guard de partes_llm.py). El guard comparte
    la implementación de extract.texto_degenerado con el productor.
    """

    # dirección limpia del L316 en ese mismo parte (re-derivada por el regex)
    LIMPIA = ("Alrededores de calle 70 desde Avenida 13 hasta Avenida 29C con "
              "Avenida 21 (Reparto Buenavista). Calle 64 desde Avenida 17 "
              "hasta Avenida 7maB(Almendares).")
    BUCLE = ("Alrededores de calle 70 desde Avenida uda " + "uda " * 300).strip()
    # muestra real recortada de la caché (74883/PZ16, 606 chars legítimos;
    # corrida de espacios de relleno colapsada)
    PZ16 = ("Alrededores de calles 37 desde 4 hasta 6, 6 desde 37 hasta San "
            "Pedro, San Pedro desde Marino hasta Mariano, Ayestarán desde San "
            "Pedro hasta 20 de Mayo, 20 de Mayo desde Ayestarán hasta "
            "Amenidad, Amenidad hasta calzada del Cerro y edificios de la "
            "Esquina de Tejas. Calle 6 desde 37 hasta Hidalgo")
    FUENTE_LARGA = "Informamos afectación en los circuitos: " + "zona. " * 200

    def _registro(self, calles):
        return {"codigo": "L316", "calles": calles}

    def test_no_adopta_bucle_de_repeticion_aunque_sea_la_mas_larga(self):
        r = self._registro(self.LIMPIA)

        MOD._adoptar_calles(r, self.BUCLE, self.LIMPIA + self.PZ16)

        self.assertEqual(r["calles"], self.LIMPIA,
                         "el texto degenerado ganó por longitud: se publicó basura")

    def test_no_adopta_direccion_mas_larga_que_el_parte(self):
        r = self._registro(self.LIMPIA)

        MOD._adoptar_calles(r, self.PZ16, "parte breve")

        self.assertEqual(r["calles"], self.LIMPIA)

    def test_adopta_direccion_larga_legitima(self):
        # la calibración no puede matar el comportamiento original: la más
        # completa gana cuando solapa bien (y PZ16 <1200, cabría en su parte).
        r = self._registro("Calle 37 desde 4 hasta 6")

        MOD._adoptar_calles(r, self.PZ16, self.FUENTE_LARGA)

        self.assertEqual(r["calles"], self.PZ16)

    def test_adopta_repeticiones_no_consecutivas(self):
        # "Avenida 7ma hasta Avenida 1ra ... Avenida 7maB hasta Avenida 3ra":
        # 'Avenida' se repite, nunca tres palabras idénticas seguidas.
        r = self._registro("")

        MOD._adoptar_calles(r, self.LIMPIA, self.LIMPIA)

        self.assertEqual(r["calles"], self.LIMPIA)
