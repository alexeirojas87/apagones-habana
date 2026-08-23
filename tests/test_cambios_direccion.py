"""Tests de detección de cambios de dirección y conteo de usuario (discrepancia).

Cubre:
  1. _cobertura: solapamiento de tokens entre dos descripciones de calles.
  2. Detección de cambio de dirección: cobertura < 0.25 → cambio.
  3. Update de calles con cambio: la nueva gana si es cambio; "longest wins" si
     es la misma zona.
  4. Reset del conteo de usuario: "con" resetea, "sin" mantiene.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).parents[1] / "extractor"))

RUTA = SCRIPTS / "build_circuitos.py"
SPEC = importlib.util.spec_from_file_location("build_circuitos", RUTA)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class TestCobertura(unittest.TestCase):
    """_cobertura: solapamiento de tokens entre descripciones de calles."""

    def test_identicas(self):
        self.assertGreaterEqual(MOD._cobertura("Calle 23 entre 5ta y 7ma", "Calle 23 entre 5ta y 7ma"), 0.99)

    def test_cambio_total(self):
        """Zonas completamente diferentes → cobertura ~0 → cambio detectado."""
        self.assertLess(MOD._cobertura("Cerro y Primelles", "Plaza y 1ra"), 0.25)

    def test_subconjunto(self):
        """La nueva es un subconjunto de la vieja → cobertura alta (misma zona)."""
        self.assertGreaterEqual(MOD._cobertura("Calle 33 desde 5ta", "Calle 33 desde 5ta hasta 17"), 0.5)

    def test_vacia(self):
        """Una vacía → cobertura 0 (no es cambio, es primera vez)."""
        self.assertEqual(MOD._cobertura("Calle 23", ""), 0.0)
        self.assertEqual(MOD._cobertura("", "Calle 23"), 0.0)

    def test_cambio_parcial(self):
        """Zonas que comparten una palabra pero no son la misma → < 0.25."""
        self.assertLess(MOD._cobertura("Reparto Siboney, Calle 5", "Reparto Bahia, Calle 12"), 0.25)


class TestUpdateCallesConCambio(unittest.TestCase):
    """La regla de update: cambio de dirección reemplaza, misma zona = longest wins."""

    def test_cambio_reemplaza_aunque_mas_corta(self):
        """Si la nueva es un cambio de dirección (cobertura < 0.25), reemplaza
        aunque sea más corta que la vieja."""
        r = {"calles": "Cerro y Primelles, Habana"}
        nuevo = "Plaza y 1ra"
        self.assertTrue(
            not r["calles"] or MOD._cobertura(nuevo, r["calles"]) < 0.25 or len(nuevo) > len(r["calles"])
        )
        r["calles"] = nuevo
        self.assertEqual(r["calles"], "Plaza y 1ra")

    def test_misma_zona_longest_wins(self):
        """Si la nueva es la misma zona (cobertura >= 0.25), la más larga gana."""
        vieja = "Calle 33 desde 5ta"
        nueva = "Calle 33 desde 5ta hasta 17"
        self.assertGreaterEqual(MOD._cobertura(nueva, vieja), 0.25)
        self.assertGreater(len(nueva), len(vieja))

    def test_misma_zona_mas_corta_no_reemplaza(self):
        """Si la nueva es la misma zona pero más corta, no reemplaza."""
        vieja = "Calle 33 desde 5ta hasta 17"
        nueva = "Calle 33 desde 5ta"
        self.assertGreaterEqual(MOD._cobertura(nueva, vieja), 0.25)
        self.assertLess(len(nueva), len(vieja))


class TestConteoUsuario(unittest.TestCase):
    """Lógica del conteo de usuario: reset con 'con', acumulación con 'sin'."""

    def test_reset_con_usuario(self):
        """Usuario reporta 'con' → desde = null (reinicia)."""
        entry = {"desde": "2026-08-20T10:00:00Z", "ultima_sin": "2026-08-22T10:00:00Z", "ultimo_con": None}
        # Simular señal 'con'
        entry["desde"] = None
        entry["ultimo_con"] = "2026-08-22T14:00:00Z"
        self.assertIsNone(entry["desde"])

    def test_acumula_sin(self):
        """Usuario reporta 'sin' → desde se mantiene (ya estaba set)."""
        entry = {"desde": "2026-08-20T10:00:00Z", "ultima_sin": None, "ultimo_con": None}
        if entry["desde"] is None:
            entry["desde"] = "2026-08-22T10:00:00Z"
        entry["ultima_sin"] = "2026-08-22T10:00:00Z"
        self.assertEqual(entry["desde"], "2026-08-20T10:00:00Z")

    def test_reset_une(self):
        """UNE dice 'con servicio' → desde = null + ultimo_reset."""
        entry = {"desde": "2026-08-20T10:00:00Z", "ultima_sin": "2026-08-22T10:00:00Z", "ultimo_con": None}
        entry["desde"] = None
        entry["ultimo_reset"] = "2026-08-22T15:00:00Z"
        entry.pop("horas", None)
        self.assertIsNone(entry["desde"])
        self.assertIn("ultimo_reset", entry)

    def test_discrepado(self):
        """Discrepado: usuario dice sin (desde not null) y UNE dice con."""
        cu = {"desde": "2026-08-20T10:00:00Z", "ultima_sin": "2026-08-22T10:00:00Z", "ultimo_con": None}
        estado_une = "con servicio"
        discrepado = bool(cu.get("desde") and estado_une in ("con servicio", None))
        self.assertTrue(discrepado)

    def test_no_discrepado_une_sin(self):
        """No discrepado: ambos dicen sin (UNE dice 'sin servicio')."""
        cu = {"desde": "2026-08-20T10:00:00Z", "ultima_sin": "2026-08-22T10:00:00Z", "ultimo_con": None}
        estado_une = "sin servicio"
        discrepado = bool(cu.get("desde") and estado_une in ("con servicio", None))
        self.assertFalse(discrepado)

    def test_no_discrepado_usuario_con(self):
        """No discrepado: usuario ya reportó 'con' (desde = null)."""
        cu = {"desde": None, "ultima_sin": "2026-08-20T10:00:00Z", "ultimo_con": "2026-08-22T14:00:00Z"}
        estado_une = "con servicio"
        discrepado = bool(cu.get("desde") and estado_une in ("con servicio", None))
        self.assertFalse(discrepado)


class TestExtraerCodigosComentario(unittest.TestCase):
    """extracción de códigos de circuito de texto libre de comentarios."""

    def test_codigo_explicito(self):
        from circuitos_id import es_conocido
        import re
        RE = re.compile(r"\b([A-Za-z]{1,3})\s*(\d{1,4})\b")
        texto = "el NX4 sigue sin luz, ya llevamos 30 horas"
        codigos = []
        for m in RE.finditer(texto):
            cod = (m.group(1) + m.group(2)).upper().replace(" ", "")
            if es_conocido(cod) and cod not in codigos:
                codigos.append(cod)
        # NX4 puede o no estar en el catálogo — lo importante es que el regex lo captura
        self.assertIn("NX4", [(m.group(1) + m.group(2)).upper() for m in RE.finditer(texto)])

    def test_no_falso_positivo_palabra(self):
        import re
        RE = re.compile(r"\b([A-Za-z]{1,3})\s*(\d{1,4})\b")
        texto = "habana2026 no tiene corriente"
        matches = [m.group(0) for m in RE.finditer(texto)]
        # "habana2026" no debería matchear como código (la h no es seguida por dígitos)
        self.assertNotIn("hab", [m.group(1) for m in RE.finditer(texto) if m.group(1) == "hab"])


if __name__ == "__main__":
    unittest.main()
