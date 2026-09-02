import importlib.util
import json
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "scripts"
RAIZ = Path(__file__).parents[1]
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

    def test_calles_sin_el_aviso_institucional(self):
        # Aunque el LLM copie el aviso del parte (pese a la regla del prompt),
        # la validación no debe dejarlo pasar como dirección del circuito.
        extraccion = {
            "tipo": "afectacion",
            "circuitos": [{
                "codigo": "GC11",
                "estado": "sin servicio",
                "calles": (
                    "Reparto Garrido, Alrededores de calles Soledad, La Palma, "
                    "Santo Domingo, Pepe Antonio, División, Padilla y Quintín. "
                    "Usted puede, aún siendo cliente de este circuito, continuar "
                    "afectado por avería en acometida o transformador. En esos "
                    "casos le pedimos contactarnos por las vías alternativas"
                ),
            }],
        }

        item = MOD.validar(extraccion)["circuitos"][0]

        self.assertEqual(
            item["calles"],
            "Reparto Garrido, Alrededores de calles Soledad, La Palma, "
            "Santo Domingo, Pepe Antonio, División, Padilla y Quintín.",
        )


# El parte real 78278 (29-ago-2026): el LLM cayó en un bucle de repetición y
# devolvió la dirección del L316 con la palabra 'uda' repetida cientos de veces
# (3014 chars). El validador la aceptó y el catálogo la adoptó por "longest
# wins", publicando la basura en circuitos.html. Estos regresos pinan que el
# ítem degenerado se descarta ANTES de cachearse, sin dañar nada legítimo.
PARTE_78278 = (
    "🚨📣Informamos a la población que a partir de las 03:31 pm se afectó el "
    "servicio eléctrico en los siguientes circuitos:\n\n"
    "👉 L316: Alrededores de calle 70 desde Avenida 13 hasta Avenida 29C con "
    "Avenida 21 (Reparto Buenavista). Calle 64 desde Avenida 17 hasta Avenida "
    "7maB(Almendares). Calle 66 desde Avenida 7maB hasta Avenida 3ra. Calle 62 "
    "desde Avenida 7ma hasta Avenida 1ra(Miramar). \n"
    "👉 1247: Calle 28 desde Avenida 41 hasta Avenida 47; Avenida 41 desde "
    "Avenida 9na hasta calle 24 (Kohly). Calle 10 desde Avenida 5ta hasta "
    "Avenida 9na (Kohly). Cuadrante de Avenida 7ma desde calle 12 hasta calle "
    "24, desde Avenida 5ta hasta Avenida 31 (Miramar).🚨📣"
)
# Como el del parte 78278 pero corto (<1200 y <la fuente): si se descarta, fue
# por la repetición en bucle, no por la regla de longitud.
CALLES_BUCLE = ("Alrededores de calle 70 desde Avenida uda " + "uda " * 80).strip()
CALLES_1247 = ("Calle 28 desde Avenida 41 hasta Avenida 47; Avenida 41 desde "
               "Avenida 9na hasta calle 24 (Kohly).")
# Direcciones legítimas reales recortadas de la caché (74883/PZ16 y 75053/A980;
# se colapsó la corrida de espacios que el parte trae como relleno).
CALLES_PZ16 = ("Alrededores de calles 37 desde 4 hasta 6, 6 desde 37 hasta San "
               "Pedro, San Pedro desde Marino hasta Mariano, Ayestarán desde San "
               "Pedro hasta 20 de Mayo, 20 de Mayo desde Ayestarán hasta "
               "Amenidad, Amenidad hasta calzada del Cerro y edificios de la "
               "Esquina de Tejas. Calle 6 desde 37 hasta Hidalgo Cuadrante desde "
               "Ayuntamiento, Ayestarán hasta Amenidad y desde Factor, Zaldo "
               "hasta Pedro Pérez Cuadrante desde Patria hasta Cruz del Padre y "
               "desde Carballo hasta Estévez y Edificios de la Esquina de Tejas.")
CALLES_A980 = ("Alrededores de los cuadrantes: -Desde Concha hasta Arango y "
               "desde Ensenada hasta Línea del Ferrocarril (Luyanó) -Desde "
               "Rodríguez Este hasta Calzada de Luyanó y Rosa Enrique hasta "
               "Línea del Ferrocarril (Luyanó) -Desde Calzada de Luyanó hasta "
               "Pasaje Córdoba y desde Manuel Pruna hasta Agramonte(Asunción).")


