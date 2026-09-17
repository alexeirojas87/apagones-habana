"""Estado "DESCONOCIDO" a las 48 h de silencio total + AZUL a la semana.

Regla del mantenedor: un circuito RECURRENTE (veces >= 3, misma convención
que aprende_circuitos MIN_POSTS=3) del que NO hay NINGUNA noticia —ni parte
de la UNE que lo mencione (`ultima`), ni reporte/comentario de usuario
(desde/ultima_sin/ultimo_con/ultimo_reset del conteo_usuario fusionado)—
decae por silencio total, SIN y CON servicio POR IGUAL (el mantenedor lo
reafirmó: "si pasan 48 horas de un circuito con servicio sin noticias se
pone desconocido también"; cada parte nuevo que no lo liste solo confirma
mientras haya noticias):
  1. silencio > 48 h (_UMBRAL_DESC_H) → "desconocido": el sitio deja de
     afirmar (ni sin ni con corriente; apagado sigue apagado SOLO las
     primeras 48 h);
  2. silencio > 48 h + 7 días = 216 h (_UMBRAL_AZUL_H) → "asum" (AZUL):
     vuelve al grupo «sin apagones reportados» hasta que una noticia nueva
     (parte que lo mencione o reporte de usuario) resetee el reloj.
Captura también al con_vecinos con veredicto envejecido. Los azules de
pocas menciones (veces < 3) NUNCA decaen. Durante evento_nacional no hay
decaimiento alguno (ni desc ni azul). Las horas históricas
(circuitos_horas.json) no se tocan: solo cambia el ESTADO.

Determinismo: el reloj es SIEMPRE estado.generado — nunca Date.now() ni el
reloj de la corrida. Offline, stdlib, py3.9: los clasificadores JS se
verifican por asserts de string + orden de ramas (convención del repo).
"""

import importlib.util
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).parents[1]
SCRIPTS = RAIZ / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _cargar(nombre, ruta):
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SEO = _cargar("build_seo", SCRIPTS / "build_seo.py")

APP_JS = (RAIZ / "web" / "app.js").read_text(encoding="utf-8")
WORKER_JS = (RAIZ / "web" / "_worker.js").read_text(encoding="utf-8")
CIRC_JS = (RAIZ / "web" / "circuitos.js").read_text(encoding="utf-8")
CSS = (RAIZ / "web" / "style.css").read_text(encoding="utf-8")

# Reloj del builder: estado.generado y señales de última noticia a ambos
# lados de cada umbral: 47/50 h (desc), 100 h (entre umbrales) y
# 220 h = 48 h + 7 d + 1 (azul).
GENERADO = "2026-07-03T15:10:00+00:00"
HACE_50H = "2026-07-01T13:10:00+00:00"
HACE_47H = "2026-07-01T16:10:00+00:00"
HACE_100H = "2026-06-29T11:10:00+00:00"
HACE_220H = "2026-06-24T11:10:00+00:00"
HACE_10H = "2026-07-03T05:10:00+00:00"


def _gen():
    return datetime.fromisoformat(GENERADO)


def circuito(**kw):
    """Recurrente caído hace 50 h (estado_fecha = ultima = HACE_50H), sin
    ninguna señal de usuario: el caso base que CAE en desconocido."""
    base = {"codigo": "ZZ01", "municipio": "Playa", "municipios": ["Playa"],
            "calles": "Calle Prueba 1 entre 2 y 3", "causa": None,
            "estado": "sin servicio", "estado_fecha": HACE_50H,
            "veces": 5, "primera": HACE_50H, "ultima": HACE_50H,
            "reportado_con": False}
    base.update(kw)
    return base


