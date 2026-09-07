"""U10 — Chatbot con la paleta compartida (D9/R1/R2).

El CSS inyectado por chatbot.js consume los tokens del sitio 1:1 (sin hexes
propios): así el widget sigue el tema claro/oscuro como el resto. El backend y
el API quedan intactos. Offline, stdlib, py3.9.
"""

import re
import unittest
from pathlib import Path

RAIZ = Path(__file__).parents[1]
JS = (RAIZ / "web" / "chatbot.js").read_text(encoding="utf-8")

# Mapa 1:1 del design D9 (nombres de tokens congelados + los nuevos).
TOKENS_REQUERIDOS = (
    "--cta", "--on-cta", "--surface", "--surface-2", "--border", "--border-2",
    "--text", "--text-muted", "--text-dim", "--accent", "--red-bg", "--red-t",
    "--text-strong",
)


class TokensChatbotTest(unittest.TestCase):
    def test_cero_hexes_en_el_estilo_del_widget(self):
        hexes = re.findall(r"#[0-9a-fA-F]{3,8}\b", JS)
        self.assertEqual(hexes, [], "hexes fuera de tokens: %r" % hexes)

    def test_consume_todos_los_tokens_del_mapa_d9(self):
        for token in TOKENS_REQUERIDOS:
            self.assertIn("var(%s)" % token, JS, "falta %s" % token)

    def test_tipografia_del_widget_con_el_token_compartido(self):
        self.assertIn("font-family: var(--fuente-sans)", JS)

    def test_el_backend_y_el_api_quedan_intactos(self):
        self.assertIn("/api/chat", JS)
        self.assertIn("body: JSON.stringify({ consulta: q, historial", JS)


if __name__ == "__main__":
    unittest.main()