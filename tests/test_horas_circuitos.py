"""Horas históricas por circuito: el acumulador vive en build_circuitos.py
(web/data/circuitos_horas.json) — replay COMPLETO del canal commiteado
(regex + caché LLM), no la ventana de ~7 días de partes.json — y su consumo
en las páginas de municipio (sección "Circuitos más afectados" + duración del
estado vigente en el catálogo, con web/horas.js re-ranqueando desde el JSON
embebido).

Offline, stdlib, py3.9; fixtures de tests/fixtures y tmp (nunca los JSON del
repo). HORA_CUBA es UTC-4 fijo, igual que el builder.
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).parents[1]


def _stub_si_falta(nombre, **atributos):
    """Interpone un módulo stub SOLO si el paquete real no está instalado.

    build_circuitos importa supabase (y extract.py, postgrest) a nivel de
    módulo; las pruebas son offline y `create_client` nunca se llama (el
    replay lee canal_cache commiteado). Con el paquete real instalado no se
    toca nada.
    """
    try:
        __import__(nombre)
    except ImportError:
        mod = types.ModuleType(nombre)
        for k, v in atributos.items():
            setattr(mod, k, v)
        sys.modules[nombre] = mod


_stub_si_falta("supabase", create_client=lambda *a, **k: None)
_stub_si_falta("postgrest", ReturnMethod=object)

RUTA_BC = RAIZ / "scripts" / "build_circuitos.py"
SPEC = importlib.util.spec_from_file_location("build_circuitos", RUTA_BC)
BC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BC)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_seo  # noqa: E402

MOD = test_seo.MOD
HC = BC.HORA_CUBA


def fh(dia, hora, minuto=0):
    """datetime local La Habana (UTC-4 fijo) del 2026-07."""
    return datetime(2026, 7, dia, hora, minuto, tzinfo=HC)


def gen(dia, hora, minuto=0):
    """`generado` como datetime local La Habana."""
    return fh(dia, hora, minuto)


class HorasHistoricasTest(unittest.TestCase):
    """Emparejado afectación→restablecimiento y reparto por día HORA_CUBA."""

    def _una(self, eventos, generado):
        return BC.horas_historicas({"AL53": eventos}, generado)["AL53"]

    def test_ciclo_simple_un_dia(self):
        r = self._una([(fh(5, 10), "sin"), (fh(5, 13, 30), "con")], gen(5, 15))
        self.assertEqual(r["total"], 3.5)
        self.assertEqual(r["por_dia"], {"2026-07-05": 3.5})

    def test_varios_ciclos_acumulan_y_sin_duplicado_no_cuenta(self):
        # dos ciclos el mismo día + un "sin" repetido dentro del segundo
        # intervalo: se acumulan 1.0 + 2.5 y el duplicado abre nada nuevo.
        eventos = [(fh(5, 8), "sin"), (fh(5, 9), "con"),
                   (fh(5, 12), "sin"), (fh(5, 12, 30), "sin"),
                   (fh(5, 14, 30), "con")]
        r = self._una(eventos, gen(5, 20))
        self.assertEqual(r["total"], 3.5)
        self.assertEqual(r["por_dia"], {"2026-07-05": 3.5})

    def test_cruce_de_medianoche_se_reparte(self):
        # 23:00 → 02:00 del día siguiente (HORA_CUBA): 1 h + 2 h.
        r = self._una([(fh(5, 23), "sin"), (fh(6, 2), "con")], gen(6, 8))
        self.assertEqual(r["total"], 3.0)
        self.assertEqual(r["por_dia"], {"2026-07-05": 1.0, "2026-07-06": 2.0})

    def test_intervalo_abierto_corre_hasta_generado(self):
        # 5.5 h < 48 h de umbral de confirmación: cuenta hasta generado, como hoy.
        r = self._una([(fh(5, 10), "sin")], gen(5, 15, 30))
        self.assertEqual(r["total"], 5.5)
        self.assertEqual(r["por_dia"], {"2026-07-05": 5.5})

    def test_intervalo_abierto_cruza_dias_hasta_generado(self):
        # 30 h < 48 h: el cap de confirmación no alcanza; reparto intacto.
        r = self._una([(fh(4, 22), "sin")], gen(6, 4))
        # 22:00 del 4 → medianoche (2 h) + 00:00 → 04:00 del 6 (28 h)
        self.assertEqual(r["por_dia"], {"2026-07-04": 2.0, "2026-07-05": 24.0,
                                        "2026-07-06": 4.0})
        self.assertEqual(r["total"], 30.0)

    def test_generado_ausente_no_cierra_nada(self):
        r = BC.horas_historicas({"AL53": [(fh(5, 10), "sin")]}, None)
        self.assertNotIn("AL53", r)  # sin cierre verificable: no se inventa

    def test_generado_anterior_a_la_apertura_no_aporta(self):
        r = BC.horas_historicas({"AL53": [(fh(5, 10), "sin")]}, gen(5, 8))
        self.assertNotIn("AL53", r)  # intervalo invertido contra el generado

    def test_dias_previos_ausentes_no_aparecen(self):
        r = self._una([(fh(5, 10), "sin"), (fh(5, 12), "con")], gen(9, 12))
        self.assertEqual(list(r["por_dia"]), ["2026-07-05"])  # ni 04 ni 06..09

    def test_circuito_sin_afectaciones_se_omite(self):
        r = BC.horas_historicas({"AL53": [(fh(5, 10), "con")]}, gen(5, 12))
        self.assertEqual(r, {})

    def test_naive_se_lee_como_utc(self):
        # 22:00 UTC = 18:00 local; 23:30 UTC = 19:30 local → 1.5 h ese día.
        sin = datetime(2026, 7, 5, 22, 0)   # naive
        con = datetime(2026, 7, 5, 23, 30)  # naive
        r = BC.horas_historicas({"AL53": [(sin, "sin"), (con, "con")]}, gen(5, 23))
        self.assertEqual(r["AL53"]["por_dia"], {"2026-07-05": 1.5})

    def test_eventos_desordenados_se_ordenan_por_fecha(self):
        # el replay anota en orden cronológico, pero el acumulador no confía:
        # un par fuera de orden se ordena y el emparejado sale igual.
        r = self._una([(fh(5, 13, 30), "con"), (fh(5, 10), "sin")], gen(5, 15))
        self.assertEqual(r["total"], 3.5)
        self.assertEqual(r["por_dia"], {"2026-07-05": 3.5})


class HorasConfirmadasTest(unittest.TestCase):
    """REGLA del mantenedor: las horas sin corriente son HORAS CONFIRMADAS
    (coherente con el estado desconocido/azul a 48 h). El tramo ABIERTO al
    horizonte cuenta 48 h desde su última mención: cada re-mención o señal
    vecinal reactiva otras 48 h DESDE ELLA y el hueco silencioso NO cuenta;
    el intervalo CERRADO con restablecimiento cuenta COMPLETO de punta a
    punta (la UNE confirmó ambos extremos), sin cap."""

    def _una(self, eventos, generado, senales=None):
        r = BC.horas_historicas({"AL53": eventos}, generado, senales)
        return r.get("AL53") or {"por_dia": {}, "total": 0.0}

    def test_abierto_100h_sin_remenciones_cuenta_48(self):
        # apertura fh(1,0); generado a las 100 h (fh(5,4)); sin re-menciones:
        # 48 h confirmadas, no 100 (fin efectivo = apertura + 48 h).
        r = self._una([(fh(1, 0), "sin")], fh(5, 4))
        self.assertEqual(r["total"], 48.0)
        self.assertEqual(r["por_dia"], {"2026-07-01": 24.0, "2026-07-02": 24.0})

    def test_abierto_30h_cuenta_30(self):
        # bajo el umbral: cuenta hasta generado, como hoy.
        r = self._una([(fh(5, 0), "sin")], fh(6, 6))
        self.assertEqual(r["total"], 30.0)
        self.assertEqual(r["por_dia"], {"2026-07-05": 24.0, "2026-07-06": 6.0})

    def test_abierto_con_remencion_a_70h(self):
        # apertura fh(1,0); re-mención a las 70 h (fh(3,22)); generado a las
        # 100 h (fh(5,4)): 48 h de la 1.ª mención + 30 h desde la re-mención;
        # el hueco 48→70 NO cuenta (el día 2026-07-03 queda sin horas).
        r = self._una([(fh(1, 0), "sin"), (fh(3, 22), "sin")], fh(5, 4))
        self.assertEqual(r["total"], 78.0)
        self.assertEqual(r["por_dia"], {"2026-07-01": 24.0, "2026-07-02": 24.0,
                                        "2026-07-03": 2.0, "2026-07-04": 24.0,
                                        "2026-07-05": 4.0})

    def test_cerrado_100h_con_restablecimiento_cuenta_completo(self):
        # la UNE confirmó ambos extremos: 100 h de punta a punta, SIN cap.
        r = self._una([(fh(1, 0), "sin"), (fh(5, 4), "con")], fh(5, 10))
        self.assertEqual(r["total"], 100.0)
        self.assertEqual(r["por_dia"], {"2026-07-01": 24.0, "2026-07-02": 24.0,
                                        "2026-07-03": 24.0, "2026-07-04": 24.0,
                                        "2026-07-05": 4.0})

    def test_cerrado_con_remenciones_intermedias_cuenta_completo(self):
        # re-menciones dentro de un tramo que se cierra: no tocan nada, el
        # episodio completo quedó confirmado por el restablecimiento final.
        r = self._una([(fh(1, 0), "sin"), (fh(2, 0), "sin"), (fh(3, 0), "sin"),
                       (fh(5, 0), "con")], fh(5, 10))
        self.assertEqual(r["total"], 96.0)
        self.assertEqual(r["por_dia"], {"2026-07-01": 24.0, "2026-07-02": 24.0,
                                        "2026-07-03": 24.0, "2026-07-04": 24.0})

    def test_senal_vecinal_extiende_las_48h(self):
        # apertura fh(1,0); señal vecinal a las 80 h (fh(4,8)); generado a las
        # 100 h: 48 h de la apertura + 20 h desde la señal; el hueco 48→80 NO
        # cuenta (el día 2026-07-03 queda sin horas).
        sen = {"AL53": [fh(4, 8)]}
        r = self._una([(fh(1, 0), "sin")], fh(5, 4), sen)
        self.assertEqual(r["total"], 68.0)
        self.assertEqual(r["por_dia"], {"2026-07-01": 24.0, "2026-07-02": 24.0,
                                        "2026-07-04": 16.0, "2026-07-05": 4.0})

    def test_senal_no_abre_intervalo_propio(self):
        # la señal solo extiende el reloj del tramo abierto de la UNE: con el
        # tramo CERRADO, la señal de después no añade ni inventa nada.
        sen = {"AL53": [fh(4, 8)]}
        r = self._una([(fh(1, 0), "sin"), (fh(2, 0), "con")], fh(5, 4), sen)
        self.assertEqual(r["total"], 24.0)  # cerrado: completo, señal fuera

    def test_senal_anterior_al_tramo_no_cuenta(self):
        # señal de un episodio anterior a la apertura: no menciona este tramo.
        sen = {"AL53": [fh(1, 0) - timedelta(hours=4)]}
        r = self._una([(fh(1, 0), "sin")], fh(5, 4), sen)
        self.assertEqual(r["total"], 48.0)

    def test_menciones_solapadas_no_duplican(self):
        # re-mención a las 10 h y señal a las 20 h, ambas DENTRO de la ventana
        # de 48 h de la apertura: el tramo cuenta una sola vez hasta 68 h.
        sen = {"AL53": [fh(1, 20)]}
        r = self._una([(fh(1, 0), "sin"), (fh(1, 10), "sin")], fh(5, 4), sen)
        self.assertEqual(r["total"], 68.0)
        self.assertEqual(r["por_dia"], {"2026-07-01": 24.0, "2026-07-02": 24.0,
                                        "2026-07-03": 20.0})

    def test_senal_naive_se_lee_como_utc(self):
        # convención del builder: ISO naive del conteo_usuario = UTC
        # (04 08:00Z = 04:00 local; 48 h de la apertura + 24 h de la señal).
        sen = {"AL53": [datetime(2026, 7, 4, 8, 0)]}
        r = self._una([(fh(1, 0), "sin")], fh(5, 4), sen)
        self.assertEqual(r["total"], 72.0)


class RedondeoHorasTest(unittest.TestCase):
    """1 decimal POR CUBO; cubos 0.0 y circuitos sin horas publicables fuera."""

    def test_redondea_por_cubo_y_descarta_ceros(self):
        historico = {"AL53": {"por_dia": {"2026-07-05": 2.34999, "2026-07-06": 0.04},
                              "total": 2.38999}}
        r = BC.redondear_horas(historico)
        self.assertEqual(r["por_dia"]["AL53"], {"2026-07-05": 2.3})
        self.assertEqual(r["total"]["AL53"], 2.4)

    def test_circuito_sin_horas_publicables_se_omite(self):
        r = BC.redondear_horas({"AL53": {"por_dia": {"2026-07-05": 0.01},
                                         "total": 0.01}})
        self.assertEqual(r, {"total": {}, "por_dia": {}})

    def test_codigos_ordenados_para_diffs_estables(self):
        r = BC.redondear_horas({
            "ZZ1": {"por_dia": {"2026-07-05": 1.0}, "total": 1.0},
            "AA9": {"por_dia": {"2026-07-05": 2.0}, "total": 2.0},
        })
        self.assertEqual(list(r["total"]), ["AA9", "ZZ1"])


class ReplayCanalHorasTest(unittest.TestCase):
    """El replay del canal alimenta el histórico de horas por el MISMO camino
    que el estado autoritativo del catálogo: regex del parte Y caché LLM.
    Fixtures de mensajes de canal (la forma de canal_cache.json); sin red."""

    def _correr(self, filas, llm_cache=None):
        return BC.replay_canal(filas, {}, set(), llm_cache or {})

    def test_afectacion_abre_y_restablecimiento_cierra(self):
        filas = [
            {"message_id": 1, "fecha": "2026-07-05T14:00:00+00:00",
             "texto": "🔻 Afectación\n👉 AL53: Zona 24, Edf 3"},
            {"message_id": 2, "fecha": "2026-07-05T17:30:00+00:00",
             "texto": "✅ Restablecimiento\n👉AL53: Zona 24, Edf 3"},
        ]
        cat, ev = self._correr(filas)
        self.assertEqual(cat["AL53"]["estado"], "con servicio")
        r = BC.horas_historicas(ev, fh(5, 23))
        # 14:00Z = 10:00 local, 17:30Z = 13:30 local → 3.5 h
        self.assertEqual(r["AL53"]["total"], 3.5)
        self.assertEqual(r["AL53"]["por_dia"], {"2026-07-05": 3.5})

    def test_mensaje_sin_estado_no_anota_nada(self):
        # "Parte del SEN" y etiquetas sin señal de estado: el circuito se
        # registra (veces) pero NINGÚN intervalo de horas se abre.
        filas = [{"message_id": 3, "fecha": "2026-07-05T14:00:00+00:00",
                  "texto": "⚡ Parte del SEN\n👉 AL53: nota informativa"}]
        cat, ev = self._correr(filas)
        self.assertIn("AL53", cat)
        self.assertIsNone(cat["AL53"]["estado"])
        self.assertEqual(ev, {})

    def test_camino_llm_cambia_estado_y_anota_horas(self):
        # Sin viñeta 👉 y sin palabra de estado para el regex: SOLO el caché
        # LLM puede saber que AL53 se afectó — el camino que mantiene vivo el
        # histórico de AL53 (sus menciones viven en partes_llm.json).
        filas = [{"message_id": 10, "fecha": "2026-07-05T14:00:00+00:00",
                  "texto": "Reportan daños en el reparto Zona 24, Habana del Este"}]
        llm = {"10": {"via": "llm", "validador_version": 2,
                      "circuitos": [{"codigos": ["AL53"],
                                     "codigos_estado": ["AL53"],
                                     "estado": "sin servicio",
                                     "calles": "Zona 24, Edf 3"}]}}
        cat, ev = self._correr(filas, llm)
        self.assertEqual(cat["AL53"]["estado"], "sin servicio")
        r = BC.horas_historicas(ev, fh(5, 16))
        self.assertEqual(r["AL53"]["total"], 6.0)  # abierto hasta el generado
        self.assertEqual(r["AL53"]["por_dia"], {"2026-07-05": 6.0})

    def test_llm_viejo_sin_evidencia_no_anota(self):
        # caché anterior a v2 (sin codigos_estado): no toca estado ni horas.
        filas = [{"message_id": 11, "fecha": "2026-07-05T14:00:00+00:00",
                  "texto": "Reportan daños en el reparto Zona 24, Habana del Este"}]
        llm = {"11": {"via": "llm", "validador_version": 1,
                      "circuitos": [{"codigos": ["AL53"],
                                     "estado": "sin servicio"}]}}
        cat, ev = self._correr(filas, llm)
        self.assertNotIn("AL53", cat)
        self.assertEqual(ev, {})

    def test_deficit_registra_sin_servicio(self):
        # "Actualización de afectaciones: AL53 - 5 horas" → estado sin
        # servicio sin calles; el intervalo abre con ese mismo timestamp.
        filas = [{"message_id": 12, "fecha": "2026-07-05T14:00:00+00:00",
                  "texto": "⚡ Actualización de afectaciones\nAL53 - 5 horas"}]
        cat, ev = self._correr(filas)
        self.assertEqual(cat["AL53"]["estado"], "sin servicio")
        r = BC.horas_historicas(ev, fh(5, 15))
        self.assertEqual(r["AL53"]["total"], 5.0)  # 10:00 → 15:00 local

    def test_replay_vacio(self):
        self.assertEqual(self._correr([]), ({}, {}))

    def test_idempotente_mismo_replay_mismas_horas(self):
        filas = [
            {"message_id": 1, "fecha": "2026-07-05T14:00:00+00:00",
             "texto": "🔻 Afectación\n👉 AL53: Zona 24, Edf 3"},
            {"message_id": 2, "fecha": "2026-07-05T17:30:00+00:00",
             "texto": "✅ Restablecimiento\n👉AL53: Zona 24, Edf 3"},
        ]
        _, ev1 = self._correr(filas)
        _, ev2 = self._correr(filas)  # sin estado persistente: de cero otra vez
        self.assertEqual(ev1, ev2)


class GeneradoHorasTest(unittest.TestCase):
    """Horizonte del histórico: `generado` de web/data/estado.json (estado.py
    corre antes en CI); respaldo, el último mensaje del canal. NUNCA el reloj
    de la corrida."""

    def test_lee_estado_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = os.path.join(tmp, "estado.json")
            with open(ruta, "w", encoding="utf-8") as f:
                json.dump({"generado": "2026-07-06T04:00:00+00:00"}, f)
            with mock.patch.object(BC, "ESTADO_FILE", ruta):
                self.assertEqual(BC._generado_horas([]).isoformat(),
                                 "2026-07-06T04:00:00+00:00")

    def test_fallback_ultimo_mensaje_del_canal(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = os.path.join(tmp, "no-existe.json")
            filas = [{"message_id": 1, "fecha": "2026-07-05T14:00:00+00:00"},
                     {"message_id": 2, "fecha": "2026-09-15T16:01:08+00:00"}]
            with mock.patch.object(BC, "ESTADO_FILE", ruta):
                self.assertEqual(BC._generado_horas(filas).isoformat(),
                                 "2026-09-15T16:01:08+00:00")

    def test_nada_de_nada_devuelve_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = os.path.join(tmp, "no-existe.json")
            with mock.patch.object(BC, "ESTADO_FILE", ruta):
                self.assertIsNone(BC._generado_horas([]))


class FormatoHorasTest(unittest.TestCase):
    """'X h' (1 decimal) bajo 48 h; 'X d Y h' a partir de ahí. Misma regla
    que web/horas.js."""

    def test_horas_con_un_decimal(self):
        self.assertEqual(MOD._formato_horas(5.3), "5.3 h")
        self.assertEqual(MOD._formato_horas(0), "0.0 h")
        self.assertEqual(MOD._formato_horas(47.9), "47.9 h")

    def test_dias_y_horas_desde_48(self):
        self.assertEqual(MOD._formato_horas(48.0), "2 d 0 h")
        self.assertEqual(MOD._formato_horas(52.3), "2 d 4 h")
        self.assertEqual(MOD._formato_horas(71.96), "3 d 0 h")  # 23.96 → 24 sube
        self.assertEqual(MOD._formato_horas(213.4), "8 d 21 h")

    def test_valores_invalidos_vacios(self):
        self.assertEqual(MOD._formato_horas(None), "")
        self.assertEqual(MOD._formato_horas(-1), "")

    def test_duracion_horas_entre_isos(self):
        self.assertEqual(
            MOD._duracion_horas("2026-07-03T13:10:00+00:00",
                                "2026-07-03T15:10:50+00:00"), "2.0 h")
        self.assertEqual(
            MOD._duracion_horas("2026-07-01T10:00:00+00:00",
                                "2026-07-03T15:10:50+00:00"), "2 d 5 h")

    def test_duracion_horas_fechas_malas_o_invertidas(self):
        self.assertEqual(MOD._duracion_horas(None, "2026-07-03T15:10:50+00:00"), "")
        self.assertEqual(MOD._duracion_horas("basura", "2026-07-03T15:10:50+00:00"), "")
        self.assertEqual(MOD._duracion_horas("2026-07-03T15:10:50+00:00", ""), "")
        self.assertEqual(MOD._duracion_horas("2026-07-03T16:00:00+00:00",
                                             "2026-07-03T15:10:50+00:00"), "")


# Histórico de horas para el árbol de pruebas: solo circuitos de Playa (Regla
# queda fuera a propósito para probar el recorte por municipio). Sumas
# consistentes con por_dia; el orden esperado del ranking es por horas desc.
HORAS_FIXTURE = {
    "generado": "2026-07-03T15:10:50+00:00",
    "total": {"B789": 213.4, "B123": 190.2, "A1443": 88.8, "L315": 5.0,
              "B246": 2.0},
    "por_dia": {
        "B789": {"2026-06-20": 120.0, "2026-07-01": 60.4, "2026-07-03": 33.0},
        "B123": {"2026-06-10": 90.0, "2026-07-02": 100.2},
        "A1443": {"2026-07-03": 88.8},
        "L315": {"2026-06-01": 5.0},
        "B246": {"2026-07-03": 2.0},
    },
}


class SeccionAfectadosTest(test_seo.BaseArbol):
    """La sección "Circuitos más afectados" de la página de municipio."""

    def setUp(self):
        test_seo.BaseArbol.setUp(self)
        with open(os.path.join(self.web, "data", "circuitos_horas.json"),
                  "w", encoding="utf-8") as f:
            json.dump(HORAS_FIXTURE, f)
        self.correr()

    def pagina(self, nombre):
        return self._leer("municipio", MOD.slug(nombre), "index.html")

    def _ol(self, pagina):
        m = re.search(r'<ol id="afect-ranking" class="afect-ranking">(.*?)</ol>',
                      pagina, re.DOTALL)
        return None if m is None else m.group(1)

    def _codigos(self, pagina):
        ol = self._ol(pagina)
        return None if ol is None else re.findall(r'href="/circuitos\?c=([^"]+)"', ol)

    def test_seccion_con_titulo_stamp_y_pildoras(self):
        p = self.pagina("Playa")
        self.assertIn("<h2>Circuitos más afectados</h2>", p)
        self.assertIn("Histórico por horas sin corriente", p)
        for rango in ("7", "30", "90", "todo"):
            self.assertIn('data-rango="%s"' % rango, p)
        self.assertIn('class="afect-rango activo" data-rango="todo" '
                      'aria-pressed="true">Todo</button>', p)
        self.assertIn('<script src="/horas.js" defer></script>', p)
        # Bug B (selector roto): el <ol> lleva id + clase — web/horas.js
        # necesita el id (y de respaldo la clase) para colgar los listeners.
        self.assertIn('<ol id="afect-ranking" class="afect-ranking">', p)

    def test_default_server_rendered_ordena_por_horas_desc(self):
        # Todo/histórico (SEO-safe): horas desc; el desempate por veces no
        # hace falta aquí (no hay empates), pero B789 (15 partes) va primero.
        self.assertEqual(self._codigos(self.pagina("Playa")),
                         ["B789", "B123", "A1443", "L315", "B246"])
        ol = self._ol(self.pagina("Playa"))
        # >= 48 h → "X d Y h"; bajo 48 → "X h" con 1 decimal
        self.assertIn("8 d 21 h sin corriente", ol)     # B789: 213.4 h
        self.assertIn("7 d 22 h sin corriente", ol)     # B123: 190.2 h
        self.assertIn("3 d 17 h sin corriente", ol)     # A1443: 88.8 h
        self.assertIn("5.0 h sin corriente", ol)        # L315
        self.assertIn("2.0 h sin corriente", ol)        # B246
        self.assertIn("15 partes", ol)
        self.assertIn("1 parte", ol)  # B246: singular

    def test_json_embebido_parsea_y_recorta_al_municipio(self):
        p = self.pagina("Playa")
        m = re.search(r'<script type="application/json" '
                      r'id="datos-horas-circuitos">(.*?)</script>', p, re.DOTALL)
        self.assertIsNotNone(m, "falta el JSON embebido")
        datos = json.loads(m.group(1))  # el escape de guion_ld parsea igual
        self.assertEqual(set(datos), {"por_dia", "total", "veces", "generado"})
        self.assertEqual(set(datos["por_dia"]),
                         {"B789", "B123", "A1443", "L315", "B246"})
        self.assertEqual(datos["veces"]["B789"], 15)  # `veces` del catálogo
        self.assertEqual(datos["generado"], "2026-07-03T15:10:50+00:00")
        # Regla (H341 catalogado, sin horas en el fixture): recorte correcto
        self.assertIn("Sin datos históricos de horas todavía.",
                      self.pagina("Regla"))

    def test_municipio_sin_circuitos_estado_vacio_explícito(self):
        p = self.pagina("Boyeros")
        self.assertIn("<h2>Circuitos más afectados</h2>", p)
        self.assertIn("Sin datos históricos de horas todavía.", p)
        self.assertNotIn('id="datos-horas-circuitos"', p)
        self.assertNotIn("/horas.js", p)

    def test_dos_corridas_son_byte_identicas(self):
        primera = self.pagina("Playa")
        self.correr()
        self.assertEqual(primera, self.pagina("Playa"))


class SinDatosHorasTest(test_seo.BaseArbol):
    """Sin web/data/circuitos_horas.json: todas las hijas degradan al estado
    vacío explícito, sin romper la corrida (build best-effort)."""

    def setUp(self):
        test_seo.BaseArbol.setUp(self)
        self.correr()

    def test_playa_sin_archivo_muestra_estado_vacio(self):
        p = self._leer("municipio", "playa", "index.html")
        self.assertIn("Sin datos históricos de horas todavía.", p)
        self.assertNotIn('id="datos-horas-circuitos"', p)
        self.assertNotIn("/horas.js", p)

    def test_archivo_invalido_no_rompe_la_corrida(self):
        with open(os.path.join(self.web, "data", "circuitos_horas.json"),
                  "w", encoding="utf-8") as f:
            f.write("{no-es-json")
        self.correr()  # no lanza
        self.assertIn("Sin datos históricos de horas todavía.",
                      self._leer("municipio", "playa", "index.html"))


class CatalogoDuracionRenderTest(test_seo.BaseArbol):
    """El catálogo ya no imprime la hora cruda; muestra la duración del
    estado vigente (sin corriente / con corriente), nada en asum. Con la
    regla nueva del mantenedor la duración del caído crece SIN TOPE: un
    apagado silencioso de 30 h o de 51 h sigue contando."""

    def setUp(self):
        test_seo.BaseArbol.setUp(self)
        self.correr()

    def pagina(self, nombre):
        return self._leer("municipio", MOD.slug(nombre), "index.html")

    def test_catalogo_sin_hora_cruda_y_con_duracion(self):
        p = self.pagina("Playa")
        m = re.search(r'<ul class="circ-filas">(.*?)</ul>', p, re.DOTALL)
        self.assertIsNotNone(m)
        catalogo = m.group(1)
        self.assertNotIn("desde 09:10", catalogo)
        self.assertNotIn("(La Habana)", catalogo)
        self.assertIn("lleva 2.0 h sin corriente", catalogo)   # B246
        self.assertIn("19.2 h con corriente", catalogo)        # L315
        # Apagados silenciosos (regla nueva: permanecen "sin", duración sin tope):
        self.assertIn("lleva 30.0 h sin corriente", catalogo)  # B123, 30 h
        self.assertIn("lleva 2 d 3 h sin corriente", catalogo)  # B456, 51 h
        # asumido (estado None): sin duración (no inventar)
        self.assertNotIn("lleva", self.pagina("Marianao").split("circ-filas")[1])


class HorasJsTest(unittest.TestCase):
    """Contrato estático de web/horas.js: determinista y sin dependencias."""

    @classmethod
    def setUpClass(cls):
        cls.js = (RAIZ / "web" / "horas.js").read_text(encoding="utf-8")

    def test_usa_generado_y_nunca_el_reloj(self):
        self.assertIn("datos.generado", self.js)
        self.assertNotIn("Date.now", self.js)
        self.assertNotIn("new Date()", self.js)  # solo new Date(expresión)

    def test_lee_el_json_embebido_y_re_ranquea(self):
        self.assertIn('getElementById("datos-horas-circuitos")', self.js)
        self.assertIn('getElementById("afect-ranking")', self.js)
        self.assertIn('querySelectorAll(".afect-rango")', self.js)
        self.assertIn("/circuitos?c=", self.js)
        self.assertIn("Sin datos históricos de horas todavía.", self.js)

    def test_formato_igual_al_server(self):
        self.assertIn("toFixed(1)", self.js)   # "5.3 h"
        self.assertIn('" d "', self.js)        # "2 d 4 h"
        self.assertIn('" h"', self.js)

    def test_parte_dom_encapsulada_y_exports(self):
        # R3-002: lo puro es exportable para node; la parte DOM vive bajo la
        # guarda de documento; el selector tiene defensa doble (id + clase).
        self.assertIn("module.exports", self.js)
        self.assertIn('typeof document === "undefined"', self.js)
        self.assertIn('document.getElementById("afect-ranking") ||', self.js)
        self.assertIn('document.querySelector("ol.afect-ranking")', self.js)


# Fixture sintético con días conocidos (el doc permite usar el JSON de
# web/municipio/playa/index.html si existe, pero ese HTML es un artefacto
# generado que puede no estar en un árbol fresco; el sintético es
# determinista). Generado 2026-09-17T12:00Z → día Habana 2026-09-17.
# Ventana 7 días (desde 2026-09-11): 09-10 queda FUERA, 09-11 (borde) cuenta.
DATOS_HORAS_JS = {
    "generado": "2026-09-17T12:00:00+00:00",
    "total": {"XX1": 37.0, "BB1": 10.0, "BB9": 10.0, "CC9": 10.0, "AA2": 10.0},
    "veces": {"XX1": 5, "BB1": 3, "BB9": 3, "CC9": 3, "AA2": 1},
    "por_dia": {
        "XX1": {"2026-06-19": 4.0, "2026-06-21": 1.0, "2026-08-01": 9.0,
                "2026-08-20": 2.0, "2026-09-10": 6.0, "2026-09-11": 7.0,
                "2026-09-15": 5.0, "2026-09-17": 3.0},
        "BB1": {"2026-09-17": 10.0},
        "BB9": {"2026-09-17": 10.0},
        "CC9": {"2026-09-17": 10.0},
        "AA2": {"2026-09-17": 10.0},
    },
}


class HorasJsEjecutableTest(unittest.TestCase):
    """R3-002: web/horas.js EXECUTABLE con node. Las funciones puras quedan
    exportadas por module.exports (sin DOM), así que un script tmp puede
    require()-el archivo real y afirmar el contrato completo: restarDias,
    ventana 7/30/90 con el borde incluido, paridad de formatoHoras con
    _formato_horas (build_seo.py) y el orden del ranking."""

    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")

    def _correr(self, cuerpo):
        """Ejecuta un script tmp que requiere el web/horas.js REAL con node."""
        if self.node is None:
            self.skipTest("node no disponible")
        prelude = ("var assert = require('assert');\n"
                   "var h = require(%s);\n"
                   % json.dumps(str(RAIZ / "web" / "horas.js")))
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(prelude + cuerpo)
            ruta = f.name
        try:
            r = subprocess.run([self.node, ruta], capture_output=True,
                               text=True, timeout=60)
        finally:
            os.unlink(ruta)
        self.assertEqual(r.returncode, 0,
                         "node falló\nstdout: %s\nstderr: %s"
                         % (r.stdout, r.stderr))
        return r.stdout

    def test_contrato_completo_de_las_funciones_puras(self):
        datos = json.dumps(DATOS_HORAS_JS)
        self._correr("""