class BasuraDelLlmTest(unittest.TestCase):
    """El chokepoint: validar() no deja cachar ítems con dirección degenerada."""

    def _item(self, codigo, calles, estado="sin servicio"):
        return {"codigo": codigo, "calles": calles, "estado": estado}

    def test_descarta_el_item_degenerado_conserva_los_demas(self):
        extraccion = {
            "tipo": "afectacion",
            "circuitos": [self._item("L316", CALLES_BUCLE),
                          self._item("1247", CALLES_1247)],
        }

        resultado = MOD.validar(extraccion, PARTE_78278)

        cods = [c["codigos"] for c in resultado["circuitos"]]
        self.assertEqual(cods, [["1247"]])
        # la basura tampoco alimenta el embudo de aprendizajes:
        self.assertEqual(resultado["por_confirmar"], [])

    def test_todo_degenerado_deja_los_circuitos_vacios(self):
        # El mensaje se cachea igual (con circuitos []) y build_circuitos
        # re-deriva la dirección limpia del crudo con el regex, como ya hace
        # con cualquier parte que el LLM no entiende.
        extraccion = {"tipo": "afectacion",
                      "circuitos": [self._item("L316", CALLES_BUCLE)]}

        resultado = MOD.validar(extraccion, PARTE_78278)

        self.assertEqual(resultado["circuitos"], [])

    def test_repeticion_no_consecutiva_es_legitima(self):
        # "Avenida 7ma hasta Avenida 1ra" repite 'Avenida' pero NO consecutiva:
        # el regex exige 3 palabras idénticas seguidas. Este texto es el L316
        # real del parte 78278: debe sobrevivir a la guardería.
        reales = MOD.validar(
            {"tipo": "afectacion", "circuitos": [self._item(
                "L316", "Alrededores de calle 70 desde Avenida 13 hasta Avenida "
                "29C con Avenida 21 (Reparto Buenavista). Calle 64 desde "
                "Avenida 17 hasta Avenida 7maB(Almendares). Calle 66 desde "
                "Avenida 7maB hasta Avenida 3ra. Calle 62 desde Avenida 7ma "
                "hasta Avenida 1ra(Miramar).")]},
            PARTE_78278)

        self.assertEqual(len(reales["circuitos"]), 1)

    def test_direccion_mas_larga_que_el_parte_se_descarta(self):
        # Sin repetición (palabras que nunca se repiten consecutivas) y por
        # debajo del tope absoluto: solo la regla de longitud contra la fuente
        # puede rechazarla. Una dirección no puede medir más que el post.
        larga = " ".join(f"C{palabra}" for palabra in
                          ["Alba", "Berro", "Caoba", "Daimara", "Embalsa",
                           "Fosforo", "Glayola", "Hisopa", "Indigo", "Jaspe"] * 12)
        self.assertLess(len(larga), 1200)
        self.assertGreater(len(larga), len("Parte corto."))
        self.assertFalse(MOD.texto_degenerado(larga, "x" * len(larga) * 3),
                         "con fuente suficientemente larga no es basura")

        resultado = MOD.validar(
            {"tipo": "afectacion", "circuitos": [{
                "codigo": "X1", "calles": larga, "estado": "sin servicio"}]},
            "Parte corto.")

        self.assertEqual(resultado["circuitos"], [])

    def test_conserva_direcciones_largas_legitimas(self):
        # PZ16 (606) y A980 (1056 recortada a ~330) son las direcciones más
        # largas sanas de la caché: la guardería calibrada contra ellas no
        # puede rechazarlas.
        for calles in (CALLES_PZ16, CALLES_A980):
            fuente = "Actualización DAF semanal. " + "detalle de zonas. " * 100
            with self.subTest(n=len(calles)):
                resultado = MOD.validar(
                    {"tipo": "afectacion",
                     "circuitos": [self._item(None, calles)]}, fuente)
                self.assertEqual(len(resultado["circuitos"]), 1)
                self.assertEqual(resultado["circuitos"][0]["calles"], calles)


class CorpusCacheadoLimpioTest(unittest.TestCase):
    """Regresión de datos: el parte 78278 ya fue limpiado a mano y ningún ítem
    de la caché puede volver a publicar basura en el catálogo."""

    def test_ningun_item_de_partes_llm_es_degenerado(self):
        partes = json.load(open(RAIZ / "data" / "partes_llm.json", encoding="utf-8"))
        filas = json.load(open(RAIZ / "data" / "canal_cache.json",
                               encoding="utf-8"))["filas"]
        for mid, e in partes.items():
            fuente = filas.get(mid, {}).get("texto")
            for c in e.get("circuitos") or []:
                self.assertFalse(
                    MOD.texto_degenerado(c.get("calles"), fuente),
                    f"ítem degenerado en caché: {mid} {c.get('codigos')}",
                )


if __name__ == "__main__":
    unittest.main()
