"""Resumen semanal — pruebas de las funciones puras de agregación.

Carga scripts/resumen_semanal.py por importlib (sin red, sin variables de
entorno, como el resto de espejos herméticos de la suite) y verifica con
datos sintéticos: horas sin corriente por municipio y resumen semanal, tops
globales, el gráfico de distribución del déficit (datos ordenados con
gradiente, PNG con matplotlib si está disponible y su bloque ASCII del
texto), clasificación de tipos de avería del texto del canal, roturas de la
ventana, señales vecinales, circuitos que no se están afectando (criterio
del mantenedor) y las tablas legibles de estado del sistema y roturas.
py3.9, offline.
"""

import importlib.util
import struct
import unittest
from datetime import date, datetime, timedelta
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


class TopGlobalesTest(unittest.TestCase):
    """Tops GLOBALES de horas sin y con corriente (un solo ranking, top 15)."""

    def test_top_sin_solo_afectados_y_con_menciones(self):
        menciones = MOD.menciones_ventana(
            {"10": {"fecha": "2026-09-05T15:00:00+00:00", "tipo": "afectacion",
                    "circuitos": [{"codigos": ["P1"]}], "por_confirmar": []},
             "11": {"fecha": "2026-09-06T15:00:00+00:00", "tipo": "averia",
                    "circuitos": [{"codigos": ["P1"]}], "por_confirmar": ["C1"]}},
            datetime.fromisoformat("2026-09-04T12:00:00+00:00"), GEN)
        top = MOD.top_sin_global(catalogo_base(), HORAS, DIA0, DIA1, menciones)
        self.assertEqual(top, [("Cerro", "C1", 24.0, 1),
                               ("Playa", "P1", 3.5, 2)])

    def test_top_con_exige_datos_y_orden_desc_con_estado(self):
        vigencias = MOD.vigencias_de(catalogo_base(), GEN, False)
        top = MOD.top_con_global(catalogo_base(), HORAS, vigencias, DIA0, DIA1)
        # P2 (con medición, 0 h en ventana) va sobre P1; P3 sin medición no
        # entra; cada fila trae la etiqueta del estado del ciclo.
        self.assertEqual(top, [("Playa", "P2", 168.0, "con servicio"),
                               ("Playa", "P1", 164.5, "sin servicio"),
                               ("Cerro", "C1", 144.0, "sin servicio")])

    def test_top_sin_recorta_a_quince(self):
        cat = [{"codigo": f"M{i:02d}", "municipio": "Playa"} for i in range(20)]
        horas = {"por_dia": {f"M{i:02d}": {str(DIA0): float(20 - i)}
                             for i in range(20)}}
        top = MOD.top_sin_global(cat, horas, DIA0, DIA1, {})
        self.assertEqual(len(top), 15)
        self.assertEqual([cod for _, cod, _, _ in top][0], "M00")  # 20 h
        self.assertEqual([cod for _, cod, _, _ in top][-1], "M14")  # 6 h


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

    def test_filas_por_fecha_desc_y_texto_limpio(self):
        rot = MOD.roturas_ventana(
            self.partes, self.canal,
            datetime.fromisoformat("2026-09-04T12:00:00+00:00"), GEN)
        fechas = [f["fecha"] for f in rot["filas"]]
        self.assertEqual(fechas, sorted(fechas, reverse=True))  # más nuevo primero
        self.assertEqual(rot["filas"][0]["calles"], "Calle Dos")  # sin emojis
        self.assertEqual(rot["filas"][-1]["tipo"], "Transformador dañado")

    def test_municipio_canonico_del_parte(self):
        # Variantes escritas a mano en el parte (sin artículo, con tilde mala)
        # se agrupan bajo el nombre exacto del catálogo.
        nombres = {"La Lisa", "San Miguel del Padrón"}
        self.assertEqual(MOD.municipio_canonico("Lisa", nombres), "La Lisa")
        self.assertEqual(MOD.municipio_canonico("San Miguel del Padròn", nombres),
                         "San Miguel del Padrón")
        self.assertEqual(MOD.municipio_canonico("Playa", nombres), "Playa")  # desconocido: intacto
        self.assertEqual(MOD.municipio_canonico("Cerro", None), "Cerro")


