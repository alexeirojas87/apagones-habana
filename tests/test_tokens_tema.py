"""U3 — Fundación de tokens: slate de doble tema + base de accesibilidad.

Spec R1–R4 (S1 automatizado; S4 parcialmente automatizado con el cálculo WCAG;
S5 requiere auditoría manual complementaria). Los NOMBRES de los tokens de
style.css son contrato congelado: solo cambian los valores y los tokens nuevos
se limitan a --on-cta, --text-strong, --fuente-sans, --ring, --focus.
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

CSS = (RAIZ / "web" / "style.css").read_text(encoding="utf-8")

# Los 8 superficies de render: 7 raíces commiteadas + 404 (las hijas salen del
# generador con su propio head).
SHELLS = [
    "index.html", "analitica.html", "partes.html", "circuitos.html",
    "sugerencias.html", "municipios/index.html",
    "preguntas-frecuentes/index.html", "404.html",
]

# Contrato congelado: los nombres que existían en style.css:1-12 ANTES del
# cambio (orden del archivo original).
NOMBRES_ORIGINALES = (
    "--bg", "--bg-header", "--surface", "--surface-2", "--border", "--border-2",
    "--text", "--text-soft", "--text-muted", "--text-dim",
    "--red", "--red-t", "--red-bg", "--green", "--green-t", "--green-bg",
    "--amber", "--amber-t", "--amber-bg", "--blue", "--blue-t", "--blue-bg",
    "--gray", "--gray-t", "--gray-bg", "--gold", "--accent", "--cta", "--mono",
)

# Únicos tokens nuevos permitidos (design Interfaces/Contracts + regla #2).
NOMBRES_NUEVOS_PERMITIDOS = ("--on-cta", "--text-strong", "--fuente-sans",
                             "--ring", "--focus")

# Familia navy del tema viejo: prohibida en style.css, shells y generador.
NAVY = ("#0c1322", "#0e1728", "#141f33", "#1a2740", "#24344f", "#2f4262",
        "#222c3d")

# Pares texto/fondo exigidos por R3 (≥4.5:1 en AMBOS temas).
PARES_TEXTO = (
    ("--text", "--bg"), ("--text-soft", "--bg"), ("--text-muted", "--bg"),
    ("--text-dim", "--bg"), ("--text-dim", "--surface"),
    ("--text-dim", "--surface-2"), ("--text-muted", "--surface-2"),
    ("--red-t", "--bg"), ("--red-t", "--red-bg"),
    ("--green-t", "--bg"), ("--green-t", "--green-bg"),
    ("--amber-t", "--bg"), ("--amber-t", "--amber-bg"),
    ("--blue-t", "--bg"), ("--blue-t", "--blue-bg"),
    ("--gray-t", "--bg"), ("--accent", "--bg"), ("--text-strong", "--bg"),
)


def bloque(regla):
    """El cuerpo { ... } de la primera regla cuyo selector coincida."""
    m = re.search(re.escape(regla) + r"\s*\{(.*?)\n\}", CSS, re.DOTALL)
    assert m, "no está la regla %s" % regla
    return m.group(1)


def tokens_de(cuerpo):
    """{--nombre: valor} del cuerpo de una regla (valores hex directos)."""
    salida = {}
    for decl in cuerpo.split(";"):
        if ":" not in decl:
            continue
        nombre, _, valor = decl.partition(":")
        nombre, valor = nombre.strip(), valor.strip()
        if nombre.startswith("--"):
            salida[nombre] = valor
    return salida


def luminancia(hex6):
    h = hex6.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contraste(a, b):
    la, lb = luminancia(a), luminancia(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


class TokensSlateTest(unittest.TestCase):
    def test_raiz_con_hex_slate_y_semantica_pinada(self):
        raiz = tokens_de(bloque(":root"))
        for hex_slate in ("#0F172A", "#111827", "#1E293B", "#334155", "#F8FAFC",
                          "#16A34A", "#DC2626"):
            self.assertIn(hex_slate.lower(), [v.lower() for v in raiz.values()],
                          "falta %s en :root" % hex_slate)
        # --cta = VERDE (familia #16A34A/#22c55e), nunca el rojo destructivo
        self.assertIn(raiz["--cta"].lower(), ("#22c55e", "#16a34a"))
        self.assertNotEqual(raiz["--cta"].lower(), "#dc2626")
        # los nombres del contrato siguen todos declarados
        for nombre in NOMBRES_ORIGINALES:
            self.assertIn(nombre, raiz, "contrato congelado: falta %s" % nombre)

    def test_bloque_de_tema_claro_paralelo(self):
        self.assertIn('[data-theme="light"]', CSS)
        claro = bloque('[data-theme="light"]')
        self.assertIn("color-scheme:light", claro.replace(" ", ""))
        claro_t = tokens_de(claro)
        for nombre in ("--bg", "--text", "--surface", "--surface-2",
                       "--border", "--cta", "--accent"):
            self.assertIn(nombre, claro_t, "el tema claro no redefine %s" % nombre)
        self.assertNotEqual(claro_t["--bg"].lower(), "#0f172a")

    def test_tokens_nuevos_limitados_a_los_permitidos(self):
        declarados = set(re.findall(r"(--[a-z0-9-]+)\s*:", CSS))
        permitidos = set(NOMBRES_ORIGINALES) | set(NOMBRES_NUEVOS_PERMITIDOS)
        extranos = declarados - permitidos
        self.assertEqual(extranos, set(),
                         "tokens fuera del contrato: %r" % extranos)
        raiz = tokens_de(bloque(":root"))
        for nuevo in ("--on-cta", "--text-strong", "--ring", "--focus"):
            self.assertIn(nuevo, raiz, "falta el token nuevo %s" % nuevo)

    def test_sin_valores_navy_en_css_shells_ni_generador(self):
        superficies = [CSS, RUTA_SEO.read_text(encoding="utf-8")]
        superficies += [(RAIZ / "web" / a).read_text(encoding="utf-8") for a in SHELLS]
        for i, texto in enumerate(superficies):
            for hex_navy in NAVY:
                self.assertNotIn(hex_navy.lower(), texto.lower(),
                                 "%s en superficie #%d" % (hex_navy, i))


class GuionSinDestelloTest(unittest.TestCase):
    def test_las_8_cabeceras_lo_cargan_antes_del_css(self):
        for archivo in SHELLS:
            html = (RAIZ / "web" / archivo).read_text(encoding="utf-8")
            guion = html.find('localStorage.getItem("tema")')
            css = html.find('<link rel="stylesheet"')
            self.assertGreater(guion, -1, "%s: falta el script sin destello" % archivo)
            self.assertLess(guion, css, "%s: el script debe ir antes del CSS" % archivo)
            self.assertIn("prefers-color-scheme", html, archivo)


class AccesibilidadBaseTest(unittest.TestCase):
    def test_anillo_de_foco_global_con_el_token(self):
        regla = re.search(r":focus-visible\s*\{[^}]*\}", CSS)
        self.assertIsNotNone(regla, "falta :focus-visible global")
        self.assertIn("var(--ring)", regla.group(0))

    def test_movimiento_reducido_suprimido(self):
        m = re.search(r"@media\s*\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)\s*\{", CSS)
        self.assertIsNotNone(m, "falta el bloque prefers-reduced-motion")

    def test_objetivos_de_44px_en_pestanas_y_controles(self):
        for selector in (r"\.tabs a,\s*\.tabs \.activo", r"#controles button"):
            regla = re.search(selector + r"\s*\{[^}]*\}", CSS)
            self.assertIsNotNone(regla, "falta la regla %s" % selector)
            self.assertRegex(regla.group(0), r"min-height:\s*44px")


class ContrasteTokensTest(unittest.TestCase):
    """S4 automatizado para los pares token: ≥4.5:1 en ambos temas."""

    def _par(self, tokens, nombre_fg, nombre_bg):
        fg, bg = tokens[nombre_fg], tokens[nombre_bg]
        self.assertTrue(fg.startswith("#") and bg.startswith("#"),
                        "par no hex: %s=%s sobre %s=%s" % (nombre_fg, fg, nombre_bg, bg))
        return contraste(fg, bg)

    def test_pares_de_texto_en_ambos_temas(self):
        temas = {"oscuro": tokens_de(bloque(":root")),
                 "claro": tokens_de(bloque('[data-theme="light"]'))}
        for tema, t in temas.items():
            for fg, bg in PARES_TEXTO:
                r = self._par(t, fg, bg)
                self.assertGreaterEqual(r, 4.5,
                                        "%s: %s sobre %s = %.2f" % (tema, fg, bg, r))

    def test_el_anillo_llega_al_menos_a_3_en_ambos_temas(self):
        temas = {"oscuro": tokens_de(bloque(":root")),
                 "claro": tokens_de(bloque('[data-theme="light"]'))}
        for tema, t in temas.items():
            r = self._par(t, "--ring", "--bg")
            self.assertGreaterEqual(r, 3.0, "%s: ring %.2f" % (tema, r))


class ContrastePopupTest(unittest.TestCase):
    """Desde U4 (D8) el popup sale de tokens: .rep y .hora llevan los mismos
    pares ya verificados en ContrasteTokensTest; solo se exige que ya no
    mantengan hex sueltos."""

    def test_rep_y_hora_usan_tokens_de_texto(self):
        rep = re.search(r"\.popup \.rep\s*\{[^}]*\}", CSS).group(0)
        hora = re.search(r"\.popup \.hora\s*\{[^}]*\}", CSS).group(0)
        self.assertIn("var(--red-t)", rep)
        self.assertIn("var(--text-muted)", hora)


if __name__ == "__main__":
    unittest.main()
