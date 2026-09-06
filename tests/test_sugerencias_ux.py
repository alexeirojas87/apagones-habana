"""U8 — UX del formulario de sugerencias.

Spec R11 (S14/S15 estático; el comportamiento en vivo es checklist manual):
validación en línea por campo (aria-invalid + aria-describedby), resumen de
errores al inicio con role=alert enfocable y enlaces por campo, regla de
mínimo 5 caracteres como pista estática en la etiqueta, estado de envío, y
caja de éxito que sustituye al formulario. El POST ({tipo,titulo,detalle})
queda intacto. Offline, stdlib, py3.9.
"""

import re
import unittest
from pathlib import Path

RAIZ = Path(__file__).parents[1]
HTML = (RAIZ / "web" / "sugerencias.html").read_text(encoding="utf-8")
JS = (RAIZ / "web" / "sugerencias.js").read_text(encoding="utf-8")
CSS = (RAIZ / "web" / "style.css").read_text(encoding="utf-8")

CAMPOS = ("tipo", "titulo", "detalle")


class ResumenDeErroresTest(unittest.TestCase):
    def test_el_resumen_existe_en_el_markup_con_role_alert_enfocable(self):
        m = re.search(r'<div id="sug-resumen"[^>]*>', HTML)
        self.assertIsNotNone(m, "falta el resumen de errores")
        self.assertIn('role="alert"', m.group(0))
        self.assertIn('tabindex="-1"', m.group(0))
        self.assertIn("hidden", m.group(0))

    def test_cada_campo_tiene_su_hueco_de_error(self):
        for campo in CAMPOS:
            self.assertIn('id="sug-error-%s"' % campo, HTML, campo)


class PistaYValidacionTest(unittest.TestCase):
    def test_la_regla_de_minimo_va_como_pista_estatica_en_la_etiqueta(self):
        self.assertIn("mínimo 5 caracteres", HTML)

    def test_el_js_marca_invalido_y_conecta_describedby(self):
        self.assertIn('setAttribute("aria-invalid", "true")', JS)
        self.assertIn('setAttribute("aria-describedby"', JS)
        self.assertIn("sug-error-", JS)

    def test_fallo_de_submit_enfoca_el_resumen_con_enlaces_por_campo(self):
        self.assertRegex(JS, r"resumen\.focus\(\)")
        self.assertRegex(JS, r'href="#sug-\$\{[^}]+\}"')  # enlace por campo

    def test_limpiar_errores_al_reintentar(self):
        self.assertIn("removeAttribute", JS)  # quita aria-invalid al corregir


class EnvioYExitoTest(unittest.TestCase):
    def test_estado_de_envio_preservado(self):
        self.assertIn('"Enviando…"', JS)
        self.assertRegex(JS, r"btn\.disabled\s*=\s*true")

    def test_el_payload_del_post_queda_intacto(self):
        self.assertIn("JSON.stringify({ tipo, titulo, detalle })", JS)
        self.assertIn("/api/sugerencia", JS)

    def test_la_caja_de_exito_sustituye_al_formulario(self):
        self.assertRegex(HTML, r'<div id="sug-exito"[^>]*role="status"[^>]*hidden')
        self.assertIn("form.hidden = true", JS)
        self.assertRegex(JS, r'sug-exito"\)\.hidden\s*=\s*false')


class EstilosFormularioTest(unittest.TestCase):
    def test_css_del_resumen_error_y_exito(self):
        for selector in (r"\.sug-resumen\s*\{[^}]*var\(--",
                         r"\.sug-error\s*\{[^}]*\}",
                         r"\.sug-exito\s*\{[^}]*var\(--"):
            self.assertIsNotNone(re.search(selector, CSS), selector)
        self.assertRegex(CSS, r'aria-invalid="true"')  # borde de campo inválido


if __name__ == "__main__":
    unittest.main()