class RoturasResumenTest(unittest.TestCase):
    """Tabla resumen de roturas legible: cantidad DESC y enteros."""

    def test_cantidad_desc_y_enteros(self):
        conteo = {("Playa", "Otra avería"): 1.0,
                  ("Playa", "Transformador dañado"): 3.0,
                  ("Cerro", "Otra avería"): 2.0}
        filas = MOD.filas_roturas_resumen(conteo)
        self.assertEqual(filas, [
            ["Playa", "Transformador dañado", "3"],
            ["Cerro", "Otra avería", "2"],
            ["Playa", "Otra avería", "1"],
        ])

    def test_vacio(self):
        self.assertEqual(MOD.filas_roturas_resumen({}), [])


class EstadoSistemaTest(unittest.TestCase):
    """Tabla "Estado del sistema": filas autoexplicativas, cantidad DESC."""

    def test_orden_desc_y_desempate_por_gravedad(self):
        dist = {"sin": 3, "con": 5, "con_vecinos": 1, "sin_vecinos": 2,
                "desconocido": 0, "asum": 0}
        pares = MOD.estado_sistema_ordenado(dist)
        self.assertEqual([c for c, _ in pares],
                         ["con", "sin", "sin_vecinos", "con_vecinos",
                          "desconocido", "asum"])
        self.assertEqual([n for _, n in pares], [5, 3, 2, 1, 0, 0])

    def test_filas_etiquetas_autoexplicativas_y_enteros(self):
        filas = MOD.filas_estado_sistema({"sin": 3.0, "sin_vecinos": 2.0})
        self.assertEqual(filas[0],
                         ["Sin corriente (confirmado por parte oficial)", "3"])
        self.assertEqual(
            filas[1],
            ["Sin corriente (según reportes de vecinos) — parte oficial "
             "con corriente", "2"])
        # Sin "discrepado" como etiqueta: el estado sin_vecinos lo sustituye.
        self.assertFalse(any("Discrepado" in f[0] for f in filas))


class SenalesVecinalesTest(unittest.TestCase):
    """Señales vecinales de la semana: visibilidad de los reportes de la
    población, que mandan en ambos sentidos (discrepado y con_vecinos)."""

    def test_cuenta_senales_en_ventana_con_desglose(self):
        cat = catalogo_base()
        cat[3].update({"codigo": "P4", "municipio": "Playa",
                       "estado": "sin servicio",
                       "estado_fecha": "2026-09-08T00:00:00+00:00",
                       "ultima": "2026-09-08T00:00:00+00:00"})
        # P2 discrepado: parte oficial "con" y los vecinos lo reportan sin.
        cat[1]["discrepado"] = True
        cu = {
            "P2": {"desde": "2026-09-10T00:00:00+00:00", "ultima_sin": None,
                   "ultimo_con": None, "ultimo_reset": None},
            "P4": {"desde": None, "ultima_sin": None,
                   "ultimo_con": "2026-09-10T12:00:00+00:00",
                   "ultimo_reset": None},
            "VIEJO": {"desde": "2026-08-01T00:00:00+00:00", "ultima_sin": None,
                      "ultimo_con": None, "ultimo_reset": None},  # fuera
        }
        MOD.fusionar_conteo(cat, cu)
        vigencias = MOD.vigencias_de(cat, GEN, False)
        sen = MOD.senales_vecinales(cat, cu, vigencias, GEN)
        self.assertEqual(sen["total"], 2)               # VIEJO queda fuera
        self.assertEqual(sen["por_municipio"], [("Playa", 2)])
        self.assertEqual(sen["sin_vecinos"], 1)         # P2 discrepado vigente
        self.assertEqual(sen["con_vecinos"], 1)         # P4 según vecinos

    def test_sin_senales_en_la_ventana(self):
        sen = MOD.senales_vecinales(catalogo_base(), {}, {}, GEN)
        self.assertEqual(sen, {"total": 0, "por_municipio": [],
                               "sin_vecinos": 0, "con_vecinos": 0})


