"""Regresión del sparkline 24 h: el reloj manda, no la última mención.

Bug reportado: un circuito con 34,8 h sin corriente pintaba las últimas 3 h en
rojo y el resto en verde. `aplicar_estado` anclaba el estado vigente en
`estado_fecha` (última MENCIÓN), que se refresca cada vez que un parte repite
el mismo "sin servicio" sin cambiar el estado. El titular "X h de silencio" usa
`estado_desde` (último CAMBIO), así que los bloques y las horas se
contradecían.

Offline, stdlib, py3.9.
"""

import importlib.util
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

RAIZ = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_serie24h", RAIZ / "scripts" / "build_serie24h.py"
)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

HABANA = ZoneInfo("America/Havana")
AHORA = datetime(2026, 11, 3, 22, 0, tzinfo=HABANA)
FINES = MOD.fin_de_cubos(AHORA)


def circuito(**extra):
    base = {"codigo": "X1", "estado": None, "estado_desde": None, "estado_fecha": None}
    base.update(extra)
    return base


def rojo(serie):
    return "".join("R" if v == "sin" else ("G" if v == "con" else "?") for v in serie)


class Serie24hRelojTest(unittest.TestCase):
    def test_ancla_en_estado_desde_no_en_la_ultima_mencion(self):
        # 34,8 h sin servicio, última mención hace 2 h: la ventana entera es roja.
        c = circuito(
            estado="sin servicio",
            estado_desde=(AHORA - timedelta(hours=34, minutes=48)).isoformat(),
            estado_fecha=(AHORA - timedelta(hours=2)).isoformat(),
        )
        self.assertEqual(rojo(MOD.serie_para_circuito([], FINES, c)), "R" * 24)

    def test_corte_de_17_horas_pinta_solo_lo_que_lleva_sin_luz(self):
        c = circuito(
            estado="sin servicio",
            estado_desde=(AHORA - timedelta(hours=17)).isoformat(),
            estado_fecha=(AHORA - timedelta(minutes=30)).isoformat(),
        )
        self.assertEqual(rojo(MOD.serie_para_circuito([], FINES, c)), "G" * 5 + "R" * 19)

    def test_con_servicio_viejo_no_pinta_rojo(self):
        c = circuito(
            estado="con servicio",
            estado_desde=(AHORA - timedelta(days=3)).isoformat(),
            estado_fecha=(AHORA - timedelta(days=3)).isoformat(),
        )
        self.assertEqual(rojo(MOD.serie_para_circuito([], FINES, c)), "G" * 24)

    def test_carry_rancio_no_contradice_el_cambio(self):
        # Sin ✅ posterior, el carry se queda en "sin"; el cambio a "sin" de
        # hace 1 h obliga a que justo antes hubiera corriente.
        eventos = [(AHORA - timedelta(hours=20), "sin")]
        c = circuito(
            estado="sin servicio",
            estado_desde=(AHORA - timedelta(hours=1)).isoformat(),
            estado_fecha=(AHORA - timedelta(hours=1)).isoformat(),
        )
        self.assertEqual(
            rojo(MOD.serie_para_circuito(eventos, FINES, c)),
            "??" + "G" * 19 + "R" * 3,
        )

    def test_senal_vecinal_discrepada_manda(self):
        c = circuito(
            estado="con servicio",
            estado_desde=(AHORA - timedelta(days=2)).isoformat(),
            estado_fecha=(AHORA - timedelta(days=2)).isoformat(),
            discrepado=True,
            conteo_usuario={"desde": (AHORA - timedelta(hours=5)).isoformat()},
        )
        self.assertEqual(rojo(MOD.serie_para_circuito([], FINES, c)), "G" * 17 + "R" * 7)

    def test_cambio_a_con_tras_un_corte(self):
        c = circuito(
            estado="con servicio",
            estado_desde=(AHORA - timedelta(hours=2)).isoformat(),
            estado_fecha=(AHORA - timedelta(hours=2)).isoformat(),
        )
        self.assertEqual(rojo(MOD.serie_para_circuito([], FINES, c)), "R" * 20 + "G" * 4)


if __name__ == "__main__":
    unittest.main()
