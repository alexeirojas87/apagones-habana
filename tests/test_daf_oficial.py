import importlib.util
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


RUTA = Path(__file__).parents[1] / "scripts" / "build_circuitos.py"
SPEC = importlib.util.spec_from_file_location("build_circuitos", RUTA)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class DafOficialTest(unittest.TestCase):
    def test_elige_periodo_vigente_de_un_parte_con_dos_semanas(self):
        filas = [{
            "message_id": 123,
            "fecha": "2026-07-17T04:06:34+00:00",
            "texto": """Los circuitos protegidos por DAF serán rotados cada viernes.
🛑DAF: desde el viernes 10 hasta el jueves 16 de julio.
👉AL55: Zonas 12 y 13
🛑DAF: desde el viernes 17 hasta el jueves 24 de julio.
👉AL52: Zonas 6 y 7
👉R464: Habana Nueva
""",
        }]
        ahora = datetime(2026, 7, 23, 12, tzinfo=ZoneInfo("America/Havana"))
        daf = MOD.extraer_daf_oficial(filas, ahora)
        self.assertTrue(daf["vigente"])
        self.assertEqual(daf["desde"], "2026-07-17")
        self.assertEqual(daf["hasta"], "2026-07-24")
        self.assertEqual(daf["circuitos"], ["AL52", "R464"])

    def test_infiere_mes_anterior_al_cruzar_de_mes(self):
        filas = [{
            "message_id": 456,
            "fecha": "2026-07-31T15:00:00+00:00",
            "texto": """Los circuitos designados para DAF serán rotados cada viernes.
🛑DAF: viernes 31 al jueves 6 de agosto.
👉H341: Santo Suárez
""",
        }]
        ahora = datetime(2026, 8, 2, 12, tzinfo=ZoneInfo("America/Havana"))
        daf = MOD.extraer_daf_oficial(filas, ahora)
        self.assertEqual(daf["desde"], "2026-07-31")
        self.assertEqual(daf["hasta"], "2026-08-06")
        self.assertTrue(daf["vigente"])

    def test_lista_vencida_no_se_presenta_como_vigente(self):
        filas = [{
            "message_id": 789,
            "fecha": "2026-07-10T19:06:18+00:00",
            "texto": """Los circuitos designados para DAF serán rotados cada viernes.
🛑DAF: viernes 10 al jueves 16 de julio.
👉L315: Buena Vista
""",
        }]
        ahora = datetime(2026, 7, 20, 12, tzinfo=ZoneInfo("America/Havana"))
        daf = MOD.extraer_daf_oficial(filas, ahora)
        self.assertFalse(daf["vigente"])
        self.assertEqual(daf["circuitos"], ["L315"])


if __name__ == "__main__":
    unittest.main()
