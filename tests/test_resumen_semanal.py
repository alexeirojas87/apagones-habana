"""Resumen semanal — pruebas de las funciones puras de agregación.

Carga scripts/resumen_semanal.py por importlib (sin red, sin variables de
entorno, como el resto de espejos herméticos de la suite) y verifica con
datos sintéticos: horas sin corriente por municipio y resumen semanal, tops
por municipio, clasificación de tipos de avería del texto del canal,
roturas de la ventana y circuitos que no se están afectando. py3.9, offline.
"""

import importlib.util
import unittest
from datetime import date, datetime
from pathlib import Path

RUTA = Path(__file__).parents[1] / "scripts" / "resumen_semanal.py"
SPEC = importlib.util.spec_from_file_location("resumen_semanal", RUTA)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

GEN = datetime.fromisoformat("2026-09-11T12:00:00+00:00")
# Ventana de horas: los últimos 7 días calendario (los que terminan en el
# día de `generado`), techo exacto de 168 h por circuito.
DIA0, DIA1 = date(2026, 9, 5), date(2026, 9, 11)


def catalogo_base():
    """Catálogo sintético: 3 circuitos de Playa y 1 del Cerro, con estados y
    última mención recientes (para que la vigencia no decaiga salvo donde
    interesa: P3 lleva más de una semana en silencio)."""
    return [
        {"codigo": "P1", "municipio": "Playa", "estado": "sin servicio",
         "estado_fecha": "2026-09-10T00:00:00+00:00",
         "ultima": "2026-09-10T00:00:00+00:00"},
        {"codigo": "P2", "municipio": "Playa", "estado": "con servicio",
         "estado_fecha": "2026-09-10T00:00:00+00:00",
         "ultima": "2026-09-10T00:00:00+00:00"},
        {"codigo": "P3", "municipio": "Playa", "estado": "con servicio",
         "estado_fecha": "2026-09-01T00:00:00+00:00",
         "ultima": "2026-09-01T00:00:00+00:00"},
        {"codigo": "C1", "municipio": "Cerro", "estado": "sin servicio",
         "estado_fecha": "2026-09-10T00:00:00+00:00",
         "ultima": "2026-09-10T00:00:00+00:00"},
    ]


HORAS = {
    "generado": GEN.isoformat(),
    "total": {"P1": 3.5, "C1": 24.0, "P2": 5.0},
    "por_dia": {
        "P1": {"2026-09-05": 2.5, "2026-09-06": 1.0, "2026-08-01": 9.9},
        "C1": {"2026-09-04": 5.0, "2026-09-06": 24.0, "2026-09-20": 30.0},
        "P2": {"2026-08-01": 5.0},  # con medición, 0 h dentro de la ventana
    },
}


class CategorizarRoturaTest(unittest.TestCase):
    """Clasificación del tipo de avería desde el texto original del canal."""

    def test_transformador_danado_gana_a_secundaria(self):
        # La combinación habitual del canal: gana el patrón más específico.
        texto = ("🛑⚡️Consumidores del municipio Guanabacoa se detectó una "
                 "AVERÍA SECUNDARIA por TRANSFORMADOR DAÑADO en: 2da / 3ra.")
        self.assertEqual(MOD.categorizar_rotura(texto), "Transformador dañado")

    def test_averia_secundaria_sola(self):
        self.assertEqual(MOD.categorizar_rotura("se detectó una AVERÍA SECUNDARIA"),
                         "Avería secundaria")

    def test_averia_primaria_y_primario_partido(self):
        self.assertEqual(MOD.categorizar_rotura("AVERÍA PRIMARIA en Reparto X"),
                         "Avería primaria")
        # Sin tilde y con doble espacio: la normalización lo captura igual.
        self.assertEqual(MOD.categorizar_rotura("AVERÍA por PRIMARIO  PARTIDO en X"),
                         "Avería primaria")

    def test_otros_y_vacios(self):
        self.assertEqual(MOD.categorizar_rotura("se reporta falla en la red"),
                         "Otra avería")
        self.assertEqual(MOD.categorizar_rotura(""), "Otra avería")
        self.assertEqual(MOD.categorizar_rotura(None), "Otra avería")