class NoAfectadosYVigenciaTest(unittest.TestCase):
    """Circuitos que no se están afectando (criterio del mantenedor) y
    distribución de ciclo de vida."""

    def test_con_y_asum_sin_horas_entran(self):
        cat = catalogo_base()
        vigencias = MOD.vigencias_de(cat, GEN, False)
        # P1 sin (reciente), P2 con (reciente), P3 asum (silencio de una
        # semana completa), C1 sin.
        self.assertEqual(vigencias["P1"], "sin")
        self.assertEqual(vigencias["P2"], "con")
        self.assertEqual(vigencias["P3"], "asum")
        no_afec = MOD.no_afectados_por_municipio(cat, HORAS, vigencias, DIA0, DIA1)
        # P2 (con servicio confirmado) y P3 (asumido con corriente) no
        # acumulan horas de corte en la ventana: ambos entran. P1 y C1
        # registran horas confirmadas: quedan fuera.
        self.assertEqual(no_afec, {"Playa": ["P2", "P3"]})

    def test_con_vecinos_entra_en_no_afectados(self):
        cat = catalogo_base()
        cat[3].update({"codigo": "P4", "municipio": "Playa",
                       "estado": "sin servicio",
                       "estado_fecha": "2026-09-08T00:00:00+00:00",
                       "ultima": "2026-09-08T00:00:00+00:00"})
        # Reporte vecinal fresco ("volvió" el 10/09): con_vecinos.
        MOD.fusionar_conteo(cat, {"P4": {"desde": None,
                                         "ultimo_con": "2026-09-10T12:00:00+00:00",
                                         "ultima_sin": None,
                                         "ultimo_reset": None}})
        vigencias = MOD.vigencias_de(cat, GEN, False)
        self.assertEqual(vigencias["P4"], "con_vecinos")
        no_afec = MOD.no_afectados_por_municipio(cat, HORAS, vigencias, DIA0, DIA1)
        self.assertIn("P4", no_afec.get("Playa", []))

    def test_desconocido_viejo_entra_y_reciente_no(self):
        cat = [
            # Silencio de 200 h: desconocido con más de 7 días de silencio.
            {"codigo": "DV", "municipio": "Playa", "estado": "con servicio",
             "estado_fecha": "2026-09-03T04:00:00+00:00",
             "ultima": "2026-09-03T04:00:00+00:00"},
            # Silencio de 100 h: desconocido aún dentro de su semana de
            # incertidumbre.
            {"codigo": "DR", "municipio": "Playa", "estado": "con servicio",
             "estado_fecha": "2026-09-07T08:00:00+00:00",
             "ultima": "2026-09-07T08:00:00+00:00"},
        ]
        vigencias = MOD.vigencias_de(cat, GEN, False)
        self.assertEqual(vigencias["DV"], "desconocido")
        self.assertEqual(vigencias["DR"], "desconocido")
        no_afec = MOD.no_afectados_por_municipio(cat, {}, vigencias,
                                                 DIA0, DIA1, GEN)
        self.assertEqual(no_afec, {"Playa": ["DV"]})

    def test_sin_y_sin_vecinos_no_entran(self):
        cat = [
            {"codigo": "SV", "municipio": "Playa", "estado": "con servicio",
             "estado_fecha": "2026-09-10T00:00:00+00:00",
             "ultima": "2026-09-10T00:00:00+00:00", "discrepado": True},
            {"codigo": "S1", "municipio": "Playa", "estado": "sin servicio",
             "estado_fecha": "2026-09-10T00:00:00+00:00",
             "ultima": "2026-09-10T00:00:00+00:00"},
        ]
        vigencias = MOD.vigencias_de(cat, GEN, False)
        self.assertEqual(vigencias["SV"], "sin_vecinos")
        self.assertEqual(vigencias["S1"], "sin")
        no_afec = MOD.no_afectados_por_municipio(cat, {}, vigencias,
                                                 DIA0, DIA1, GEN)
        self.assertEqual(no_afec, {})

    def test_municipios_ordenados_por_conteo_desc(self):
        cat = ([{"codigo": f"P{i}", "municipio": "Playa"} for i in range(3)]
               + [{"codigo": "C0", "municipio": "Cerro"}])
        vigencias = MOD.vigencias_de(cat, GEN, False)  # sin estado → asum
        no_afec = MOD.no_afectados_por_municipio(cat, {}, vigencias,
                                                 DIA0, DIA1, GEN)
        self.assertEqual(list(no_afec), ["Playa", "Cerro"])  # 3 > 1

    def test_distribucion_vigencia_cuenta_todos_los_estados(self):
        cat = catalogo_base()
        dist = MOD.distribucion_vigencia(cat, GEN, False)
        self.assertEqual(dist["sin"], 2)          # P1, C1
        self.assertEqual(dist["con"], 1)          # P2
        self.assertEqual(dist["asum"], 1)         # P3
        self.assertEqual(dist["con_vecinos"], 0)
        self.assertEqual(dist["sin_vecinos"], 0)
        self.assertEqual(dist["desconocido"], 0)

    def test_distribucion_cuenta_sin_vecinos_en_lugar_de_discrepado(self):
        # Dirección 1 del reporte vecinal: UNE "con" + vecinos "sin" →
        # sin_vecinos (ya no hay etiqueta "discrepado").
        cat = catalogo_base()
        cat[1]["discrepado"] = True  # P2
        dist = MOD.distribucion_vigencia(cat, GEN, False)
        self.assertEqual(dist["sin_vecinos"], 1)
        self.assertEqual(dist["con"], 0)

    def test_evento_nacional_congela_el_decaimiento(self):
        # Con evento_nacional no hay decaimiento: P3 queda "con", no "asum".
        dist = MOD.distribucion_vigencia(catalogo_base(), GEN, True)
        self.assertEqual(dist["con"], 2)          # P2, P3
        self.assertEqual(dist["asum"], 0)


