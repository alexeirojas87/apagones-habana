"""Camino del reporte de usuario BIDIRECCIONAL (review R4-002).

Dirección 1 (ya existía, NO se toca): UNE "con servicio" + vecinos reportan
sin corriente → discrepado («usuarios reportan sin corriente»).
Dirección 2 (nueva): circuito "sin servicio" + vecinos reportan que VOLVIÓ
(ultimo_con posterior a estado_fecha) → reportado_con en el catálogo y estado
con_vecinos («con servicio (según vecinos)») en las tres superficies JS y el
catálogo estático (build_seo).

Offline, stdlib, py3.9. Los clasificadores JS se verifican por asserts de
string + orden de ramas (discrepado GANA sobre con_vecinos en los clientes);
extraerlos a módulos puros testeables con node queda para R3-101.
"""

import importlib.util
import re
import sys
import unittest
from pathlib import Path

RAIZ = Path(__file__).parents[1]
SCRIPTS = RAIZ / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(RAIZ / "extractor"))


def _cargar(nombre, ruta):
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BC = _cargar("build_circuitos", SCRIPTS / "build_circuitos.py")
SEO = _cargar("build_seo", SCRIPTS / "build_seo.py")

APP_JS = (RAIZ / "web" / "app.js").read_text(encoding="utf-8")
WORKER_JS = (RAIZ / "web" / "_worker.js").read_text(encoding="utf-8")
CIRC_JS = (RAIZ / "web" / "circuitos.js").read_text(encoding="utf-8")
CSS = (RAIZ / "web" / "style.css").read_text(encoding="utf-8")

# Fixture A (dirección 2): la UNE lo declara caída en T1; los vecinos
# reportan que volvió DESPUÉS (T2 > T1). T3 (> T2) es un "se fue" posterior
# al "volvió" (flip-flop: otro vecino la reportó caída de nuevo).
T1 = "2026-07-02T16:10:00+00:00"
T2 = "2026-07-03T09:30:00+00:00"
T3 = "2026-07-03T14:00:00+00:00"
CU_VECINOS = {"desde": None, "ultima_sin": "2026-07-02T18:00:00+00:00",
              "ultimo_con": T2}


def circuito(**kw):
    base = {"codigo": "ZZ01", "municipio": "Playa", "municipios": ["Playa"],
            "calles": "Calle Prueba 1 entre 2 y 3", "causa": None,
            "estado": "sin servicio", "estado_fecha": T1,
            "veces": 1, "primera": T1}
    base.update(kw)
    return base