var datos = %s;
// restarDias: el caso del doc
assert.strictEqual(h.restarDias('2026-09-17', 7), '2026-09-10');
assert.strictEqual(h.restarDias('2026-09-17', 89), '2026-06-20');
// diaHabana: UTC-4 fijo (01:30Z del 18 aún es el 17 en La Habana)
assert.strictEqual(h.diaHabana('2026-09-18T01:30:00+00:00'), '2026-09-17');
assert.strictEqual(h.diaHabana('basura'), null);
// formatoHoras: paridad con _formato_horas (build_seo.py)
assert.strictEqual(h.formatoHoras(5.3), '5.3 h');
assert.strictEqual(h.formatoHoras(47.9), '47.9 h');
assert.strictEqual(h.formatoHoras(48.0), '2 d 0 h');
assert.strictEqual(h.formatoHoras(71.5), '3 d 0 h');
// horasEnRango: suma EXACTA de los buckets del rango, borde incluido
// (el día del generado cuenta; el día anterior al desde, no)
assert.strictEqual(h.horasEnRango(datos, 'XX1', 7), 15.0);    // 09-11..09-17
assert.strictEqual(h.horasEnRango(datos, 'XX1', 30), 23.0);   // + 08-20
assert.strictEqual(h.horasEnRango(datos, 'XX1', 90), 33.0);   // + 08-01, 06-21
assert.strictEqual(h.horasEnRango(datos, 'XX1', null), 37.0); // todo
assert.strictEqual(h.horasEnRango(datos, 'XX1', 1), 3.0);     // solo el generado
// ranking: horas desc, veces desc, código asc
assert.deepStrictEqual(h.ranquear(datos, 'todo').map(function (p) { return p[0]; }),
                       ['XX1', 'BB1', 'BB9', 'CC9', 'AA2']);
