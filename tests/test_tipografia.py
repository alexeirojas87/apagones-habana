"""U6 — Inter 400/700 (latin) autoalojada con OFL.

Spec R9/S11 (parte estática): los woff2 y la licencia viven commiteados en
web/fonts/, el @font-face usa font-display: swap, el texto corre sobre el
token --fuente-sans con el fallback métricamente compatible anterior intacto,
--mono no cambia y no hay URLs de CDN de fuentes en la superficie web.
Offline, stdlib, py3.9.
"""

import re
import unittest
from pathlib import Path

RAIZ = Path(__file__).parents[1]
CSS = (RAIZ / "web" / "style.css").read_text(encoding="utf-8")

FUENTES = (
    ("inter-latin-400.woff2", 20_000, 30_000),
    ("inter-latin-700.woff2", 20_000, 30_000),
)
CDNS = ("fonts.googleapis.com", "fonts.gstatic.com", "cdn.jsdelivr.net",
        "cdnjs.cloudflare.com", "use.typekit", "fonts.adobe.com")


class ArchivosDeFuenteTest(unittest.TestCase):
    def test_los_dos_woff2_comiteados_con_peso_valido(self):
        hashes = set()
        for nombre, minimo, maximo in FUENTES:
            ruta = RAIZ / "web" / "fonts" / nombre
            self.assertTrue(ruta.exists(), "falta %s" % nombre)
            datos = ruta.read_bytes()
            self.assertGreaterEqual(len(datos), minimo, nombre)
            self.assertLessEqual(len(datos), maximo, nombre)
            self.assertEqual(datos[:4], b"wOF2", "no es woff2: %s" % nombre)
            hashes.add(len(datos))
        self.assertEqual(len(hashes), 2, "400 y 700 no deben ser el mismo archivo")

    def test_licencia_ofl_comiteada(self):
        ruta = RAIZ / "web" / "fonts" / "OFL.txt"
        self.assertTrue(ruta.exists(), "falta web/fonts/OFL.txt")
        texto = ruta.read_text(encoding="utf-8")
        self.assertIn("SIL OPEN FONT LICENSE", texto)
        self.assertIn("Inter Project Authors", texto)


class FontFaceTest(unittest.TestCase):
    def test_una_pareja_font_face_con_swap_y_rutas_locales(self):
        caras = re.findall(r"@font-face\s*\{(.*?)\}", CSS, re.DOTALL)
        de_inter = [c for c in caras if "Inter" in c]
        self.assertEqual(len(de_inter), 2, "deben ser 400 y 700")
        pesos = set()
        for cara in de_inter:
            self.assertIn("font-display:swap", cara.replace(" ", ""))
            self.assertIn("Inter", cara)
            m = re.search(r"font-weight:\s*(\d+)", cara)
            self.assertIsNotNone(m, cara)
            pesos.add(m.group(1))
            self.assertRegex(cara, r"url\(/fonts/inter-latin-(400|700)\.woff2\)")
        self.assertEqual(pesos, {"400", "700"})

    def test_cero_urls_de_cdn_de_fuentes_en_la_superficie(self):
        for p in (RAIZ / "web").rglob("*"):
            if not p.is_file() or p.suffix not in (".html", ".js", ".css", ".svg"):
                continue
            if any(d in p.parts for d in ("vendor", "tiles", "data", ".wrangler", "municipio")):
                continue
            if p.name == "_worker.js":
                continue
            texto = p.read_text(encoding="utf-8", errors="ignore").lower()
            for cdn in CDNS:
                self.assertNotIn(cdn, texto, "%s menciona %s" % (p.name, cdn))


class TokenFuenteSansTest(unittest.TestCase):
    def test_fuentes_sans_con_el_fallback_anterior_intacto(self):
        raiz = re.search(r":root\s*\{(.*?)\n\}", CSS, re.DOTALL).group(1)
        m = re.search(r"--fuente-sans:([^;]+);", raiz)
        self.assertIsNotNone(m, "falta el token --fuente-sans")
        pila = [f.strip() for f in m.group(1).split(",")]
        # orden del fallback original preservado (style.css:16 antes del cambio)
        self.assertEqual(pila, ['"Inter"', "-apple-system", '"Segoe UI"',
                                "Roboto", "sans-serif"])
        # el body consume el token (la línea del stack pasa a token)
        body = re.search(r"body\s*\{(.*?)\}", CSS, re.DOTALL).group(1)
        self.assertIn("font-family: var(--fuente-sans)", body)

    def test_mono_intacto(self):
        pila = re.search(r"--mono:([^;]+);", CSS).group(1)
        self.assertEqual([f.strip() for f in pila.split(",")],
                         ["ui-monospace", '"SF Mono"', '"JetBrains Mono"',
                          "Menlo", "Consolas", "monospace"])


if __name__ == "__main__":
    unittest.main()