class TestVigenciaDesconocido(unittest.TestCase):
    """build_seo._vigencia: la rama de 48 h y sus exclusiones."""

    def test_recurrente_50h_decae_a_desconocido(self):
        self.assertEqual(SEO._vigencia(circuito(), _gen()), "desconocido")

    def test_47h_sigue_sin_con_duracion(self):
        # Misma señal a 47 h: NO cruza el umbral (estrictamente mayor que 48).
        c = circuito(ultima=HACE_47H, estado_fecha=HACE_47H)
        self.assertEqual(SEO._vigencia(c, _gen()), "sin")
        # y la fila del catálogo sigue mostrando la duración del apagado.
        html = SEO.catalogo_circuitos(
            "Playa", {"generado": GENERADO}, {"circuitos": [c]})
        self.assertIn("lleva", html)
        self.assertNotIn("estado desconocido", html)

    def test_justo_48h_no_cae(self):
        # Umbral EXACTO: > 48 h es estricto; a 48.0 h en punto sigue "sin".
        justo = (datetime.fromisoformat(GENERADO)
                 - timedelta(hours=48)).isoformat()
        self.assertEqual(SEO._vigencia(circuito(ultima=justo), _gen()), "sin")

    def test_no_recurrente_50h_sigue_sin(self):
        # veces < _UMBRAL_RECURRENCIA (azules de pocas menciones): FUERA de la
        # regla, pase lo que pase con el silencio.
        self.assertEqual(SEO._vigencia(circuito(veces=2), _gen()), "sin")
        self.assertEqual(SEO._vigencia(circuito(veces=1), _gen()), "sin")
        self.assertEqual(SEO._vigencia(circuito(veces=None), _gen()), "sin")

    def test_reporte_usuario_fresco_resetea_el_reloj(self):
        # Recurrente con `ultima` de hace 50 h PERO señal de usuario de hace
        # 10 h: la señal fresca resetea el reloj → sigue "sin".
        c = circuito(conteo_usuario={"ultima_sin": HACE_10H})
        self.assertEqual(SEO._vigencia(c, _gen()), "sin")

    def test_reporte_usuario_fresco_veredicto_con_da_con_vecinos(self):
        # Ídem con veredicto vecinal "con" VIGENTE (ultimo_con de hace 10 h
        # posterior a la caída): con_vecinos, no desconocido.
        c = circuito(reportado_con=True,
                     conteo_usuario={"ultimo_con": HACE_10H})
        self.assertEqual(SEO._vigencia(c, _gen()), "con_vecinos")

    def test_con_vecinos_envejecido_cae_a_desconocido(self):
        # con_vecinos con veredicto de hace 50 h y NADA más: su última
        # noticia es el reporte del vecino; si envejece > 48 h sin más
        # noticias, cae a desconocido (la rama va ANTES de con_vecinos).
        c = circuito(estado_fecha="2026-07-01T06:00:00+00:00",
                     reportado_con=True,
                     conteo_usuario={"ultimo_con": HACE_50H})
        self.assertEqual(SEO._vigencia(c, _gen()), "desconocido")

    def test_toda_senal_de_usuario_cuenta_para_el_reloj(self):
        # desde/ultima_sin/ultimo_con/ultimo_reset: CUALQUIERA fresca evita
        # el decaimiento (el doc las lista explícitamente).
        for senal in ("desde", "ultima_sin", "ultimo_con", "ultimo_reset"):
            c = circuito(conteo_usuario={senal: HACE_10H})
            self.assertEqual(SEO._vigencia(c, _gen()), "sin", senal)

    def test_sin_reloj_queda_como_esta(self):
        # Sin `ultima` ni señales de usuario: NO hay reloj → no se mide
        # silencio → el circuito queda "sin" (no inventar).
        self.assertEqual(SEO._vigencia(circuito(ultima=None), _gen()), "sin")
        self.assertEqual(SEO._vigencia(
            circuito(ultima=None, conteo_usuario={}), _gen()), "sin")

    def test_generado_ausente_o_invalido_no_cae(self):
        # Sin generado (o no parseable) no hay reloj de referencia: decae a la
        # regla anterior, sin lanzar (determinismo: nunca el reloj real).
        self.assertEqual(SEO._vigencia(circuito(), None), "sin")
        self.assertEqual(SEO._vigencia(circuito(), SEO._dt("no-es-fecha")), "sin")

    def test_datos_a_futuro_no_miden(self):
        # `ultima` posterior al generado (datos adelantados): silencio None →
        # "sin" (el reloj no se inventa hacia atrás).
        futuro = "2026-07-04T00:00:00+00:00"
        self.assertEqual(SEO._vigencia(circuito(ultima=futuro), _gen()), "sin")

    def test_con_servicio_fresco_y_asum_no_decaen(self):
        # Con servicio y noticia FRESCA (10 h) sigue "con"; el None/asum no
        # entra en el escalonamiento (solo sin/con servicio decaen).
        self.assertEqual(SEO._vigencia(circuito(estado="con servicio",
                                                veces=9, ultima=HACE_10H),
                                        _gen()), "con")
        self.assertEqual(SEO._vigencia(circuito(estado=None, veces=9),
                                        _gen()), "asum")

    def test_reportado_con_sin_reloj_sigue_con_vecinos(self):
        # con_vecinos sin `ultima` ni señales: sin reloj, se conserva el
        # veredicto vecinal (comportamiento previo intacto).
        self.assertEqual(SEO._vigencia(circuito(ultima=None, reportado_con=True,
                                                conteo_usuario={}),
                                       _gen()), "con_vecinos")


