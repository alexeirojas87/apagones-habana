"""U1 — Regla de persistencia comunitaria como función pura (D1; spec §2 R1-R4).

Espejo hermético de `aplicar_senales_conteo(entry, senales)` en scripts/estado.py:
carga por importlib sin red, igual que test_desconexion_sen.py. Regla (spec §2):
`holder` = ip_hash cuyo veredicto cambió el estado por última vez; un reporte del
mismo holder con signo opuesto queda grabado pero NO mueve desde/horas. Los
comentarios_llm llegan sin ip (None) y jamás se suprimen. py3.9, offline.
"""

import importlib.util
import unittest
from pathlib import Path

RUTA = Path(__file__).parents[1] / "scripts" / "estado.py"
SPEC = importlib.util.spec_from_file_location("estado", RUTA)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

T1 = "2026-09-06T08:00:00+00:00"
T2 = "2026-09-06T09:00:00+00:00"
T3 = "2026-09-06T10:00:00+00:00"

CLAVES_VALIDAS = {"desde", "ultima_sin", "ultimo_con", "ultimo_reset", "horas", "holder_ip_hash"}


def entrada_base(**extras):
    e = {"desde": None, "ultima_sin": None, "ultimo_con": None, "holder_ip_hash": None}
    e.update(extras)
    return e


class ConteoReglasTest(unittest.TestCase):
    """Espejo S4-S9 de la spec §2 + extras de borde (X2, comentarios ip=None)."""

    def test_s4_flips_del_mismo_holder_se_graban_pero_no_mueven(self):
        # sin@hA t1, con@hA t2 → desde=t1 intacto, ultimo_con=t2.
        senales = [(T1, 1, "sin", "hA"), (T2, 2, "con", "hA")]
        e = MOD.aplicar_senales_conteo(entrada_base(), senales)
        self.assertEqual(e["desde"], T1)
        self.assertEqual(e["holder_ip_hash"], "hA")
        self.assertEqual(e["ultimo_con"], T2)
        self.assertEqual(e["ultima_sin"], T1)

    def test_s5_con_de_otro_ip_resetea(self):
        # sin@hA t1, con@hB t2 → desde=null, holder=hB.
        senales = [(T1, 1, "sin", "hA"), (T2, 2, "con", "hB")]
        e = MOD.aplicar_senales_conteo(entrada_base(), senales)
        self.assertIsNone(e["desde"])
        self.assertEqual(e["holder_ip_hash"], "hB")
        self.assertEqual(e["ultimo_con"], T2)

    def test_s6_reset_oficial_une_da_reloj_fresco(self):
        # build_circuitos resetea (desde=null, holder=null, ultimo_reset=t2);
        # el sin previo al reset se ignora y el posterior abre reloj nuevo.
        e_in = entrada_base(ultimo_reset=T2)
        senales = [(T1, 1, "sin", "hA"), (T3, 3, "sin", "hA")]
        e = MOD.aplicar_senales_conteo(e_in, senales)
        self.assertEqual(e["desde"], T3)
        self.assertEqual(e["holder_ip_hash"], "hA")
        self.assertEqual(e["ultimo_reset"], T2)

    def test_s7_corroboracion_no_transfiere_holder(self):
        # sin@hA t1, sin@hB t2 → desde queda t1 y holder NO pasa a hB;
        # con@hB t3 sí resetea (hB ≠ hA, corroboration no protege).
        e = MOD.aplicar_senales_conteo(
            entrada_base(), [(T1, 1, "sin", "hA"), (T2, 2, "sin", "hB")])
        self.assertEqual(e["desde"], T1)
        self.assertEqual(e["holder_ip_hash"], "hA")
        e = MOD.aplicar_senales_conteo(
            entrada_base(), [(T1, 1, "sin", "hA"), (T2, 2, "sin", "hB"), (T3, 3, "con", "hB")])
        self.assertIsNone(e["desde"])
        self.assertEqual(e["holder_ip_hash"], "hB")

    def test_s8_empates_de_fecha_son_deterministas_por_id(self):
        # Misma fecha: gana el id menor (orden ASC estipulado al llamar).
        senales = [(T1, 1, "sin", "hA"), (T1, 2, "sin", "hB")]
        e = MOD.aplicar_senales_conteo(entrada_base(), senales)
        self.assertEqual(e["desde"], T1)
        self.assertEqual(e["holder_ip_hash"], "hA")

    def test_s9_holder_persistido_suprime_tras_frontera_de_cron(self):
        # Entrada con holder_ip_hash persistido y ventana con solo su 'con'.
        e_in = entrada_base(desde=T1, ultima_sin=T1, holder_ip_hash="hA")
        senales = [(T2, 2, "con", "hA")]
        e = MOD.aplicar_senales_conteo(e_in, senales)
        self.assertEqual(e["desde"], T1)
        self.assertEqual(e["holder_ip_hash"], "hA")
        self.assertEqual(e["ultimo_con"], T2)

    def test_comentario_sin_ip_nunca_se_suprime(self):
        # comentarios_llm llega sin ip (None): el None no puede auto-suprimirse.
        # Con igualdad ingenua None==None, el sin ni siquiera abriría el reloj.
        e = MOD.aplicar_senales_conteo(entrada_base(), [(T1, 1, "sin", None)])
        self.assertEqual(e["desde"], T1)
        # Y su 'con' resetea efectivamente (siempre efectivo, hoy y antes).
        e = MOD.aplicar_senales_conteo(
            entrada_base(desde=T1, ultima_sin=T1), [(T2, 2, "con", None)])
        self.assertIsNone(e["desde"])
        self.assertEqual(e["ultimo_con"], T2)

    def test_con_sin_ip_despierta_reloj_vigente(self):
        # desde vigente con holder=None + 'con' sin ip → reseteo efectivo.
        e_in = entrada_base(desde=T1, ultima_sin=T1, holder_ip_hash=None)
        e = MOD.aplicar_senales_conteo(e_in, [(T2, 2, "con", None)])
        self.assertIsNone(e["desde"])

    def test_senal_previa_al_reset_se_descarta(self):
        # Guardia uniforme: 'sin' y 'con' previos al reset no tocan nada.
        e_in = entrada_base(ultimo_reset=T2)
        e = MOD.aplicar_senales_conteo(e_in, [(T1, 1, "sin", "hA"), (T1, 2, "con", "hA")])
        self.assertIsNone(e["desde"])
        self.assertIsNone(e["ultimo_con"])
        self.assertIsNone(e["holder_ip_hash"])

    def test_no_fija_horas_ni_claves_fuera_del_contrato(self):
        # La función pura no deriva horas (eso es del cron) y solo muta claves
        # conocidas (X2: consumidores ignoran claves extra).
        e_in = entrada_base(ultimo_reset=T2)
        e = MOD.aplicar_senales_conteo(e_in, [(T3, 3, "sin", "hA")])
        self.assertNotIn("horas", e)
        self.assertEqual(set(e.keys()) - CLAVES_VALIDAS, set())


if __name__ == "__main__":
    unittest.main()
