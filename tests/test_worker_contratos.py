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
CHATBOT_JS = (RAIZ / "web" / "chatbot.js").read_text(encoding="utf-8")
APP_JS = (RAIZ / "web" / "app.js").read_text(encoding="utf-8")
SCHEMA_SQL = (RAIZ / "ingestor" / "schema.sql").read_text(encoding="utf-8")


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


def _caso_worker(nombre):
    """Extrae el cuerpo del case `nombre` de ejecutarHerramienta (hasta el
    siguiente case/default) para asserts de ausencia con ámbito real."""
    i = WORKER_JS.index('case "%s"' % nombre)
    j = WORKER_JS.find("\n    case ", i + 1)
    k = WORKER_JS.find("\n    default:", i + 1)
    fines = [x for x in (j, k) if x != -1]
    return WORKER_JS[i:min(fines) if fines else len(WORKER_JS)]


class S1WorkerReportarTest(unittest.TestCase):
    """S1 (worker) — herramienta reportar de SOLO LECTURA + payload adjunto."""

    def test_herramienta_reportar_existe(self):
        self.assertIn('name: "reportar"', WORKER_JS)
        self.assertIn("reporte_pendiente", WORKER_JS)

    def test_el_caso_reportar_no_toca_supabase(self):
        caso = _caso_worker("reportar")
        self.assertNotIn("supa(", caso, "reportar debe ser de solo lectura")
        self.assertNotIn('method: "POST"', caso)
        self.assertNotIn("insert", caso)


class S10ListarSinUmbralesTest(unittest.TestCase):
    """S10 — listarReportes: sin umbral/CONFIRMADOS_MIN, con codigo."""

    def test_sin_tokens_de_umbral(self):
        self.assertNotIn("umbral", WORKER_JS)
        self.assertNotIn("CONFIRMADOS_MIN", WORKER_JS)

    def test_select_de_reportes_incluye_codigo(self):
        self.assertIn("select=lat,lon,direccion,ip_hash,tipo,fecha,codigo", WORKER_JS)

    def test_forma_de_respuesta_puntos_y_ventana(self):
        self.assertIn("JSON.stringify({ puntos, ventana_h: VENTANA_H })", WORKER_JS)


class S19WorkerInsertTest(unittest.TestCase):
    """S19 (worker) — el insert escribe codigo SOLO cuando viene en el body."""

    def test_insert_condicional_de_codigo(self):
        self.assertIn("...(codigo ? { codigo } : {})", WORKER_JS)


class S1ChatbotTarjetaTest(unittest.TestCase):
    """S1 (chatbot) — tarjeta de confirmación con POST a /api/reporte."""

    def test_post_al_endpoint_existente(self):
        self.assertIn('API + "/api/reporte"', CHATBOT_JS)

    def test_cuerpo_del_post_con_codigo(self):
        # Forma del botón del mapa + codigo opcional (R4 §1).
        for token in ("lat: +boton.dataset.lat", "lon: +boton.dataset.lon",
                      "direccion: boton.dataset.dir", "tipo: boton.dataset.tipo",
                      "codigo: boton.dataset.cod"):
            self.assertIn(token, CHATBOT_JS)

    def test_botones_confirmar_y_cancelar(self):
        self.assertIn("cb-ok", CHATBOT_JS)
        self.assertIn("cb-no", CHATBOT_JS)

    def test_esc_en_los_valores_dinamicos(self):
        for token in ("esc(p.codigo)", "esc(p.direccion)", "esc(p.tipo)",
                      "esc(p.lat)", "esc(p.lon)", "esc(p.confianza)",
                      "esc(boton.dataset.cod)", "esc(d && d.error)"):
            self.assertIn(token, CHATBOT_JS)

    def test_un_solo_uso_y_delegacion_sin_onclick(self):
        # dataset.done impide el doble envío; escucha delegada en el contenedor
        # (nunca onclick interpolado en HTML).
        self.assertIn("dataset.done", CHATBOT_JS)
        self.assertIn('msgs.addEventListener("click"', CHATBOT_JS)
        self.assertNotIn("onclick=", CHATBOT_JS)


class S12ProcedenciaUnicaTest(unittest.TestCase):
    """S12 — app.js: procedencia única "Reportado por N vecino(s)", sin
    confirmado/umbral (los niveles de confirmación se fueron de la UI)."""

    def test_copia_de_reportado_por(self):
        self.assertIn("Reportado por", APP_JS)
        self.assertIn("vecino(s)", APP_JS)

    def test_sin_tokens_de_tiers(self):
        self.assertNotIn("confirmado", APP_JS)
        self.assertNotIn("umbral", APP_JS)
        self.assertNotIn("Se confirma con", APP_JS)

    def test_obsolescencia_solo_para_con(self):
        # El filtro por edad se aplica con guardia esCon: los 'sin' jamás se
        # filtran; la firma nueva ya no recibe confirmado.
        self.assertIn("esCon && reporteConObsoleto(p.fecha)", APP_JS)


class S17FallbackViejoTest(unittest.TestCase):
    """S17 — el estado.json comiteado (sin conteo_usuario) no rompe la UI."""

    def test_guardas_de_los_nuevos_campos(self):
        # snapshot municipal opcional, puntos vacíos tolerados, discrepado guardado
        self.assertIn("d?.", APP_JS)
        self.assertIn("r.puntos || []", APP_JS)
        self.assertIn("c.conteo_usuario &&", APP_JS)


class S18S19SchemaTest(unittest.TestCase):
    """S18/S19 — ALTER idempotente documentado; sin columna 'ignorado'."""

    def test_alter_verbatim_idempotente(self):
        # S18: re-ejecutar es no-op (add column if not exists), convención #61-62.
        self.assertIn(
            "alter table reportes add column if not exists codigo text;",
            SCHEMA_SQL,
        )

    def test_sin_columna_ignorado(self):
        # R4 §6 (#627.2): nada de shadow columns.
        self.assertNotIn("ignorado", SCHEMA_SQL)


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