class TestEscalonAzul(unittest.TestCase):
    """Segundo escalón del decaimiento y extensión a los "con servicio":
    48 h → desconocido (sin Y con por igual); 48 h + 7 días = 216 h → azul
    "asum" hasta que una noticia nueva despierte al circuito. Ni desc ni
    azul durante evento_nacional; los no recurrentes nunca decaen."""

    def test_con_servicio_50h_decae_a_desconocido(self):
        # El mantenedor lo reafirmó: el "con servicio" con silencio total de
        # 50 h YA NO retorna "con" incondicional.
        c = circuito(estado="con servicio", veces=9)
        self.assertEqual(SEO._vigencia(c, _gen()), "desconocido")
        self.assertFalse(SEO._sin_efectivos(c, _gen()))  # tampoco cuenta "sin"

    def test_con_servicio_47h_sigue_con(self):
        # Debajo del umbral, la confirmación implícita sigue valiendo.
        c = circuito(estado="con servicio", veces=9, ultima=HACE_47H,
                     estado_fecha=HACE_47H)
        self.assertEqual(SEO._vigencia(c, _gen()), "con")

    def test_220h_decae_a_azul_asum(self):
        # 220 h = 48 h + 7 d + 1: pasa al grupo azul "asum" («sin apagones
        # reportados»), sin y con servicio por igual.
        sin_azul = circuito(ultima=HACE_220H, estado_fecha=HACE_220H)
        con_azul = circuito(estado="con servicio", veces=9,
                            ultima=HACE_220H, estado_fecha=HACE_220H)
        self.assertEqual(SEO._vigencia(sin_azul, _gen()), "asum")
        self.assertEqual(SEO._vigencia(con_azul, _gen()), "asum")
        # Un azul por decaimiento NO cuenta en la cifra «sin».
        self.assertFalse(SEO._sin_efectivos(sin_azul, _gen()))
        self.assertFalse(SEO._sin_efectivos(con_azul, _gen()))

    def test_100h_entre_umbrales_desconocido(self):
        # 100 h: ya pasó el primer umbral (48 h) pero no el segundo (216 h).
        sin_100 = circuito(ultima=HACE_100H, estado_fecha=HACE_100H)
        con_100 = circuito(estado="con servicio", veces=9,
                           ultima=HACE_100H, estado_fecha=HACE_100H)
        self.assertEqual(SEO._vigencia(sin_100, _gen()), "desconocido")
        self.assertEqual(SEO._vigencia(con_100, _gen()), "desconocido")

    def test_con_vecinos_envejecido_220h_cae_a_azul(self):
        # El veredicto vecinal usa el MISMO escalonamiento: una semana de
        # silencio tras el "con_vecinos" y el circuito es azul, no desconocido.
        c = circuito(ultima="2026-06-20T00:00:00+00:00",
                     estado_fecha="2026-06-20T00:00:00+00:00",
                     reportado_con=True, conteo_usuario={"ultimo_con": HACE_220H})
        self.assertEqual(SEO._vigencia(c, _gen()), "asum")

    def test_no_recurrente_220h_nunca_decae(self):
        # veces < _UMBRAL_RECURRENCIA: ni desc ni azul, pase lo que pase.
        sin = circuito(veces=2, ultima=HACE_220H, estado_fecha=HACE_220H)
        con = circuito(veces=2, estado="con servicio",
                       ultima=HACE_220H, estado_fecha=HACE_220H)
        self.assertEqual(SEO._vigencia(sin, _gen()), "sin")
        self.assertEqual(SEO._vigencia(con, _gen()), "con")

    def test_evento_nacional_220h_estados_vigentes(self):
        # Durante el evento NO decae NADA (ni desc ni azul): el recurrente
        # silencioso queda en su estado vigente sin/con como hoy.
        self.addCleanup(setattr, SEO, "_EVENTO_NACIONAL", SEO._EVENTO_NACIONAL)
        SEO._EVENTO_NACIONAL = True
        sin = circuito(ultima=HACE_220H, estado_fecha=HACE_220H)
        con = circuito(estado="con servicio", veces=9,
                       ultima=HACE_220H, estado_fecha=HACE_220H)
        self.assertEqual(SEO._vigencia(sin, _gen()), "sin")
        self.assertEqual(SEO._vigencia(con, _gen()), "con")
        self.assertTrue(SEO._sin_efectivos(sin, _gen()))  # la cifra de crisis

    def test_despertar_noticia_fresca_devuelve_al_estado_del_parte(self):
        # Un azul (220 h) con noticia nueva FRESCA (10 h) vuelve a sin/con
        # según lo que diga esa noticia: el reloj se resetea.
        sin_despierto = circuito(ultima=HACE_220H, estado_fecha=HACE_220H,
                                 conteo_usuario={"ultima_sin": HACE_10H})
        con_despierto = circuito(estado="con servicio", veces=9,
                                 ultima=HACE_10H, estado_fecha=HACE_220H)
        self.assertEqual(SEO._vigencia(sin_despierto, _gen()), "sin")
        self.assertEqual(SEO._vigencia(con_despierto, _gen()), "con")

    def test_paridad_azul_no_cuenta_en_las_6_medidas_sin(self):
        # Paridad: un circuito pasado a azul NO cuenta en NINGUNA cifra «sin»
        # (las 6 medidas que consumen _sin_efectivos): hub, portada (x2),
        # hija, ranking y _circ_sin — la cifra «sin» queda solo con ZZ02
        # (caído real sin reloj) y el azul ni siquiera entra en el estimado.
        estado = {"generado": GENERADO,
                  "poblacion_municipio": {"Playa": 1000}}
        azul = circuito(codigo="ZZ06", ultima=HACE_220H, estado_fecha=HACE_220H)
        sin_real = circuito(codigo="ZZ02", ultima=None)
        circ = {"circuitos": [azul, sin_real]}
        gen = _gen()
        self.assertEqual(SEO._vigencia(azul, gen), "asum")
        self.assertFalse(SEO._sin_efectivos(azul, gen))
        self.assertEqual([c["codigo"] for c in SEO._circ_sin(circ, gen)], ["ZZ02"])
        self.assertEqual(SEO.conteo_municipio("Playa", circ, gen), (1, 2))
        self.assertIn("1 <small>de 2 circuitos sin servicio</small>",
                      SEO.region_hub(estado, circ, ["Playa"]))
        portada = SEO.instantanea_index(estado, circ)
        self.assertIn("<b>1 de 2 circuitos</b>", portada)
        hija = SEO.pagina_municipio("Playa", estado, circ, ["Playa"])
        self.assertIn("<b>1 de 2 circuitos</b> del municipio", hija)
        self.assertIn("<b>1 de 1 municipios más afectados hoy</b>",
                      SEO.ranking_poblacion("Playa", estado, circ, ["Playa"]))
        # El estimado de personas NO lo cuenta como sin (fracción 1/2 → 500).
        self.assertEqual(SEO._estimado_afectados("Playa", estado, circ), 500)


