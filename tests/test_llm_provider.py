import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import llm_cuota  # noqa: E402
import llm_provider  # noqa: E402


class LlmProviderTest(unittest.TestCase):
    def test_nvidia_es_preferido_cuando_hay_clave(self):
        with mock.patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-test"}, clear=True):
            self.assertEqual(llm_provider.proveedor_preferido(), "nvidia")

    def test_peticion_nvidia_y_extraccion_json(self):
        respuesta = io.BytesIO(json.dumps({
            "choices": [{"message": {"content": "```json\n{\"tipo\": \"daf\"}\n```"}}]
        }).encode())
        with mock.patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-test"}, clear=True), \
                mock.patch.object(llm_provider.urllib.request, "urlopen", return_value=respuesta) as abrir, \
                mock.patch.object(llm_provider.llm_cuota, "puede", return_value=True), \
                mock.patch.object(llm_provider.llm_cuota, "registrar"):
            objeto, info = llm_provider.extraer_json(
                [{"role": "user", "content": "parte"}], "partes",
                {"nvidia": "openai/gpt-oss-120b"})
        req = abrir.call_args.args[0]
        cuerpo = json.loads(req.data)
        self.assertEqual(req.full_url, llm_provider.NVIDIA_URL)
        self.assertEqual(cuerpo["model"], "openai/gpt-oss-120b")
        self.assertEqual(objeto, {"tipo": "daf"})
        self.assertEqual(info["proveedor"], "nvidia")

    def test_cloudflare_agotado_no_bloquea_nvidia(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(llm_cuota, "ARCHIVO", str(Path(td) / "cuota.json")), \
                mock.patch.object(llm_cuota, "_hoy", return_value="2026-08-03"):
            Path(llm_cuota.ARCHIVO).write_text(json.dumps({
                "dia": "2026-08-03", "partes": 201,
                "comentarios": 47, "agotada": True,
            }))
            self.assertFalse(llm_cuota.puede("partes", "cloudflare"))
            self.assertTrue(llm_cuota.puede("partes", "nvidia"))
            llm_cuota.registrar("partes", proveedor="nvidia")
            estado = json.loads(Path(llm_cuota.ARCHIVO).read_text())
            self.assertEqual(estado["proveedores"]["cloudflare"]["partes"], 201)
            self.assertEqual(estado["proveedores"]["nvidia"]["partes"], 1)


if __name__ == "__main__":
    unittest.main()