class TestBuilderReportadoCon(unittest.TestCase):
    """build_circuitos._reportado_con: espejo exacto del mecanismo discrepado
    (sin umbral de vecinos, sin ventana de recencia: la frescura la fija la
    comparación ultimo_con > estado_fecha sobre timestamps de los datos) MÁS
    el guard del veredicto vecinal VIGENTE: el ÚLTIMO veredicto manda — un
    "se fue" posterior al último "volvió" (`desde` > `ultimo_con`) deja el
    circuito "sin" aunque ultimo_con > estado_fecha."""

    def test_fixture_a_ultimo_con_posterior_a_la_caida(self):
        self.assertTrue(BC._reportado_con(circuito(), CU_VECINOS))

    def test_ultimo_con_anterior_a_la_caida_es_stale(self):
        # Reporte de "volvió" PREVIO a la caída declarada: no resucita.
        viejo = dict(CU_VECINOS, ultimo_con="2026-07-01T09:30:00+00:00")
        self.assertFalse(BC._reportado_con(circuito(), viejo))

    def test_ultimo_con_igual_a_la_caida_no_es_posterior(self):
        igual = dict(CU_VECINOS, ultimo_con=T1)
        self.assertFalse(BC._reportado_con(circuito(), igual))

    def test_flip_flop_se_fue_posterior_al_volvio_queda_sin(self):
        # Caída T1, "volvió" T2, "se fue" T3 (> T2): el ÚLTIMO veredicto
        # vecinal es "sin corriente" → reportado_con False (queda "sin"),
        # aunque ultimo_con > estado_fecha. El guard del veredicto vigente.
        flip = dict(CU_VECINOS, desde=T3)
        self.assertFalse(BC._reportado_con(circuito(), flip))

    def test_volvio_posterior_al_se_fue_vuelve_a_ser_veredicto_vigente(self):
        # "se fue" ANTERIOR al "volvió" T2: el veredicto vigente es "con"
        # de nuevo → reportado_con True (el `desde` viejo no lo tapa).
        vigente = dict(CU_VECINOS, desde="2026-07-02T18:00:00+00:00")
        self.assertTrue(BC._reportado_con(circuito(), vigente))

    def test_colision_estado_con_no_emite_reportado_con(self):
        # Estado "con servicio" + ultimo_con: ni reportado_con ni con_vecinos
        # (imposible en la práctica; discrepado manda si aplica).
        self.assertFalse(BC._reportado_con(circuito(estado="con servicio"), CU_VECINOS))

    def test_estado_none_y_sin_estado_fecha_no_emiten(self):
        self.assertFalse(BC._reportado_con(circuito(estado=None), CU_VECINOS))
        self.assertFalse(BC._reportado_con(circuito(estado_fecha=None), CU_VECINOS))

    def test_sin_registro_de_conteo(self):
        self.assertFalse(BC._reportado_con(circuito(), None))
        self.assertFalse(BC._reportado_con(circuito(), {}))
        self.assertFalse(BC._reportado_con(circuito(), {"ultimo_con": None}))

    def test_microsegundos_y_formato_del_pipeline(self):
        # El mismo pipeline usa '+00:00' con/sin microsegundos: el parseo gana.
        con_micro = dict(CU_VECINOS,
                         ultimo_con="2026-07-02T16:10:00.000001+00:00")
        self.assertTrue(BC._reportado_con(circuito(), con_micro))
        un_micro_antes = dict(CU_VECINOS,
                              ultimo_con="2026-07-02T16:09:59.999999+00:00")
        self.assertFalse(BC._reportado_con(circuito(), un_micro_antes))

    def test_ultimo_con_no_string_nunca_lanza(self):
        # R3-4: timestamps no-string (p. ej. epoch 1751537400 contra estado_fecha
        # ISO) NO abortan el build: la comparación completa degrada a False
        # (conservador: sin reportado_con) y el parseo ISO sigue siendo el camino
        # feliz (los tests de microsegundos de arriba lo cubren).
        self.assertFalse(BC._reportado_con(circuito(), {"ultimo_con": 1751537400}))

    def test_desde_no_string_nunca_lanza(self):
        # R3-REL-001: el guard del veredicto vigente TAMBIÉN está dentro del try
        # — un `desde` no-string (epoch) con ultimo_con ISO degrada a False sin
        # lanzar, igual que el caso simétrico de arriba.
        self.assertFalse(BC._reportado_con(
            circuito(), {"desde": 1751537400, "ultimo_con": "2026-07-03T09:30:00+00:00"}))
        # y el caso inverso (ultimo_con no-string con desde ISO): sin lanzar
        self.assertFalse(BC._reportado_con(
            circuito(), {"desde": "2026-07-03T09:30:00+00:00", "ultimo_con": 1751537400}))

    def test_mecanismo_discrepado_intacto(self):
        # Dirección 1: la fórmula original sigue byte a byte en el builder.
        fuente = (SCRIPTS / "build_circuitos.py").read_text(encoding="utf-8")
        self.assertIn(
            'c["discrepado"] = bool(cu.get("desde") and c.get("estado") in ("con servicio", None))',
            fuente)
        # y reportado_con vive JUNTO a discrepado en la fusión (no la sustituye).
        self.assertIn('c["reportado_con"] = _reportado_con(c, cu)', fuente)


class TestVigenciaSeo(unittest.TestCase):
    """build_seo._vigencia / _ESTADO_FILA / _GRUPO: con_vecinos entre sin y con."""

    def test_fixture_a_clasifica_con_vecinos(self):
        self.assertEqual(SEO._vigencia(circuite_con_vecinos(), None), "con_vecinos")

    def test_sin_servicio_sigue_siendo_sin(self):
        # Regla "apagado sigue apagado" intacta sin reporte vecinal.
        self.assertEqual(SEO._vigencia(circuite_con_vecinos(reportado_con=False), None), "sin")
        self.assertEqual(SEO._vigencia(circuito(), None), "sin")

    def test_estado_con_gana_sobre_reportado_con_residual(self):
        c = circuite_con_vecinos(estado="con servicio")
        self.assertEqual(SEO._vigencia(c, None), "con")

    def test_orden_y_etiqueta_del_grupo_nuevo(self):
        self.assertEqual(SEO._GRUPO,
                         {"sin": 0, "con_vecinos": 1, "con": 2, "asum": 3})
        self.assertEqual(SEO._ESTADO_FILA["con_vecinos"],
                         ("con-vec", "con servicio (según vecinos)"))
        self.assertLess(SEO._GRUPO["sin"], SEO._GRUPO["con_vecinos"])
        self.assertLess(SEO._GRUPO["con_vecinos"], SEO._GRUPO["con"])
        self.assertLess(SEO._GRUPO["con"], SEO._GRUPO["asum"])

    def test_con_vecinos_cuenta_como_con_en_el_estimado(self):
        # _estimado_afectados cuenta "sin" vía _vigencia: con_vecinos NO suma
        # como sin corriente (los vecinos dicen que volvió).
        estado = {"generado": "2026-07-03T15:10:00+00:00",
                  "poblacion_municipio": {"Playa": 1000}}
        circ = {"circuitos": [circuite_con_vecinos()]}
        self.assertEqual(SEO._estimado_afectados("Playa", estado, circ), 0)