class TestConstantes(unittest.TestCase):
    """Constantes nombradas, tunables (doc: definiciones operativas)."""

    def test_valores_y_convenciones(self):
        self.assertEqual(SEO._UMBRAL_RECURRENCIA, 3)  # = MIN_POSTS aprende_circuitos
        self.assertEqual(SEO._UMBRAL_DESC_H, 48.0)
        # Segundo escalón: azul = 48 h + 7 días (constante nombrada, derivada).
        self.assertEqual(SEO._UMBRAL_AZUL_H, 216.0)
        self.assertEqual(SEO._UMBRAL_AZUL_H, SEO._UMBRAL_DESC_H + 24 * 7)

    def test_orden_de_grupos_y_etiqueta(self):
        self.assertEqual(SEO._GRUPO,
                         {"sin": 0, "con_vecinos": 1, "desconocido": 2,
                          "con": 3, "asum": 4})
        self.assertEqual(SEO._ESTADO_FILA["desconocido"],
                         ("desc", "estado desconocido"))


class TestFilaDesconocida(unittest.TestCase):
    """Duración de la fila desconocida: «sin datos hace N días» (N = días
    enteros del silencio, mismo reloj) y NUNCA «lleva X sin corriente»."""

    def _html(self, circuitos):
        return SEO.catalogo_circuitos("Playa", {"generado": GENERADO},
                                      {"circuitos": circuitos})

    def test_sin_datos_hace_n_dias(self):
        html = self._html([circuito()])  # silencio 50 h → 2 días enteros
        self.assertIn('<span class="circ-est desc">estado desconocido</span>', html)
        self.assertIn("sin datos hace 2 días", html)
        self.assertNotIn("lleva", html)

    def test_singular_dia(self):
        # 49.9 h de silencio → 2 días; probamos el borde de 1 día completo:
        # 24-47 h NO es desconocido, así que el singular solo es alcanzable
        # desde 48 h+ (49 h → 2 días). Verificamos el plural por defecto y el
        # helper directo para 1 día.
        c = circuito(ultima="2026-07-02T13:10:00+00:00")  # 26 h: sigue "sin"
        html = self._html([c])
        self.assertIn("sin servicio", html)
        self.assertEqual(SEO._dias_silencio(
            circuito(ultima="2026-07-02T15:10:00+00:00"), _gen()), 1)

    def test_sin_reloj_fila_sin_duracion(self):
        # Sin `ultima` ni señales no hay reloj: el circuito ni siquiera decae
        # a desconocido, así que la fila sigue siendo "sin" sin «sin datos».
        html = self._html([circuito(ultima=None)])
        self.assertIn("sin servicio", html)
        self.assertNotIn("sin datos hace", html)

    def test_grupo_desconocido_entre_con_vecinos_y_con(self):
        # El catálogo agrupa: sin=0, con_vecinos=1, desconocido=2, con=3,
        # asum=4 — la fila desconocida sale después de las caídas y antes de
        # los "con".
        desc = circuito(codigo="ZZ03")
        vec = circuito(codigo="ZZ04", reportado_con=True,
                       conteo_usuario={"ultimo_con": HACE_10H})
        con = circuito(codigo="ZZ05", estado="con servicio", ultima=HACE_10H)
        html = self._html([con, desc, vec])
        i_vec = html.index("ZZ04")
        i_desc = html.index("ZZ03")
        i_con = html.index("ZZ05")
        self.assertLess(i_vec, i_desc)
        self.assertLess(i_desc, i_con)


