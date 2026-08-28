"""Serie diaria de horas sin corriente por circuito (bot_datos.horas_dia).

Responde '¿cuántas horas sin corriente lleva el circuito X hoy?': cada parte
declara horas acumuladas del corte en curso, se reconstruyen los tramos de
corte y se reparten por día LOCAL habanero, con la cola del corte abierto.
"""

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "scripts"
RUTA = SCRIPTS / "build_bot_datos.py"
SPEC = importlib.util.spec_from_file_location("build_bot_datos", RUTA)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

UTC = timezone.utc
HAB = MOD.ZoneInfo("America/Havana")


def f(iso):
    return datetime.fromisoformat(iso).replace(tzinfo=UTC)


class CortesIntervalosTest(unittest.TestCase):
    def test_declaraciones_que_sube_el_contador_son_un_solo_corte(self):
        # 8.4 -> 11.6 -> 14.4 h: mismo apagón publicado 3 veces.
        regs = [("2026-08-20T10:00", 8.4), ("2026-08-20T13:12", 11.6),
                ("2026-08-20T16:00", 14.4)]
        # ahora lejos (> HUECO_MAX_H) para no activar el corte abierto
        cortes = MOD.cortes_intervalos(regs, ahora=f("2026-08-21T20:00"))
        self.assertEqual(len(cortes), 1)
        ini, fin = cortes[0]
        self.assertEqual(ini, f("2026-08-20T01:36"))
        self.assertEqual(fin, f("2026-08-20T16:00"))

    def test_contador_que_baja_empieza_otro_corte(self):
        regs = [("2026-08-20T10:00", 8.4), ("2026-08-21T09:00", 3.0)]
        cortes = MOD.cortes_intervalos(regs, ahora=f("2026-08-21T20:00"))
        self.assertEqual(len(cortes), 2)

    def test_restauracion_de_minutos_no_fusiona_cortes(self):
        # luz vuelve a las 10:40 y se va a las 10:30+... : el parte de 11:00
        # declara 0.3 h (contador que baja) y empieza DENTRO de la tolerancia
        # de 1 h del corte anterior. Sin el guard del reinicio, se fusionaban.
        regs = [("2026-08-20T10:00", 8.4), ("2026-08-20T11:00", 0.3)]
        cortes = MOD.cortes_intervalos(regs, ahora=f("2026-08-21T20:00"))
        self.assertEqual(len(cortes), 2)
        self.assertEqual(cortes[0], (f("2026-08-20T01:36"), f("2026-08-20T10:00")))
        self.assertEqual(cortes[1], (f("2026-08-20T10:42"), f("2026-08-20T11:00")))

    def test_corte_abierto_reciente_se_extiende_hasta_ahora(self):
        # último parte hace 3 h (< HUECO_MAX_H): el apagón sigue.
        regs = [("2026-08-20T10:00", 5.0)]
        cortes = MOD.cortes_intervalos(regs, ahora=f("2026-08-20T13:00"))
        self.assertEqual(cortes[-1][1], f("2026-08-20T13:00"))

    def test_corte_viejo_no_se_extiende(self):
        # parte a las 10:00 UTC que declara 5 h: el corte fue [05:00, 10:00]
        regs = [("2026-08-19T10:00", 5.0)]
        cortes = MOD.cortes_intervalos(regs, ahora=f("2026-08-21T13:00"))
        self.assertEqual(cortes[-1][1], f("2026-08-19T10:00"))


class HorasPorDiaTest(unittest.TestCase):
    def test_corte_de_un_solo_dia(self):
        h = MOD.horas_por_dia([(f("2026-08-20T09:00"), f("2026-08-20T13:30"))])
        self.assertEqual(h, {"2026-08-20": 4.5})

    def test_corte_que_cruza_la_medianoche_cuenta_a_cada_dia(self):
        # 22:00 UTC = 18:00 en La Habana; cruza medianoche local.
        h = MOD.horas_por_dia([(f("2026-08-20T02:00"), f("2026-08-20T14:00"))])
        self.assertEqual(set(h), {"2026-08-19", "2026-08-20"})
        self.assertAlmostEqual(sum(h.values()), 12.0, places=1)

    def test_horas_hoy_con_corte_abierto(self):
        # El caso del usuario: parte con 9.1 h hace 2 h (14:00 UTC) y sigue sin
        # luz. El corte empezó a las 04:54 UTC (00:54 en La Habana) y sigue
        # abierto: hoy acumula 9.1 + 2 = 11.1 h, todas del día habanero 28.
        ahora = f("2026-08-28T16:00")
        cortes = MOD.cortes_intervalos([("2026-08-28T14:00", 9.1)], ahora)
        h = MOD.horas_por_dia(cortes)
        self.assertEqual(h, {"2026-08-28": 11.1})

    def test_dias_sin_corte_no_aparecen(self):
        h = MOD.horas_por_dia([(f("2026-08-18T09:00"), f("2026-08-18T10:00"))])
        self.assertEqual(list(h), ["2026-08-18"])


if __name__ == "__main__":
    unittest.main()