def circuite_con_vecinos(**kw):
    base = circuito(reportado_con=True, conteo_usuario=dict(CU_VECINOS))
    base.update(kw)
    return base


class TestCatalogoSeoRenderiza(unittest.TestCase):
    """El catálogo estático publica «con servicio (según vecinos)» con la
    hora vecinal y NUNCA «lleva X sin corriente» para estos circuitos."""

    def _html(self, circuitos):
        estado = {"generado": "2026-07-03T15:10:00+00:00"}
        return SEO.catalogo_circuitos("Playa", estado, {"circuitos": circuitos})

    def test_fixture_a_renderiza_con_vecinos_con_hora(self):
        html = self._html([circuite_con_vecinos()])
        self.assertIn("con servicio (según vecinos)", html)
        self.assertIn('class="circ-est con-vec"', html)
        # ultimo_con 09:30 UTC -> 05:30 en La Habana (-4)
        self.assertIn("según vecinos desde 05:30", html)
        self.assertNotIn("lleva", html)
        self.assertNotIn("circ-dur\"> · lleva", html)

    def test_sin_registro_ultimo_con_va_sin_duracion(self):
        c = circuite_con_vecinos(conteo_usuario={"desde": None, "ultimo_con": None})
        html = self._html([c])
        self.assertIn("con servicio (según vecinos)", html)
        self.assertNotIn("según vecinos desde", html)
        self.assertNotIn("lleva", html)

    def test_fila_sin_normal_no_cambia(self):
        html = self._html([circuito()])
        self.assertIn("sin servicio", html)
        self.assertIn('class="circ-est sin"', html)
        self.assertIn("lleva", html)  # la duración del caído sigue creciendo


class TestParidadCifraSin(unittest.TestCase):
    """Paridad de la cifra «sin» entre las cuatro superficies con cifra
    (review R3): una sola definición de «sin servicio efectivo» (_sin_efectivos,
    que excluye reportado_con) alimenta conteo_municipio (tarjeta del hub),
    _circ_sin/instantanea_index (portada), ranking_poblacion y pagina_municipio
    (hija). Con 1 reportado_con + 1 caído real en el MISMO municipio, todas
    reportan la MISMA cuenta: 1 — el hub y la portada ya no contradicen a la
    hija."""

    ESTADO = {"generado": "2026-07-03T15:10:00+00:00"}

    def _catalogo(self):
        # Fixture de paridad: ZZ02 volvió según vecinos (reportado_con) y
        # ZZ01 sigue caído de verdad; ambos en Playa.
        return {"circuitos": [circuito(), circuite_con_vecinos(codigo="ZZ02")]}

    def test_sin_efectivos_es_la_definicion_unica(self):
        self.assertTrue(SEO._sin_efectivos(circuito()))
        self.assertFalse(SEO._sin_efectivos(circuite_con_vecinos()))
        self.assertFalse(SEO._sin_efectivos(circuito(estado="con servicio")))

    def test_hub_portada_hija_y_ranking_dan_la_misma_cifra(self):
        circ = self._catalogo()
        # Definición única: ZZ01 sin, ZZ02 excluido por reportado_con.
        self.assertEqual([c["codigo"] for c in SEO._circ_sin(circ)], ["ZZ01"])
        self.assertEqual(SEO.conteo_municipio("Playa", circ), (1, 2))
        # Hub: tarjeta real renderizada (region_hub), misma cifra que la hija.
        hub = SEO.region_hub(self.ESTADO, circ, ["Playa"])
        self.assertIn("1 <small>de 2 circuitos sin servicio</small>", hub)
        # Portada: instantánea «N de M» y lista por municipio, misma cifra.
        portada = SEO.instantanea_index(self.ESTADO, circ)
        self.assertIn("<b>1 de 2 circuitos</b>", portada)
        self.assertIn("1 circuito(s) sin corriente", portada)
        # Hija: párrafo de estado y UNA tarjeta en «Circuitos sin servicio
        # ahora» (la fila de ZZ01 en el catálogo completo no es una tarjeta).
        hija = SEO.pagina_municipio("Playa", self.ESTADO, circ, ["Playa"])
        self.assertIn("<b>1 de 2 circuitos</b> del municipio", hija)
        self.assertEqual(hija.count('<article class="circ">'), 1)
        # Ranking: ordena con la MISMA definición (1 sin → puesto 1 de 1).
        ranking = SEO.ranking_poblacion("Playa", self.ESTADO, circ, ["Playa"])
        self.assertIn("<b>1 de 1 municipios más afectados hoy</b>", ranking)

    def test_hub_sin_reporte_vecinal_no_cambia_de_cifra(self):
        # Sin reportado_con en el catálogo, el conteo es idéntico al de antes
        # del arreglo (regresión: la exclusión solo aplica a reportado_con).
        circ = {"circuitos": [circuito(), circuite_con_vecinos(codigo="ZZ02",
                                                               reportado_con=False)]}
        self.assertEqual(SEO.conteo_municipio("Playa", circ), (2, 2))
        self.assertEqual(len(SEO._circ_sin(circ)), 2)