class TestEstimadoYParidad(unittest.TestCase):
    """Los desconocidos quedan FUERA del estimado de personas y de la cifra
    «sin» (el caso de paridad de las cuatro superficies vive extendido en
    TestParidadCifraSin, test_reporte_bidireccional.py)."""

    def test_estimado_excluye_desconocidos(self):
        estado = {"generado": GENERADO,
                  "poblacion_municipio": {"Playa": 1000}}
        # Solo un desconocido en el municipio: fracción de sin = 0/1 → 0
        # personas estimadas (el estado no es afirmable).
        circ = {"circuitos": [circuito()]}
        self.assertEqual(SEO._estimado_afectados("Playa", estado, circ), 0)

    def test_estimado_cuenta_solo_los_sin_reales(self):
        estado = {"generado": GENERADO,
                  "poblacion_municipio": {"Playa": 1000}}
        # 1 "sin" real (sin reloj, no decae) + 1 desconocido: fracción 1/2.
        sin_real = circuito(codigo="ZZ02", ultima=None)
        circ = {"circuitos": [circuito(), sin_real]}
        self.assertEqual(SEO._estimado_afectados("Playa", estado, circ), 500)


class TestSuperficiesJS(unittest.TestCase):
    """Las 3 superficies JS: misma regla (veces >= 3, silencio > 48 h contra
    generado), verificación por asserts de string (convención del repo)."""

    def test_app_js_rama_desconocido_en_sin_servicio(self):
        self.assertIn("if ((c.veces || 0) >= UMBRAL_RECURRENCIA) {", APP_JS)
        self.assertIn('if (s != null && s > UMBRAL_DESC_H) return "desconocido";', APP_JS)
        # el reloj es estado.generado, NUNCA Date.now()
        rama = APP_JS.index("function circuitoVigente")
        fin = APP_JS.index("function popupMunicipio")
        bloque = APP_JS[rama:fin]
        self.assertNotIn("Date.now()", bloque)
        self.assertIn("silencioHoras(c, estado.generado)", bloque)

    def test_con_vecinos_consulta_silencio_en_app_y_circuitos(self):
        # R3-1: el veredicto vecinal también caduca — ENTRE la condición
        # reportado_con y su retorno está el MISMO chequeo de silencio
        # (recurrente + > 48 h contra generado) que la rama "sin".
        for js, retorno in ((APP_JS, 'return "con_vecinos";'),
                            (CIRC_JS, 'clase: "con-vec"')):
            bloque = js[js.index("c.reportado_con"):]
            bloque = bloque[:bloque.index(retorno)]
            self.assertIn("(c.veces || 0) >= UMBRAL_RECURRENCIA", bloque)
            self.assertIn("silencioHoras(", bloque)
            self.assertIn("UMBRAL_DESC_H", bloque)

    def test_app_js_resumen_y_popup_cuentan_desconocidos(self):
        self.assertIn("desc: 0", APP_JS)                      # popupMunicipio
        self.assertIn("desconocidos · ", APP_JS)              # popup resumen
        self.assertIn('rc-chip desc', APP_JS)                 # chip de portada
        self.assertIn('seg desc', APP_JS)                     # segmento de barra
        self.assertIn("desconocido: { l: ", APP_JS)           # capa del mapa
        self.assertIn('"dot-status", "est-desc"', APP_JS)     # icono del sprite

    def test_worker_js_rama_y_resumen(self):
        self.assertIn('if (s != null && s > UMBRAL_DESC_H) return "desconocido";', WORKER_JS)
        self.assertIn('desconocido: "estado desconocido"', WORKER_JS)
        self.assertIn("estado_desconocido: conteo.desconocido", WORKER_JS)
        self.assertIn('estado_desconocido: cuenta("estado desconocido")', WORKER_JS)
        self.assertIn("silencioHoras(c, est.generado)", WORKER_JS)

    def test_worker_prompt_linea_de_48h(self):
        self.assertIn("estado desconocido", WORKER_JS)
        self.assertIn("3+ partes", WORKER_JS)
        self.assertIn("48 horas", WORKER_JS)
        self.assertIn("no afirmes ni que están sin corriente", WORKER_JS)
        # Segundo escalón en el prompt: una semana → grupo azul, hasta que
        # una noticia nueva despierte al circuito.
        self.assertIn("más de una semana de silencio", WORKER_JS)
        self.assertIn('grupo azul de "sin cortes reportados"', WORKER_JS)
        self.assertIn("los despierte", WORKER_JS)

    def test_umbral_azul_en_las_3_superficies(self):
        # Constante nombrada derivada (48 + 168) y el escalonamiento COMPLETO
        # (azul antes que desconocido) en las 4 ramas de estado de cada
        # cliente: reportado_con/con_vecinos, "con", "sin" — 3 usos por file.
        for js in (APP_JS, WORKER_JS, CIRC_JS):
            self.assertIn("const UMBRAL_AZUL_H = UMBRAL_DESC_H + 24 * 7;", js)
            # 4 ramas de estado (reportado_con, con, sin) + azul ANTES de desc:
            # 3 usos del umbral azul por archivo, uno por rama.
            self.assertEqual(js.count("s > UMBRAL_AZUL_H"), 3)

    def test_rama_con_servicio_consulta_silencio(self):
        # El mantenedor reafirmó: los "con servicio" TAMBIÉN decaen. La rama
        # "con" de cada cliente ya NO retorna "con" incondicional: entre su
        # apertura y el retorno está el MISMO bloque de silencio (azul/desc)
        # que la rama "sin", contra generado (nunca Date.now()).
        for js, cierre, azul in ((APP_JS, 'return "con";', 'return "asum";'),
                                 (WORKER_JS, 'return "con";', 'return "asum";'),
                                 (CIRC_JS, 'return { clase: "con", txt: "con servicio"',
                                  'clase: "asum"')):
            ini = js.index('if (c.estado === "con servicio") {')
            bloque = js[ini:ini + js[ini:].index(cierre)]
            self.assertIn("(c.veces || 0) >= UMBRAL_RECURRENCIA", bloque)
            self.assertIn("silencioHoras(", bloque)
            self.assertIn("UMBRAL_AZUL_H", bloque)
            self.assertIn(azul, bloque)
            self.assertIn("desconocido", bloque)

    def test_circuitos_js_rama_duracion_y_encabezado(self):
        self.assertIn('{ clase: "desc", txt: "estado desconocido"', CIRC_JS)
        self.assertIn("sinDatosDesde(c)", CIRC_JS)
        self.assertIn("sin datos hace", CIRC_JS)
        self.assertIn("est-desc", CIRC_JS)
        # reloj determinista: estado.generado en la clasificación Y la fila
        self.assertIn("silencioHoras(c, ESTADO.generado)", CIRC_JS)

    def test_css_clases_con_token_gris(self):
        for regla, token in ((".circ-est.desc", "var(--gray-bg)"),
                             (".circ-lleva.desc", "var(--text-muted)"),
                             (".rc-barra .seg.desc", "var(--gray)"),
                             (".rc-mini-barra .seg.desc", "var(--gray)"),
                             (".est-desc", "var(--gray)")):
            m = re.search(re.escape(regla) + r"\s*\{[^}]*\}", CSS)
            self.assertIsNotNone(m, "falta %s" % regla)
            self.assertIn(token, m.group(0), regla)
        self.assertIn(".rc-chip.desc", CSS)