class HorasVentanaTest(unittest.TestCase):
    """Suma de horas confirmadas por circuito dentro de la ventana."""

    def test_suma_solo_dias_de_la_ventana(self):
        # P1: 2.5 + 1.0 en ventana; el 9.9 del 01/08 queda fuera.
        self.assertEqual(MOD.horas_sin_ventana(HORAS, "P1", DIA0, DIA1), 3.5)
        # C1: 24.0 en ventana; el 30.0 del 20/09 queda fuera.
        self.assertEqual(MOD.horas_sin_ventana(HORAS, "C1", DIA0, DIA1), 24.0)

    def test_sin_registros_en_la_ventana_o_sin_datos(self):
        self.assertEqual(MOD.horas_sin_ventana(HORAS, "P2", DIA0, DIA1), 0.0)
        self.assertEqual(MOD.horas_sin_ventana(HORAS, "ZZZ", DIA0, DIA1), 0.0)

    def test_resumen_por_municipio_ordenado_y_total(self):
        filas, total = MOD.resumen_por_municipio(catalogo_base(), HORAS, DIA0, DIA1)
        self.assertEqual([m for m, _ in filas], ["Cerro", "Playa"])  # 24.0 > 3.5
        por_nombre = dict(filas)
        playa = por_nombre["Playa"]
        self.assertEqual(playa["catalogados"], 3)
        self.assertEqual(playa["afectados"], 1)
        self.assertAlmostEqual(playa["horas_sin"], 3.5)
        self.assertAlmostEqual(playa["horas_con"], 3 * 168 - 3.5)
        self.assertAlmostEqual(playa["pct"], (3 * 168 - 3.5) / (3 * 168) * 100)
        self.assertEqual(total["catalogados"], 4)
        self.assertEqual(total["afectados"], 2)
        self.assertAlmostEqual(total["horas_sin"], 27.5)
        self.assertAlmostEqual(total["horas_con"], 4 * 168 - 27.5)

    def test_resumen_piso_a_cero_con_exceso_de_horas(self):
        cat = [{"codigo": "X1", "municipio": "Playa"}]
        horas = {"por_dia": {"X1": {str(DIA0): 200.0}}}  # 200 h > techo semanal
        filas, total = MOD.resumen_por_municipio(cat, horas, DIA0, DIA1)
        self.assertEqual(filas[0][1]["horas_con"], 0.0)
        self.assertEqual(total["horas_con"], 0.0)


class TopPorMunicipioTest(unittest.TestCase):
    """Tops de horas sin y con corriente por municipio."""

    def test_top_sin_solo_afectados_y_con_menciones(self):
        menciones = MOD.menciones_ventana(
            {"10": {"fecha": "2026-09-05T15:00:00+00:00", "tipo": "afectacion",
                    "circuitos": [{"codigos": ["P1"]}], "por_confirmar": []},
             "11": {"fecha": "2026-09-06T15:00:00+00:00", "tipo": "averia",
                    "circuitos": [{"codigos": ["P1"]}], "por_confirmar": ["C1"]}},
            datetime.fromisoformat("2026-09-04T12:00:00+00:00"), GEN)
        top = MOD.top_sin_por_municipio(catalogo_base(), HORAS, DIA0, DIA1, menciones)
        self.assertEqual(top["Cerro"], [("C1", 24.0, 1)])
        self.assertEqual(top["Playa"], [("P1", 3.5, 2)])

    def test_top_con_exige_datos_y_orden_desc(self):
        top = MOD.top_con_por_municipio(catalogo_base(), HORAS, DIA0, DIA1)
        # P2 (con medición, 0 h en ventana) va sobre P1; P3 sin medición no entra.
        self.assertEqual(top["Playa"], [("P2", 168.0), ("P1", 164.5)])
        self.assertEqual(top["Cerro"], [("C1", 144.0)])

    def test_top_sin_recorta_a_tres(self):
        cat = [{"codigo": f"M{i}", "municipio": "Playa"} for i in range(5)]
        horas = {"por_dia": {f"M{i}": {str(DIA0): float(i + 1)} for i in range(5)}}
        top = MOD.top_sin_por_municipio(cat, horas, DIA0, DIA1, {})
        self.assertEqual(len(top["Playa"]), 3)
        self.assertEqual([cod for cod, _, _ in top["Playa"]], ["M4", "M3", "M2"])


