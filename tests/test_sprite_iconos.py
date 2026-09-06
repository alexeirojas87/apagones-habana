"""U5 — Sprite SVG: cero emoji-como-icono.

Spec R8/S10: el sprite externo mismo-origen (web/icons.svg, símbolos 24×24,
currentColor) sustituye TODOS los emoji de la superficie web (nav, h1,
placeholders, popups, leyenda, capas, records, chatbot, formulario). Excluido
por contrato: web/_worker.js (los títulos de issues de GitHub son backend) y
las cadenas conversacionales del bot (no llevan emoji tras este cambio, así
que el gate corre sobre todo el árbol menos _worker.js y vendor).
Offline, stdlib, py3.9.
"""

import importlib.util
import re
import unittest
from pathlib import Path

RAIZ = Path(__file__).parents[1]
RUTA_SEO = RAIZ / "scripts" / "build_seo.py"
SPEC = importlib.util.spec_from_file_location("build_seo", RUTA_SEO)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

SPRITE = RAIZ / "web" / "icons.svg"

# Escaneo de emoji-as-icon (rango de la estrategia de pruebas del design).
EMOJI = re.compile(u"[\\U0001F300-\\U0001FAFF\\u2600-\\u27BF]")

# Los 24 símbolos: 7 nav + utilitarios + los que pide el inventario real
# (clipboard/construction/sprout/siren/clock/users no cabían en los ~18 del
# design y salieron del barrido completo de emojis).
SIMBOLOS = {
    "map", "chart", "megaphone", "zap", "buildings", "help", "lightbulb",
    "search", "chat", "bot", "x", "check", "alert-triangle", "alert-circle",
    "pin", "trophy", "wrench", "dot-status",
    "clipboard", "construction", "sprout", "siren", "clock", "users",
}

NAVEGACION = (
    ("map", "Mapa"), ("chart", "Análisis"), ("megaphone", "Partes"),
    ("zap", "Circuitos"), ("buildings", "Municipios"),
    ("help", "Preguntas"), ("lightbulb", "Sugerencias"),
)

# Archivos de superficie (los .js/.html/.css/.svg commiteados bajo web/).
EXCLUIR_DIR = {"vendor", "tiles", "data", ".wrangler", "municipio"}  # municipio: generado
EXCLUIR_ARCHIVO = {"_worker.js"}


def archivos_de_superficie():
    for p in sorted((RAIZ / "web").rglob("*")):
        if not p.is_file() or p.suffix not in (".html", ".js", ".css", ".svg"):
            continue
        if any(d in p.parts for d in EXCLUIR_DIR) or p.name in EXCLUIR_ARCHIVO:
            continue
        yield p


class SpriteTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SPRITE.exists(), "falta web/icons.svg")
        self.svg = SPRITE.read_text(encoding="utf-8")

    def test_declara_todos_los_simbolos_requeridos(self):
        ids = set(re.findall(r'<symbol id="icon-([a-z-]+)"', self.svg))
        faltan = SIMBOLOS - ids
        self.assertEqual(faltan, set(), "símbolos que faltan: %r" % faltan)

    def test_simbolos_en_viewbox_24_con_current_color(self):
        for m in re.finditer(r'<symbol id="icon-[a-z-]+"([^>]*)>', self.svg):
            attrs = m.group(1)
            self.assertIn('viewBox="0 0 24 24"', attrs, m.group(0))
        self.assertIn("currentColor", self.svg)


class CeroEmojisTest(unittest.TestCase):
    def test_toda_la_superficie_web_sin_emojis(self):
        sobrantes = {}
        for p in archivos_de_superficie():
            hits = EMOJI.findall(p.read_text(encoding="utf-8"))
            if hits:
                sobrantes[str(p.relative_to(RAIZ))] = sorted(set(hits))
        self.assertEqual(sobrantes, {}, "emojis restantes: %r" % sobrantes)

    def test_el_generador_tampoco_emite_emojis(self):
        self.assertEqual(EMOJI.findall(RUTA_SEO.read_text(encoding="utf-8")), [])