class TestEventoNacional(unittest.TestCase):
    """R3-2: durante evento_nacional NO hay decaimiento (los clientes JS ya
    evalúan la puerta de SEN ANTES de esa rama): el recurrente silencioso 50 h
    sigue "sin" y la cifra de paridad lo cuenta como sin en las 6 medidas."""

    ESTADO = {"generado": GENERADO,
              "evento_nacional": {"desde": "2026-07-02T00:00:00+00:00"}}

    def setUp(self):
        self.addCleanup(setattr, SEO, "_EVENTO_NACIONAL", SEO._EVENTO_NACIONAL)
        SEO._EVENTO_NACIONAL = True

    def test_recurrente_silencioso_50h_sigue_sin(self):
        # El mismo circuito que test_recurrente_50h_decae_a_desconocido, con
        # el evento activo: la rama desconocido se salta → "sin".
        self.assertEqual(SEO._vigencia(circuito(), _gen()), "sin")
        self.assertTrue(SEO._sin_efectivos(circuito(), _gen()))

    def test_la_cifra_de_paridad_lo_cuenta_como_sin(self):
        gen, circ = _gen(), {"circuitos": [circuito()]}
        self.assertEqual([c["codigo"] for c in SEO._circ_sin(circ, gen)],
                         ["ZZ01"])
        self.assertEqual(SEO.conteo_municipio("Playa", circ, gen), (1, 1))
        self.assertIn("1 <small>de 1 circuitos sin servicio</small>",
                      SEO.region_hub(self.ESTADO, circ, ["Playa"]))
        self.assertIn("<b>1 de 1 circuitos</b>",
                      SEO.instantanea_index(self.ESTADO, circ))
        hija = SEO.pagina_municipio("Playa", self.ESTADO, circ, ["Playa"])
        self.assertIn("<b>1 de 1 circuitos</b> del municipio", hija)
        self.assertIn("<b>1 de 1 municipios más afectados hoy</b>",
                      SEO.ranking_poblacion("Playa", self.ESTADO, circ,
                                            ["Playa"]))


if __name__ == "__main__":
    unittest.main()
