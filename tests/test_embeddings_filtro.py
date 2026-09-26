"""Contrato del filtro de reportes del índice semántico.

Cubre scripts/chatbot/embeddings.py: solo los reportes de estado de corriente
entran al RAG, y la query los filtra y ordena en el servidor. Hermético: no toca
red ni Supabase. El cliente falso registra la cadena de llamadas; supabase y
postgrest están instalados, así que el módulo se importa directo.
"""

import importlib.util
import os
import types
import unittest

RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def cargar_modulo(nombre, ruta):
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cargar_embeddings():
    return cargar_modulo(
        "embeddings_bajo_prueba",
        os.path.join(RAIZ, "scripts", "chatbot", "embeddings.py"))


def cargar_purga(env_extra=None):
    env = {"SUPABASE_URL": "https://ejemplo.supabase.co",
           "SUPABASE_SERVICE_KEY": "k"}
    env.update(env_extra or {})
    viejo = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        return cargar_modulo(
            "purga_bajo_prueba", os.path.join(RAIZ, "scripts", "purga.py"))
    finally:
        for k, v in viejo.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class FakeQuery:
    """Cliente mínimo: graba cada llamada de la cadena y se devuelve a sí mismo.

    No es un mock de librería a propósito: solo necesita exponer los métodos que
    fragmentos_comentarios() encadena y un execute() con .data.
    """

    def __init__(self, data):
        self._data = data
        self.llamadas = []

    def __getattr__(self, nombre):
        def registrar(*args, **kwargs):
            self.llamadas.append((nombre, args, kwargs))
            return self
        return registrar

    def execute(self):
        return types.SimpleNamespace(data=self._data)


class FiltroReportaTest(unittest.TestCase):
    def test_reproduce_la_tupla_de_reporta_indexable(self):
        emb = cargar_embeddings()
        self.assertEqual(
            emb.REPORTA_INDEXABLE, ("sin_corriente", "con_corriente"),
            "REPORTA_INDEXABLE cambió: si agregás un valor, volvé a medir cuánto "
            "pesa el índice ivfflat y cuántos de los fragmentos de comentario son "
            "ruido de conversación (medido 2026-09-26: 2.583 de 11.771 = 22%).")

    def test_el_filtro_y_el_orden_van_en_la_query(self):
        emb = cargar_embeddings()
        fake = FakeQuery([])
        emb.fragmentos_comentarios(fake)

        nombres = list(fake.llamadas)
        self.assertIn(("in_", ("reporta", ["sin_corriente", "con_corriente"]), {}),
                      nombres,
                      "la query no filtró por reporta en el servidor: 'pregunta', "
                      "'queja' e 'irrelevante' volverían a competir por el top-6")
        self.assertIn(("order", ("fecha",), {"desc": True}), nombres,
                      "la query no ordenó por fecha desc: sin orden, .limit(500) "
                      "devuelve filas arbitrarias y dos corridas seguidas indexan "
                      "conjuntos distintos")

        idx_order = [i for i, (n, _, _) in enumerate(fake.llamadas) if n == "order"]
        idx_limit = [i for i, (n, _, _) in enumerate(fake.llamadas) if n == "limit"]
        self.assertTrue(idx_order, "falta el order('fecha', desc=True)")
        self.assertTrue(idx_limit, "falta el limit(500)")
        self.assertLess(
            idx_order[0], idx_limit[0],
            "limit(500) debe ir DESPUÉS del order: si corta antes, el orden no "
            "decide qué 500 filas quedan")

    def test_los_fragmentos_salen_con_id_com_prefix_y_tipo_reporte(self):
        emb = cargar_embeddings()
        fila = {"message_id": 42, "fecha": "2026-09-26T10:00:00+00:00",
                "reporta": "sin_corriente", "lugar": "Centro Habana", "horas": 5}
        frags = emb.fragmentos_comentarios(FakeQuery([fila]))
        self.assertEqual(len(frags), 1)
        self.assertEqual(frags[0]["id"], "com_42")
        self.assertEqual(frags[0]["tipo"], "reporte")

    def test_sin_lugar_no_genera_fragmento(self):
        emb = cargar_embeddings()
        fila = {"message_id": 7, "fecha": "2026-09-26T10:00:00+00:00",
                "reporta": "con_corriente", "lugar": "", "horas": None}
        self.assertEqual(emb.fragmentos_comentarios(FakeQuery([fila])), [])


class CoherenciaPurgaTest(unittest.TestCase):
    def test_purga_no_puede_ser_mas_corta_que_la_ventana_de_embeddings(self):
        purga = cargar_purga()
        emb = cargar_embeddings()
        retencion = {(t[0], tuple(t[1].items())): t[3] for t in purga.TABLAS}[
            ("chatbot_fragmentos", ())]
        self.assertGreaterEqual(
            retencion, emb.DIAS_HISTORICO,
            "la retención de chatbot_fragmentos es más corta que DIAS_HISTORICO "
            "de embeddings.py: cada corrida re-insertaría lo que la purga acaba "
            "de borrar, con churn infinito de embeddings y de cuota")


if __name__ == "__main__":
    unittest.main()