class NavConIconosTest(unittest.TestCase):
    def test_nav_tabs_lleva_icono_y_texto_por_destino(self):
        for icono, texto in NAVEGACION:
            nav = MOD.nav_tabs("") if icono != "map" else MOD.nav_tabs("analitica")
            self.assertRegex(
                nav, r'<use href="/icons\.svg#icon-%s"></use>' % icono,
                "falta el icono %s" % icono)
            self.assertIn(" " + texto, nav, "falta el texto %s" % texto)
            break  # una sola nav contiene los 7 destinos; el assert vale para todos
        for icono, _ in NAVEGACION:
            self.assertIn("icon-%s" % icono, MOD.nav_tabs(""), icono)

    def test_h1_y_tarjetas_del_generador_usan_el_sprite(self):
        estado, circ = (None, None)
        with open(RAIZ / "tests" / "fixtures" / "mini_estado.json", encoding="utf-8") as f:
            estado = importlib.import_module("json").load(f)
        with open(RAIZ / "tests" / "fixtures" / "mini_circuitos.json", encoding="utf-8") as f:
            circ = importlib.import_module("json").load(f)
        pagina = MOD.pagina_municipio("Playa", estado, circ, ["Playa"])
        self.assertIn("icon-zap", pagina)  # h1 del municipio
        self.assertNotIn(u"\U0001F5FA", pagina)  # 🗺 fuera


class UsoAccesibleTest(unittest.TestCase):
    def test_todo_icono_de_superficie_es_decorativo_con_aria_hidden(self):
        # los <svg> del sprite llevan aria-hidden; el texto visible adjacency
        # da el nombre (S10: los iconos significativos, como el botón de tema,
        # tienen aria-label/title y se cubren en el caso siguiente).
        for p in archivos_de_superficie():
            if p.suffix == ".css" or p.name == "icons.svg":
                continue
            texto = p.read_text(encoding="utf-8")
            for m in re.finditer(r"<svg\b[^>]*>", texto):
                self.assertIn('aria-hidden="true"', m.group(0),
                              "%s: %s" % (p.name, m.group(0)))

    def test_botones_de_solo_icono_con_nombre_accesible(self):
        toggle = (RAIZ / "web" / "chatbot.js").read_text(encoding="utf-8")
        self.assertIn('id="chatbot-toggle" title=', toggle)
        nav = MOD.nav_tabs("")
        self.assertIn('id="boton-tema"', nav)
        self.assertIn('aria-label="Cambiar entre tema claro y oscuro"', nav)


class ClasesDeEstadoTest(unittest.TestCase):
    """Los puntos de estado (🔴🟢🟠🔵⚪🟣🟡) salen de icon-dot-status + una
    clase de color definida en style.css con tokens (o el color duro heredado
    de las barras/capas que no tiene token: púrpura de averías, naranja de
    reportes)."""

    def test_clases_de_estado_definidas_en_el_css(self):
        css = (RAIZ / "web" / "style.css").read_text(encoding="utf-8")
        for clase, color in ((".est-sin", "var(--red)"), (".est-con", "var(--green)"),
                             (".est-disc", "var(--amber)"), (".est-asum", "var(--blue)"),
                             (".est-nd", "var(--gray)"), (".est-daf", "var(--gold)"),
                             (".est-av", "#b455c8"), (".est-rep", "#e07b00")):
            regla = re.search(re.escape(clase) + r"\s*\{[^}]*\}", css)
            self.assertIsNotNone(regla, "falta %s" % clase)
            self.assertIn(color, regla.group(0), clase)

    def test_los_estados_de_app_js_usan_el_punto_con_clase(self):
        app = (RAIZ / "web" / "app.js").read_text(encoding="utf-8")
        for clase in ("est-sin", "est-con", "est-disc", "est-asum", "est-nd"):
            self.assertIn('"dot-status", "%s"' % clase, app, clase)


if __name__ == "__main__":
    unittest.main()