class RoturasVentanaTest(unittest.TestCase):
    """Partes de avería de la ventana: total, conteo y filas por circuito."""

    def setUp(self):
        self.partes = {
            "100": {"fecha": "2026-09-05T15:00:00+00:00", "tipo": "averia",
                    "circuitos": [{"codigos": ["R1"], "calles": "🚨 Calle Uno 🚨",
                                   "municipio": "Playa"}]},
            "101": {"fecha": "2026-09-06T15:00:00+00:00", "tipo": "averia",
                    "circuitos": [{"codigos": [], "calles": "Calle Dos",
                                   "municipio": "Cerro"},
                                  {"codigos": [], "calles": "Calle Tres",
                                   "municipio": "Cerro"}]},
            "102": {"fecha": "2026-09-02T15:00:00+00:00", "tipo": "averia",
                    "circuitos": [{"codigos": [], "calles": "Fuera",
                                   "municipio": "Playa"}]},
            "103": {"fecha": "2026-09-05T16:00:00+00:00", "tipo": "afectacion",
                    "circuitos": [{"codigos": [], "calles": "No es avería",
                                   "municipio": "Playa"}]},
        }
        self.canal = {
            "100": {"texto": "🛑 AVERÍA SECUNDARIA por TRANSFORMADOR DAÑADO en Calle Uno"},
            # el mensaje 101 ya no está en el caché del canal
        }

    def test_total_cuenta_partes_y_filas_cuenta_circuitos(self):
        rot = MOD.roturas_ventana(
            self.partes, self.canal,
            datetime.fromisoformat("2026-09-04T12:00:00+00:00"), GEN)
        self.assertEqual(rot["total"], 2)          # 2 partes de avería en ventana
        self.assertEqual(len(rot["filas"]), 3)     # 1 + 2 circuitos afectados
        self.assertEqual(rot["conteo"][("Playa", "Transformador dañado")], 1)
        self.assertEqual(rot["conteo"][("Cerro", "Otra avería")], 2)  # texto ausente

    def test_filas_cronologicas_y_texto_limpio(self):
        rot = MOD.roturas_ventana(
            self.partes, self.canal,
            datetime.fromisoformat("2026-09-04T12:00:00+00:00"), GEN)
        fechas = [f["fecha"] for f in rot["filas"]]
        self.assertEqual(fechas, sorted(fechas))
        self.assertEqual(rot["filas"][0]["calles"], "Calle Uno")  # sin emojis
        self.assertEqual(rot["filas"][0]["tipo"], "Transformador dañado")

    def test_municipio_canonico_del_parte(self):
        # Variantes escritas a mano en el parte (sin artículo, con tilde mala)
        # se agrupan bajo el nombre exacto del catálogo.
        nombres = {"La Lisa", "San Miguel del Padrón"}
        self.assertEqual(MOD.municipio_canonico("Lisa", nombres), "La Lisa")
        self.assertEqual(MOD.municipio_canonico("San Miguel del Padròn", nombres),
                         "San Miguel del Padrón")
        self.assertEqual(MOD.municipio_canonico("Playa", nombres), "Playa")  # desconocido: intacto
        self.assertEqual(MOD.municipio_canonico("Cerro", None), "Cerro")


class NoAfectadosYVigenciaTest(unittest.TestCase):
    """Circuitos que no se están afectando y distribución de ciclo de vida."""

    def test_solo_con_servicio_confirmado_y_sin_horas(self):
        cat = catalogo_base()
        vigencias = MOD.vigencias_de(cat, GEN, False)
        # P1 sin (reciente), P2 con (reciente), P3 asum (silencio de una
        # semana completa), C1 sin.
        self.assertEqual(vigencias["P1"], "sin")
        self.assertEqual(vigencias["P2"], "con")
        self.assertEqual(vigencias["P3"], "asum")
        no_afec = MOD.no_afectados_por_municipio(cat, HORAS, vigencias, DIA0, DIA1)
        # P2: con servicio confirmado y 0 h de corte en la ventana. P1 y C1
        # registran horas; P3 es asumido (no sabemos); nadie más califica.
        self.assertEqual(no_afec, {"Playa": ["P2"]})

    def test_con_vecinos_no_entra_en_no_afectados(self):
        cat = catalogo_base()
        cat[3].update({"codigo": "P4", "municipio": "Playa",
                       "estado": "sin servicio",
                       "estado_fecha": "2026-09-08T00:00:00+00:00",
                       "ultima": "2026-09-08T00:00:00+00:00"})
        # Reporte vecinal fresco ("volvió" el 10/09): con_vecinos, no desconocido.
        MOD.fusionar_conteo(cat, {"P4": {"desde": None,
                                         "ultimo_con": "2026-09-10T12:00:00+00:00",
                                         "ultima_sin": None,
                                         "ultimo_reset": None}})
        vigencias = MOD.vigencias_de(cat, GEN, False)
        self.assertEqual(vigencias["P4"], "con_vecinos")
        no_afec = MOD.no_afectados_por_municipio(cat, HORAS, vigencias, DIA0, DIA1)
        self.assertNotIn("P4", no_afec.get("Playa", []))

    def test_distribucion_vigencia_cuenta_todos_los_estados(self):
        cat = catalogo_base()
        dist = MOD.distribucion_vigencia(cat, GEN, False)
        self.assertEqual(dist["sin"], 2)          # P1, C1
        self.assertEqual(dist["con"], 1)          # P2
        self.assertEqual(dist["asum"], 1)         # P3
        self.assertEqual(dist["con_vecinos"], 0)
        self.assertEqual(dist["desconocido"], 0)

    def test_evento_nacional_congela_el_decaimiento(self):
        # Con evento_nacional no hay decaimiento: P3 queda "con", no "asum".
        dist = MOD.distribucion_vigencia(catalogo_base(), GEN, True)
        self.assertEqual(dist["con"], 2)          # P2, P3
        self.assertEqual(dist["asum"], 0)


if __name__ == "__main__":
    unittest.main()
