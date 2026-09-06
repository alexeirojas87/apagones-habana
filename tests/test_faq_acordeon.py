"""U7 — FAQ como acordeón nativo details/summary.

Spec R10/S12 (S13 es nativo: details responde a Enter/Espacio sin JS) y R7:
el cuerpo del FAQ sale de la MISMA fuente única (FAQ_PREGUNTAS) que el
JSON-LD FAQPage, que queda byte-idéntico (paridad por construcción).
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


class CuerpoFaqTest(unittest.TestCase):
    def setUp(self):
        self.cuerpo = MOD.cuerpo_faq()

    def test_emite_details_summary_con_div_de_respuesta(self):
        m = re.search(r'<details class="faq" id="p1">\s*<summary>(.*?)</summary>\s*'
                      r'<div class="faq-a">(.*?)</div>\s*</details>', self.cuerpo, re.DOTALL)
        self.assertIsNotNone(m, "el primer ítem no es acordeón nativo")
        self.assertEqual(m.group(1), MOD.esc_html(MOD.FAQ_PREGUNTAS[0][0]))
        self.assertEqual(m.group(2), MOD.FAQ_PREGUNTAS[0][1])

    def test_las_8_preguntas_son_details_con_ids_seguidos(self):
        ids = re.findall(r'<details class="faq" id="p(\d+)">', self.cuerpo)
        self.assertEqual(ids, [str(i) for i in range(1, 9)])
        self.assertEqual(len(re.findall(r"<summary>", self.cuerpo)), 8)
        # cero secciones h2 viejas
        self.assertNotIn("<h2>", self.cuerpo)

    def test_la_pregunta_va_en_el_summary_escapada(self):
        primera = MOD.FAQ_PREGUNTAS[0][0]
        self.assertIn("<summary>%s</summary>" % MOD.esc_html(primera), self.cuerpo)


class ParidadJsonLdTest(unittest.TestCase):
    def test_ld_faq_sigue_saliendo_de_faq_preguntas_sin_cambios(self):
        doc = MOD.ld_faq()
        self.assertEqual(doc["@type"], "FAQPage")
        self.assertEqual([e["name"] for e in doc["mainEntity"]],
                         [p for p, _ in MOD.FAQ_PREGUNTAS])
        self.assertEqual([e["acceptedAnswer"]["text"] for e in doc["mainEntity"]],
                         [r for _, r in MOD.FAQ_PREGUNTAS])


class EstilosAcordeonTest(unittest.TestCase):
    def test_css_del_acordeon_y_columna_legible(self):
        self.assertRegex(CSS, r"details\.faq\s*\{[^}]*var\(--")
        summary = re.search(r"details\.faq summary\s*\{[^}]*\}", CSS)
        self.assertIsNotNone(summary, "falta el estilo del summary")
        self.assertIn("cursor:pointer", summary.group(0).replace(" ", ""))
        self.assertRegex(CSS, r"\.faq-a\s*\{[^}]*\}")
        columna = re.search(r"#faq-preguntas\s*\{[^}]*\}", CSS)
        self.assertIsNotNone(columna, "falta el estilo de #faq-preguntas")
        self.assertIn("max-width", columna.group(0))
        # chevron con transform, apagado el marcador nativo de webkit
        self.assertIn("::-webkit-details-marker", CSS)
        self.assertIn("details.faq[open]", CSS)


if __name__ == "__main__":
    unittest.main()