// con rango 1 el empate de 10.0 manda y XX1 (3.0) cae al final
assert.deepStrictEqual(h.ranquear(datos, '1').map(function (p) { return p[0]; }),
                       ['BB1', 'BB9', 'CC9', 'AA2', 'XX1']);
""" % datos)

    def test_json_embebido_de_pagina_real_si_existe(self):
        # Fixture preferido del doc: el JSON embebido de una página real de
        # municipio (artefacto de build_seo; si no está en el árbol, skip).
        # El artefacto puede ser STALE (de un build anterior): solo se usa su
        # JSON como datos de prueba; el contrato del id="afect-ranking" lo
        # cubren los tests de fixtures sobre salida fresca.
        pagina = RAIZ / "web" / "municipio" / "playa" / "index.html"
        if not pagina.exists():
            self.skipTest("web/municipio/playa/index.html no generado en este árbol")
        texto = pagina.read_text(encoding="utf-8")
        m = re.search(r'<script type="application/json" '
                      r'id="datos-horas-circuitos">(.*?)</script>', texto, re.DOTALL)
        if m is None:
            self.skipTest("la página real no trae JSON embebido (sin histórico de horas)")
        datos = json.dumps(json.loads(m.group(1)))
        self._correr("""
var datos = %s;
assert.ok(datos.por_dia && datos.generado, 'JSON real incompleto');
var dia = h.diaHabana(datos.generado);
assert.ok(/^\\d{4}-\\d{2}-\\d{2}$/.test(dia), 'diaHabana del generado real');
// el ranking completo es total desc (con desempate estable) y el primero
// coincide con su total exacto
var ranking = h.ranquear(datos, 'todo');
assert.ok(ranking.length > 0, 'el histórico real debe tener horas');
var primero = ranking[0];
assert.strictEqual(primero[1], datos.total[primero[0]]);
for (var i = 1; i < ranking.length; i++) {
  assert.ok(ranking[i - 1][1] >= ranking[i][1], 'horas no ascendentes');
}
""" % datos)


if __name__ == "__main__":
    unittest.main()
