"""Contrato de retenciones de scripts/purga.py: toda tabla de crecimiento
abierto debe estar en TABLAS con su retención por env. Hermético: no toca red
(la carga del módulo exige SUPABASE_URL/KEY; se talonean en setUp)."""

import importlib.util
import os
import unittest

RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def cargar_purga(env_extra=None):
    env = {"SUPABASE_URL": "https://ejemplo.supabase.co",
           "SUPABASE_SERVICE_KEY": "k"}
    env.update(env_extra or {})
    viejo = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        spec = importlib.util.spec_from_file_location(
            "purga", os.path.join(RAIZ, "scripts", "purga.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in viejo.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class RetencionesTest(unittest.TestCase):
    def test_reportes_y_sugerencias_tienen_retencion(self):
        mod = cargar_purga()
        tablas = {(t[0], tuple(t[1].items())): t[3] for t in mod.TABLAS}
        self.assertEqual(tablas[("reportes", ())], 30,
                         "reportes sin retención: crece para siempre")
        self.assertEqual(tablas[("sugerencias", ())], 14,
                         "sugerencias sin retención: solo sirve al rate-limit de 24h")

    def test_retenciones_por_env(self):
        mod = cargar_purga({"PURGA_REPORTES_DIAS": "7",
                            "PURGA_SUGERENCIAS_DIAS": "3"})
        tablas = {(t[0], tuple(t[1].items())): t[3] for t in mod.TABLAS}
        self.assertEqual(tablas[("reportes", ())], 7)
        self.assertEqual(tablas[("sugerencias", ())], 3)

    def test_retenciones_existentes_no_cambian(self):
        mod = cargar_purga()
        tablas = {(t[0], tuple(t[1].items())): t[3] for t in mod.TABLAS}
        self.assertEqual(tablas[("mensajes", (("chat", "eq.comentarios"),))], 60)
        self.assertEqual(tablas[("mensajes", (("chat", "eq.canal"),))], 365)
        self.assertEqual(tablas[("comentarios_llm", ())], 120)
        self.assertEqual(tablas[("eventos", ())], 365)
        self.assertEqual(tablas[("chatbot_fragmentos", ())], 90)


if __name__ == "__main__":
    unittest.main()