class GraficoDeficitTest(unittest.TestCase):
    """Datos del gráfico 'Dónde se soporta el déficit de la capital': TODOS
    los circuitos catalogados ordenados DESC por horas confirmadas (incluidos
    los de 0 h), gradiente rojo→azul y concentración del top 10 sobre el
    TOTAL de horas sin corriente de la semana."""

    @staticmethod
    def catalogo_grafico():
        """14 circuitos de un mismo municipio: 12 con horas en la ventana
        (48, 44, …, 4) y 2 sin registros de corte (0 h — siempre con
        corriente, parte del mensaje visual)."""
        return [{"codigo": f"G{i:02d}", "municipio": "Playa"}
                for i in range(14)]

    @classmethod
    def horas_grafico(cls, n=12):
        """Horas del catálogo anterior: G00 48 h … G11 4 h en ventana;
        G12 y G13 sin registros (0 h)."""
        return {"por_dia": {f"G{i:02d}": {str(DIA0): float(48 - 4 * i)}
                            for i in range(n)}}

    @classmethod
    def datos_grafico(cls):
        return MOD.datos_grafico_deficit(
            cls.catalogo_grafico(), cls.horas_grafico(), DIA0, DIA1)

    def test_todos_los_circuitos_orden_desc_incluye_ceros(self):
        g = self.datos_grafico()
        # TODOS los circuitos del catálogo, incluidos los de 0 h.
        self.assertEqual(len(g["filas"]), 14)
        horas = [f["horas"] for f in g["filas"]]
        self.assertEqual(horas, sorted(horas, reverse=True))
        self.assertEqual(horas[0], 48.0)
        self.assertEqual(horas[-2:], [0.0, 0.0])   # G12 y G13, siempre con corriente
        self.assertEqual([f["codigo"] for f in g["filas"]][:3],
                         ["G00", "G01", "G02"])
        self.assertEqual([f["codigo"] for f in g["filas"]][-2:],
                         ["G12", "G13"])

    def test_gradiente_primera_barra_roja_ultima_azul(self):
        g = self.datos_grafico()
        self.assertEqual(g["filas"][0]["color"], "#dc2626")   # máx: rojo
        self.assertEqual(g["filas"][-1]["color"], "#2563eb")  # 0 h: azul
        # Interpolación RGB lineal al 50% del máximo (G06 con 24 h).
        self.assertEqual(g["filas"][6]["color"], "#804488")

    def test_concentracion_denominador_total_semanal(self):
        # El top 10 suma 300 h; el total de la semana (con G10, G11 y los
        # de 0 h) es 312 h — el denominador correcto.
        g = self.datos_grafico()
        self.assertEqual(g["top_n"], 10)
        self.assertAlmostEqual(sum(f["horas"] for f in g["filas"][:10]), 300.0)
        self.assertAlmostEqual(g["total"], 312.0)
        self.assertEqual(g["concentracion"], round(300 * 100 / 312))   # 96

    def test_semana_sin_cortes_devuelve_none(self):
        cat = self.catalogo_grafico()
        self.assertIsNone(MOD.datos_grafico_deficit(cat, {}, DIA0, DIA1))
        # Horas solo fuera de la ventana: para la semana es lo mismo que nada.
        horas_fuera = {"por_dia": {c["codigo"]: {"2026-08-01": 24.0}
                                   for c in cat}}
        self.assertIsNone(
            MOD.datos_grafico_deficit(cat, horas_fuera, DIA0, DIA1))

    def test_menos_de_diez_circuitos_usa_los_que_haya(self):
        g = MOD.datos_grafico_deficit(self.catalogo_grafico()[:3],
                                      self.horas_grafico(3), DIA0, DIA1)
        self.assertEqual(len(g["filas"]), 3)
        self.assertEqual(g["top_n"], 3)
        self.assertEqual(g["concentracion"], 100)  # todo el corte está en el top

    def test_empate_por_municipio_luego_codigo(self):
        cat = [{"codigo": "C-B", "municipio": "Cerro"},
               {"codigo": "C-A", "municipio": "Cerro"},
               {"codigo": "P-B", "municipio": "Playa"},
               {"codigo": "P-A", "municipio": "Playa"}]
        horas = {"por_dia": {c["codigo"]: {str(DIA0): 10.0} for c in cat}}
        g = MOD.datos_grafico_deficit(cat, horas, DIA0, DIA1)
        self.assertEqual([(f["municipio"], f["codigo"]) for f in g["filas"]],
                         [("Cerro", "C-A"), ("Cerro", "C-B"),
                          ("Playa", "P-A"), ("Playa", "P-B")])