class TestSuperficiesJS(unittest.TestCase):
    """Las 3 superficies JS clasifican con_vecinos DESPUÉS de discrepado
    (discrepado gana la colisión por orden de ramas, como hoy)."""

    RAMA = 'c.estado === "sin servicio" && c.reportado_con'

    def test_app_js_clasifica_y_despues_de_discrepado(self):
        self.assertIn(f'if ({self.RAMA}) return "con_vecinos";', APP_JS)
        self.assertLess(APP_JS.index('return "discrepado";'),
                        APP_JS.index('return "con_vecinos";'))

    def test_worker_js_clasifica_y_despues_de_discrepado(self):
        self.assertIn(f"if ({self.RAMA}) return \"con_vecinos\";", WORKER_JS)
        self.assertLess(WORKER_JS.index('return "discrepado";'),
                        WORKER_JS.index('return "con_vecinos";'))

    def test_circuitos_js_clasifica_y_despues_de_discrepado(self):
        self.assertIn(f"if ({self.RAMA})", CIRC_JS)
        self.assertIn('"con-vec", txt: "con servicio (según vecinos)"', CIRC_JS)
        self.assertLess(CIRC_JS.index('clase: "discrepado"'),
                        CIRC_JS.index('clase: "con-vec"'))

    def test_app_js_conteo_en_popup_y_resumen(self):
        self.assertIn("con_vecinos: 0", APP_JS)  # popupMunicipio
        self.assertIn("con servicio según vecinos", APP_JS)  # textos de resumen

    def test_worker_describe_el_estado_y_cuenta_aparte(self):
        self.assertIn('con_vecinos: "con servicio (según vecinos)"', WORKER_JS)
        self.assertIn("segun_vecinos_desde", WORKER_JS)
        self.assertIn("con_servicio_segun_vecinos: conteo.con_vecinos", WORKER_JS)
        self.assertIn('con_servicio_segun_vecinos: cuenta("con servicio (según vecinos)")',
                      WORKER_JS)

    def test_worker_prompt_menciona_ambos_sentidos(self):
        self.assertIn("AMBOS sentidos", WORKER_JS)
        self.assertIn('"con servicio (según vecinos)"', WORKER_JS)
        self.assertIn('"usuarios reportan sin corriente"', WORKER_JS)
        self.assertIn("no un dato oficial de la Empresa", WORKER_JS)

    def test_circuitos_js_duracion_y_encabezado(self):
        # Duración: "según vecinos desde HH:MM" con la hora habanera de
        # ultimo_con; sin registro, sin duración.
        self.assertIn('e.clase === "con-vec"', CIRC_JS)
        self.assertIn("toLocaleTimeString", CIRC_JS)
        self.assertIn("con servicio según vecinos", CIRC_JS)

    def test_css_con_tokens_del_sitio(self):
        for regla in (".circ-est.con-vec", ".circ-lleva.con-vec"):
            m = re.search(re.escape(regla) + r"\s*\{[^}]*\}", CSS)
            self.assertIsNotNone(m, f"falta {regla}")
            self.assertIn("var(--green-t)", m.group(0), regla)

    def test_no_fabrica_horas_ni_toca_mecanismos_prohibidos(self):
        # El estado con_vecinos no inventa horas UNE ni toca circuitos_horas:
        # app.js nunca clasifica con Date.now() en circuitoVigente.
        rama = APP_JS.index("function circuitoVigente")
        fin = APP_JS.index("function popupMunicipio")
        self.assertNotIn("Date.now()", APP_JS[rama:fin])


if __name__ == "__main__":
    unittest.main()
