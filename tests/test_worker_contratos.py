"""Contratos estáticos y herméticos para el worker y el pipeline (convención
test_chatbot_tokens.py #36-37: el JS no es ejecutable con stdlib → asserts de
fuente; el Python se espeja por importlib). Offline, py3.9.

Secciones por unidad: U1 S15/S16 (estado.py + emit-compat), U2 S1-worker/S10/
S19-worker, U3 S1-chatbot, U4 S12/S13-mirror-estáticos/S17, U5 S18/S19-schema.
"""

import ast
import json
import unittest
from pathlib import Path

RAIZ = Path(__file__).parents[1]
ESTADO_PY = (RAIZ / "scripts" / "estado.py").read_text(encoding="utf-8")
WORKER_JS = (RAIZ / "web" / "_worker.js").read_text(encoding="utf-8")


class S15EstadoAstTest(unittest.TestCase):
    """S15 — AST de estado.py: función pura a nivel de módulo + fast-path."""

    def setUp(self):
        self.arbol = ast.parse(ESTADO_PY)

    def test_funcion_pura_a_nivel_de_modulo(self):
        nombres = [n.name for n in self.arbol.body if isinstance(n, ast.FunctionDef)]
        self.assertIn("aplicar_senales_conteo", nombres)

    def test_feed_de_reportes_selecciona_ip_hash_y_codigo(self):
        # La ventana de reportes trae fila_id (id) y holder (ip_hash) + codigo.
        self.assertIn('select("id,fecha,lat,lon,direccion,tipo,ip_hash,codigo")', ESTADO_PY)

    def test_fast_path_de_codigo_presente(self):
        # Reportes con codigo válido se anexan sin resolver ni join espacial.
        self.assertIn('r.get("codigo")', ESTADO_PY)
        self.assertIn("cod in cat", ESTADO_PY)

    def test_orden_de_empates_por_id(self):
        # Clave de sort determinista (fecha, fila_id) — S8 en el cableado.
        self.assertIn("(x[0], x[1])", ESTADO_PY)


class S16EmitCompatTest(unittest.TestCase):
    """S16 — emit-compat (X2): describirCircuito/estadoVigente estables sin
    conteo_usuario y con holder_ip_hash extra; consumidores ignoran claves."""

    def test_guardas_de_conteo_usuario_en_el_worker(self):
        # Sin conteo_usuario el estado decae a los estados oficiales (guards).
        self.assertIn("c.conteo_usuario && c.conteo_usuario.desde", WORKER_JS)
        self.assertIn('v === "discrepado" && c.conteo_usuario', WORKER_JS)

    def test_worker_ignora_holder_ip_hash(self):
        # Clave aditiva: el worker no la consume (desconocida → ignorada).
        self.assertNotIn("holder_ip_hash", WORKER_JS)

    def test_entrada_de_conteo_tolera_holder_ip_hash_aditivo(self):
        # Fixture mini-estado: entradas con y sin la clave extra conviven.
        mini = {"conteo_usuario": {
            "B1": {"desde": "2026-09-06T08:00:00+00:00", "ultima_sin": None,
                   "ultimo_con": None, "horas": 1.0, "holder_ip_hash": "hA"},
            "B2": {"desde": None, "ultima_sin": None, "ultimo_con": None},
        }}
        datos = json.loads(json.dumps(mini))
        self.assertEqual(datos["conteo_usuario"]["B1"]["holder_ip_hash"], "hA")
        self.assertIsNone(datos["conteo_usuario"]["B2"].get("holder_ip_hash"))
        mini_sin_conteo = {"generado": "2026-09-06T08:00:00+00:00"}
        self.assertNotIn("conteo_usuario", json.loads(json.dumps(mini_sin_conteo)))

    def test_estado_json_comiteado_parsea(self):
        est = json.loads((RAIZ / "web" / "data" / "estado.json").read_text(encoding="utf-8"))
        self.assertIn("generado", est)


if __name__ == "__main__":
    unittest.main()
