"""El catálogo publicado no puede contradecir lo que el LLM extrajo.

Reproduce el fallo real de AL53 (parte 74171, 12-ago-2026): el LLM lo extrajo
como afectado a las 22:22 y el catálogo siguió publicando "con servicio" del
restablecimiento de las 19:41.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
RUTA = SCRIPTS / "coherencia_catalogo.py"
SPEC = importlib.util.spec_from_file_location("coherencia_catalogo", RUTA)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def entrada(fecha, codigo, estado):
    return {"via": "llm", "fecha": fecha,
            "circuitos": [{"codigos": [codigo], "codigos_estado": [codigo],
                           "estado": estado}]}


class CoherenciaCatalogoTest(unittest.TestCase):
    def test_corrige_el_caso_al53(self):
        catalogo = [{"codigo": "AL53", "estado": "con servicio",
                     "estado_fecha": "2026-08-12T19:41:00+00:00",
                     "ultima_message_id": 74139}]
        llm = {"74139": entrada("2026-08-12T19:41:00+00:00", "AL53", "con servicio"),
               "74171": entrada("2026-08-12T22:22:48+00:00", "AL53", "sin servicio")}

        _, corr = MOD.corregir(catalogo, llm)

        self.assertEqual(len(corr), 1)
        self.assertEqual(catalogo[0]["estado"], "sin servicio")
        self.assertEqual(catalogo[0]["ultima_message_id"], 74171)

    def test_no_pisa_informacion_mas_nueva_del_catalogo(self):
        # El catálogo puede saber algo posterior (otro parte, o el regex sobre
        # un post que el LLM aún no procesó). Eso no es una incoherencia.
        catalogo = [{"codigo": "A800", "estado": "con servicio",
                     "estado_fecha": "2026-08-13T12:42:00+00:00"}]
        llm = {"74235": entrada("2026-08-13T10:52:00+00:00", "A800", "sin servicio")}

        _, corr = MOD.corregir(catalogo, llm)

        self.assertEqual(corr, [])
        self.assertEqual(catalogo[0]["estado"], "con servicio")

    def test_es_idempotente(self):
        catalogo = [{"codigo": "AL53", "estado": "con servicio",
                     "estado_fecha": "2026-08-12T19:41:00+00:00"}]
        llm = {"74171": entrada("2026-08-12T22:22:48+00:00", "AL53", "sin servicio")}

        MOD.corregir(catalogo, llm)
        _, segunda = MOD.corregir(catalogo, llm)

        self.assertEqual(segunda, [], "una segunda pasada no debe corregir nada")

    def test_una_coincidencia_por_calles_no_cambia_el_estado(self):
        # Sin el código escrito en el parte no hay evidencia: codigos_estado vacío.
        catalogo = [{"codigo": "AL53", "estado": "con servicio",
                     "estado_fecha": "2026-08-12T19:41:00+00:00"}]
        llm = {"74171": {"via": "llm", "fecha": "2026-08-12T22:22:48+00:00",
                         "circuitos": [{"codigos": ["AL53"], "codigos_estado": [],
                                        "estado": "sin servicio"}]}}

        _, corr = MOD.corregir(catalogo, llm)

        self.assertEqual(corr, [])
        self.assertEqual(catalogo[0]["estado"], "con servicio")

    def test_ignora_lo_que_no_viene_del_llm(self):
        catalogo = [{"codigo": "AL53", "estado": "con servicio",
                     "estado_fecha": "2026-08-12T19:41:00+00:00"}]
        llm = {"74171": {"via": "prefiltro", "fecha": "2026-08-12T22:22:48+00:00",
                         "circuitos": [{"codigos": ["AL53"], "codigos_estado": ["AL53"],
                                        "estado": "sin servicio"}]}}

        _, corr = MOD.corregir(catalogo, llm)

        self.assertEqual(corr, [])

    def test_gana_el_parte_mas_reciente(self):
        catalogo = [{"codigo": "AL53", "estado": "sin servicio",
                     "estado_fecha": "2026-08-12T18:00:00+00:00"}]
        llm = {"74171": entrada("2026-08-12T22:22:00+00:00", "AL53", "sin servicio"),
               "74190": entrada("2026-08-13T06:00:00+00:00", "AL53", "con servicio")}

        _, corr = MOD.corregir(catalogo, llm)

        self.assertEqual(catalogo[0]["estado"], "con servicio")
        self.assertEqual(corr[0]["post"], "74190")


if __name__ == "__main__":
    unittest.main()