class GraficoDeficitPngTest(unittest.TestCase):
    """Generación del PNG con matplotlib (backend Agg). Si matplotlib no
    está disponible en el entorno, los tests que lo exigen se saltan
    limpios (mismo patrón que 'git no disponible')."""

    @staticmethod
    def datos_grafico():
        return MOD.datos_grafico_deficit(
            GraficoDeficitTest.catalogo_grafico(),
            GraficoDeficitTest.horas_grafico(), DIA0, DIA1)

    def _requiere_matplotlib(self):
        try:
            import matplotlib  # noqa: F401
        except ImportError:
            self.skipTest("matplotlib no disponible")

    def test_png_valido_dimensiones_y_tamano(self):
        self._requiere_matplotlib()
        png = MOD.generar_grafico_deficit(self.datos_grafico())
        self.assertIsNotNone(png)
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertGreater(len(png), 0)
        # Techo del spec: <= 300 KB.
        self.assertLessEqual(len(png), MOD.GRAFICO_LIMITE_BYTES)
        # Dimensiones exactas 1400×420 px (IHDR: ancho y alto big-endian).
        ancho, alto = struct.unpack(">II", png[16:24])
        self.assertEqual((ancho, alto),
                         (MOD.GRAFICO_ANCHO_PX, MOD.GRAFICO_ALTO_PX))

    def test_sin_datos_devuelve_none(self):
        self.assertIsNone(MOD.generar_grafico_deficit(None))
        self.assertIsNone(MOD.generar_grafico_deficit({"filas": []}))


