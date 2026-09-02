"""El aprendiz de circuitos (aprende_circuitos.py): umbrales, estabilidad,
veto humano, resolución de alias y el filtro del chequeo 9. Evidencia
sintética en memoria + un smoke contra el caché REAL de partes (solo lectura).

La clase raíz: el embudo 'por_confirmar' nunca se vaciaba — los códigos que el
LLM ve repetidos no estaban en el catálogo, así que partes_llm los marcaba
dudosos, estado.circuitos_llm los descartaba y el chequeo 9 los recomendaba
cada día (A1328: 65 partes). Con el archivo aprendido cableado en
es_conocido/canonico el ciclo se cierra solo.
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "scripts"
RAIZ = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent / "extractor"))
import circuitos_id as ci  # noqa: E402


def _cargar(nombre):
    spec = importlib.util.spec_from_file_location(nombre, SCRIPTS / f"{nombre}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


AC = _cargar("aprende_circuitos")
VD = _cargar("verificar_datos")


def partes_de(*posts):
    """posts = [(message_id, [codigos], calles, municipio), ...] -> caché."""
    out = {}
    for mid, cods, calles, muni in posts:
        out[mid] = {"via": "llm", "validador_version": 4,
                    "circuitos": [{"codigos": list(cods), "calles": calles,
                                   "municipio": muni, "estado": "sin servicio",
                                   "codigos_estado": list(cods)}],
                    "por_confirmar": list(cods)}
    return out


class UmbralTest(unittest.TestCase):
    def test_dos_posts_no_aprenden(self):
        p = partes_de(("1", ["Z9998"], "Callefalsa Uno desde Uno hasta Dos", None),
                      ("2", ["Z9998"], "Callefalsa Uno desde Uno hasta Dos", None))
        self.assertEqual(AC.aprender(p, catalogo={}, autoridad={}, falsos=set(), ya={}), {})

    def test_tres_posts_estables_aprenden(self):
        p = partes_de(*[(str(i), ["Z9998"], "Callefalsa Uno desde Uno hasta Dos", None)
                        for i in range(1, 4)])
        nuevos = AC.aprender(p, catalogo={}, autoridad={}, falsos=set(), ya={})
        self.assertEqual(list(nuevos), ["Z9998"])
        r = nuevos["Z9998"]
        self.assertIsNone(r["alias_de"])
        self.assertEqual(r["posts"], 3)
        self.assertEqual(r["calles"], "Callefalsa Uno desde Uno hasta Dos")
        self.assertEqual(r["ejemplo_mensaje"], "3")


class EstabilidadTest(unittest.TestCase):
    def test_calles_que_rotan_no_se_aprenden(self):
        """El código 'cambia de dirección' post a post: es ruido del LLM, no un
        circuito con zona fija."""
        p = partes_de(("1", ["Z9997"], "Alpha bravo charlie", None),
                      ("2", ["Z9997"], "Delta echo foxtrot", None),
                      ("3", ["Z9997"], "Golf hotel india", None))
        self.assertEqual(AC.aprender(p, catalogo={}, autoridad={}, falsos=set(), ya={}), {})

    def test_mitad_exacta_no_es_mayoria(self):
        p = partes_de(("1", ["Z9996"], "Callefalsa Uno desde Uno hasta Dos", None),
                      ("2", ["Z9996"], "Callefalsa Uno desde Uno hasta Dos", None),
                      ("3", ["Z9996"], "Otra distinta siempre", None),
                      ("4", ["Z9996"], "Rotando por completo", None))
        self.assertEqual(AC.aprender(p, catalogo={}, autoridad={}, falsos=set(), ya={}), {})

    def test_variantes_de_puntuacion_se_agrupan(self):
        base = "Callefalsa Uno desde Uno hasta Dos"
        p = partes_de(("1", ["Z9995"], base, None),
                      ("2", ["Z9995"], base + ".", None),
                      ("3", ["Z9995"], base.replace(" desde ", ", desde "), None))
        nuevos = AC.aprender(p, catalogo={}, autoridad={}, falsos=set(), ya={})
        self.assertIn("Z9995", nuevos)
        self.assertEqual(nuevos["Z9995"]["posts"], 3)


class VetoTest(unittest.TestCase):
    def test_circuitos_falsos_no_aprenden_nunca(self):
        """'L2' es la calle L del Vedado: el veto humano (correcciones.json)
        manda sobre cualquier recurrencia."""
        p = partes_de(*[(str(i), ["L2"], "Callefalsa Uno desde Uno hasta Dos", None)
                        for i in range(1, 5)])
        self.assertEqual(AC.aprender(p, catalogo={}, autoridad={"L2": ["Plaza"]},
                                     falsos={"L2"}, ya={}), {})
        # con el veto levantado sí aprendería (la recurrencia estaba)
        self.assertIn("L2", AC.aprender(p, catalogo={}, autoridad={},
                                        falsos=set(), ya={}))


class AliasTest(unittest.TestCase):
    CALLES_CANON = "Sanbenignota hasta Macedonota, Viablanca hasta Resguardo"

    def _cat(self, canon):
        return {canon: ci._tokens(self.CALLES_CANON)}

    def test_alias_estricto_calles_identicias(self):
        """'P325' -> OP325: prefijo omitido por la UNE y las MISMAS calles."""
        p = partes_de(*[(str(i), ["Z325"], self.CALLES_CANON, "Cerro")
                        for i in range(1, 4)])
        nuevos = AC.aprender(p, catalogo=self._cat("OZ325"), autoridad={},
                             falsos=set(), ya={})
        self.assertEqual(nuevos["Z325"]["alias_de"], "OZ325")
        self.assertEqual(nuevos["Z325"]["municipio"], "Cerro")
        # idempotencia local: ya registrado, la segunda pasada no repite
        self.assertEqual(AC.aprender(p, catalogo=self._cat("OZ325"), autoridad={},
                                     falsos=set(), ya=nuevos), {})

    def test_alias_debil_numero_suelto_cifras_prefijo_y_municipio(self):
        """'581' -> SF581: las tablas describen SF581 por repartos y el parte lo
        escribe por calles numeradas (solape 0.25); para un número suelto de 3
        cifras —que no puede promoverse— basta candidato único + municipio de
        autoridad + al menos un topónimo común."""
        cat = {"SF777": ci._tokens("Parte de Santa Fe, Juan Manuel Farsante")}
        aut = {"SF777": ["Playa"]}
        p = partes_de(*[(str(i), ["777"], "1ra B entre 290 y 292 Santa Fe", "Playa")
                        for i in range(1, 4)])
        nuevos = AC.aprender(p, catalogo=cat, autoridad=aut, falsos=set(), ya={})
        self.assertEqual(nuevos["777"]["alias_de"], "SF777")

    def test_numero_suelto_sin_alias_queda_en_el_embudo(self):
        """El 3-dígitos se niega a crear registro nuevo: sin alias verificable,
        se queda en por_confirmar (que el aprendiz no puede inventar)."""
        p = partes_de(*[(str(i), ["778"], "1ra B entre 290 y 292 Santa Fe", "Playa")
                        for i in range(1, 4)])
        self.assertEqual(AC.aprender(p, catalogo={}, autoridad={},
                                     falsos=set(), ya={}), {})

    def test_letras_sin_solape_no_alias_debil(self):
        """El alias débil es SOLO para números sueltos: 'AB777' con solape bajo
        y municipio coincidente se promueve por sus calles, no se aliasa."""
        cat = {"SF777": ci._tokens("Parte de Santa Fe, Juan Manuel Farsante")}
        aut = {"SF777": ["Playa"]}
        p = partes_de(*[(str(i), ["AB777"], "1ra B entre 290 y 292 Santa Fe", "Playa")
                        for i in range(1, 4)])
        nuevos = AC.aprender(p, catalogo=cat, autoridad=aut, falsos=set(), ya={})
        self.assertEqual(nuevos["AB777"]["alias_de"], None)
        self.assertEqual(nuevos["AB777"]["calles"], "1ra B entre 290 y 292 Santa Fe")

    def test_digitos_distintos_no_aliassan(self):
        """PZ20 casaba 'I entre 23 y 25' al 1.0 con P329 por calles — pero son
        circuitos distintos: el alias exige las MISMAS cifras."""
        cat = {"P329": ci._tokens("I entre 23 y 25, Vedado")}
        p = partes_de(*[(str(i), ["PZ202"], "I entre 23 y 25, Vedado", "Plaza")
                        for i in range(1, 4)])
        nuevos = AC.aprender(p, catalogo=cat, autoridad={}, falsos=set(), ya={})
        self.assertIsNone(nuevos["PZ202"]["alias_de"])
        self.assertEqual(nuevos["PZ202"]["municipio"], "Plaza")


class ConocidoWiringTest(unittest.TestCase):
    """es_conocido/canonico leen el archivo aprendido (precedente:
    bloques_aprendidos.json editable a mano)."""

    def setUp(self):
        self._cat = ci._CATALOGO
        self._ap = ci._APRENDIDOS
        self._ruta_original = ci.APRENDIDOS_FILE
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ruta = os.path.join(self.tmp.name, "aprendidos.json")
        json.dump({"QQ7": {"calles": "Callefalsa Uno desde Uno hasta Dos",
                           "municipio": None, "alias_de": None, "posts": 4,
                           "ejemplo_mensaje": "9"},
                   "581": {"calles": "1ra B entre 290 y 292 Santa Fe",
                           "municipio": "Playa", "alias_de": "SF581", "posts": 5,
                           "ejemplo_mensaje": "9"}},
                   open(self.ruta, "w"), ensure_ascii=False)
        ci.APRENDIDOS_FILE = self.ruta
        ci.recargar()  # fuerza la re-lectura del archivo bajo el tmp
        self.addCleanup(setattr, ci, "APRENDIDOS_FILE", self._ruta_original)
        self.addCleanup(ci.recargar)

    def tearDown(self):
        ci._CATALOGO = self._cat
        ci._APRENDIDOS = self._ap

    def test_es_conocido_aceptados_aprendidos_y_alias(self):
        self.assertTrue(ci.es_conocido("QQ7"))     # promovido no-alias
        self.assertTrue(ci.es_conocido("581"))     # alias -> SF581 conocido
        self.assertTrue(ci.es_conocido("SF581"))   # canónico (oficial)
        self.assertFalse(ci.es_conocido("NOPE99"))

    def test_canonico_enruta_el_alias(self):
        self.assertEqual(ci.canonico("581"), "SF581")
        self.assertEqual(ci.canonico("QQ7"), "QQ7")
        self.assertEqual(ci.canonico("SF581"), "SF581")

    def test_el_archivo_no_crea_gemelos_en_el_matching(self):
        # el no-alias aprendido vota por su código; el alias NO aporta clave
        # propia (su evidencia se enruta al canónico):
        ct = ci._catalogo_tokens()
        self.assertIn("QQ7", ct)
        self.assertNotIn("581", ct)


class Check9Test(unittest.TestCase):
    def test_recurrente_conocido_deja_de_recomendarse(self):
        """SR850 se recomendaría a diario por su lista congelada — pero ESTÁ en
        el catálogo servido. El filtro es contra es_conocido de hoy."""
        cache = {str(i): {"por_confirmar": ["SR850"]} for i in range(1, 5)}
        cache.update({"x1": {"por_confirmar": ["NOPE99"]},
                      "x2": {"por_confirmar": ["NOPE99"]},
                      "x3": {"por_confirmar": ["NOPE99"]}})
        textos = " ".join(det for _, det in VD.candidatos_por_confirmar(cache))
        self.assertIn("NOPE99: visto 3 veces por el LLM y no está en el catálogo "
                      "— candidato a añadir", textos)
        self.assertNotIn("SR850", textos)


class SmokeRealTest(unittest.TestCase):
    """El caché REAL de partes del repo tiene que producir exactamente los 6
    promovidos + 2 alias verificados a mano el 2-sep-2026. Hermetic: solo
    lectura de JSON del repo, sin red."""

    def setUp(self):
        self._cat = ci._CATALOGO
        self._ap = ci._APRENDIDOS
        ci._APRENDIDOS = {}   # mundo pre-aprendizaje
        ci._CATALOGO = None
        self.addCleanup(setattr, ci, "_CATALOGO", self._cat)
        self.addCleanup(setattr, ci, "_APRENDIDOS", self._ap)

    def test_los_6_mas_2_del_caso_vivo(self):
        partes = json.load(open(os.path.join(RAIZ, "data", "partes_llm.json")))
        nuevos = AC.aprender(partes)
        promos = {c: r for c, r in nuevos.items() if not r["alias_de"]}
        alias = {c: r["alias_de"] for c, r in nuevos.items() if r["alias_de"]}
        self.assertEqual(set(promos), {"A1328", "H446", "PZ20", "3228", "A1216", "H466"})
        self.assertEqual(alias, {"581": "SF581", "P325": "OP325"})
        self.assertEqual({c: r["posts"] for c, r in nuevos.items()},
                         {"A1328": 65, "H446": 22, "PZ20": 9, "3228": 9,
                          "A1216": 6, "H466": 3, "581": 5, "P325": 4})
        # H466 NO es el R466 de Avenida del Puerto (Regla): cifras iguales,
        # prefijo incompatible y calles sin un solo token común.
        self.assertIsNone(nuevos["H466"]["alias_de"])
        self.assertIn("Guasabacoa", nuevos["H466"]["calles"])
        # SR850 ya servido -> ni promo ni alias:
        self.assertNotIn("SR850", nuevos)
        # idempotencia: sobre el archivo ya escrito, nada nuevo
        self.assertEqual(AC.aprender(partes, ya={**nuevos}), {})


if __name__ == "__main__":
    unittest.main()