class GraficoDeficitRenderTest(unittest.TestCase):
    """Render del gráfico: titular de concentración ENCIMA de la imagen
    incrustada por cid en el HTML (o nota textual sin matplotlib) y bloque
    ASCII del top 10 en el texto; la sección desaparece si no hubo cortes."""

    @classmethod
    def datos_grafico(cls):
        return GraficoDeficitTest.datos_grafico()

    @staticmethod
    def res_base(grafico):
        """Fixture mínimo del agregado semanal, suficiente para renderizar."""
        return {
            "generado": GEN,
            "desde": GEN - timedelta(days=7),
            "filas": [
                ("Cerro", {"catalogados": 1, "afectados": 1, "horas_sin": 24.0,
                           "horas_con": 144.0, "pct": 85.7}),
                ("Playa", {"catalogados": 3, "afectados": 1, "horas_sin": 3.5,
                           "horas_con": 500.5, "pct": 99.3}),
            ],
            "total": {"catalogados": 4, "afectados": 2, "horas_sin": 27.5,
                      "horas_con": 644.5, "pct": 95.9},
            "top_sin": [("Cerro", "C1", 24.0, 1)],
            "top_con": [("Playa", "P2", 168.0, "con servicio")],
            "grafico_deficit": grafico,
            "no_afectados": {"Playa": ["P2"]},
            "roturas": {
                "total": 1,
                "conteo": {("Cerro", "Otra avería"): 1},
                "filas": [{"fecha": datetime.fromisoformat(
                               "2026-09-06T15:00:00+00:00"),
                           "municipio": "Cerro", "tipo": "Otra avería",
                           "calles": "Calle Dos"}],
            },
            "distribucion": {"sin": 2, "con": 1, "asum": 1, "con_vecinos": 0,
                             "sin_vecinos": 0, "desconocido": 0},
            "senales": {"total": 0, "por_municipio": [], "sin_vecinos": 0,
                        "con_vecinos": 0},
            "mw": 800.0,
        }

    def test_html_titular_encima_y_img_por_cid(self):
        h = MOD.render_html(self.res_base(self.datos_grafico()), png=b"PNG")
        self.assertIn("Dónde se soporta el déficit de la capital", h)
        # Titular con la concentración (denominador correcto) y el N del top.
        self.assertIn("<strong>96%</strong>", h)
        self.assertIn("se concentró en solo <strong>10</strong> circuitos", h)
        # La imagen incrustada por cid, con el alt del spec.
        self.assertIn('<img src="cid:grafico-deficit" '
                      'alt="Distribución del déficit por circuito" '
                      'style="width:100%;max-width:1400px;height:auto;'
                      'border:0;">', h)
        # El titular queda ENCIMA de la imagen.
        self.assertLess(h.index("El <strong>96%</strong>"),
                        h.index("cid:grafico-deficit"))
        # Las barras CSS del bug anterior desaparecieron.
        self.assertNotIn("width:92%", h)
        self.assertNotIn("background:#f1f5f9;height:20px", h)
        self.assertNotIn("width:100%;\">&nbsp;</div>", h)

    def test_html_sin_matplotlib_nota_textual(self):
        h = MOD.render_html(self.res_base(self.datos_grafico()), png=None)
        self.assertNotIn("cid:grafico-deficit", h)
        self.assertIn("<strong>96%</strong>", h)  # el titular se mantiene
        self.assertIn("no pudo renderizarse como imagen", h)

    def test_texto_barras_ascii_top10(self):
        txt = MOD.render_texto(self.res_base(self.datos_grafico()))
        self.assertIn("DÓNDE SE SOPORTA EL DÉFICIT DE LA CAPITAL", txt)
        self.assertIn("El 96% de las horas sin corriente confirmadas de la "
                      "semana se concentró en solo 10 circuitos.", txt)
        # El bloque ASCII conserva el top 10 aunque `filas` traiga los 14.
        lineas = [l for l in txt.splitlines() if "█" in l]
        self.assertEqual(len(lineas), 10)
        self.assertIn("█" * 30, lineas[0])   # G00: 48 h → barra llena
        self.assertIn("█" * 8, lineas[9])    # G09: 12 h → 12·30/48 = 7.5 → 8
        self.assertIn("48.0 h", lineas[0])
        self.assertIn("12.0 h", lineas[9])
        self.assertIn("G00 · Playa", lineas[0])
        # Nota al pie.
        self.assertIn("el resto del sistema absorbe el resto del tiempo.", txt)

    def test_seccion_omitida_sin_cortes(self):
        h = MOD.render_html(self.res_base(None))
        self.assertNotIn("Dónde se soporta el déficit", h)
        self.assertNotIn("cid:grafico-deficit", h)
        txt = MOD.render_texto(self.res_base(None))
        self.assertNotIn("DÓNDE SE SOPORTA", txt)
        self.assertNotIn("█", txt)


if __name__ == "__main__":
    unittest.main()
