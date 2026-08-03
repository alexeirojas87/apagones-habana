"""Cliente común para NVIDIA NIM y Cloudflare Workers AI.

NVIDIA es el proveedor preferido cuando existe ``NVIDIA_API_KEY``. Cloudflare
queda como respaldo para que una caída, límite temporal o respuesta inválida de
NIM no interrumpa la extracción. Las llamadas se contabilizan por proveedor.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request

import llm_cuota

NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"


def proveedor_preferido():
    pedido = os.environ.get("LLM_PROVIDER", "auto").strip().lower()
    if pedido in ("nvidia", "cloudflare"):
        return pedido
    return "nvidia" if os.environ.get("NVIDIA_API_KEY") else "cloudflare"


def _orden_proveedores():
    primero = proveedor_preferido()
    return [primero, "cloudflare" if primero == "nvidia" else "nvidia"]


def _disponible(proveedor):
    if proveedor == "nvidia":
        return bool(os.environ.get("NVIDIA_API_KEY"))
    return bool(os.environ.get("CLOUDFLARE_ACCOUNT_ID") and
                os.environ.get("CLOUDFLARE_AI_TOKEN"))


def _nvidia(messages, modelo, timeout):
    body = json.dumps({
        "model": modelo,
        "messages": messages,
        "temperature": 0,
        "max_tokens": 2048,
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        NVIDIA_URL, data=body,
        headers={"Authorization": f"Bearer {os.environ['NVIDIA_API_KEY']}",
                 "Content-Type": "application/json"},
    )
    data = json.load(urllib.request.urlopen(req, timeout=timeout))
    return data["choices"][0]["message"]["content"]


def _cloudflare(messages, modelo, timeout):
    account = os.environ["CLOUDFLARE_ACCOUNT_ID"]
    body = json.dumps({"messages": messages, "temperature": 0}).encode()
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{modelo}",
        data=body,
        headers={"Authorization": f"Bearer {os.environ['CLOUDFLARE_AI_TOKEN']}",
                 "Content-Type": "application/json"},
    )
    data = json.load(urllib.request.urlopen(req, timeout=timeout)).get("result", {})
    salida = data.get("response")
    if isinstance(salida, str):
        return salida
    return data["choices"][0]["message"]["content"]


def _json_de_texto(salida):
    if not isinstance(salida, str):
        return None
    salida = salida.replace("```json", "").replace("```", "")
    m = re.search(r"\{.*\}", salida, re.DOTALL)
    try:
        return json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None


def extraer_json(messages, quien, modelos, timeout=90):
    """Devuelve ``(objeto, info)`` probando NVIDIA y luego Cloudflare.

    ``modelos`` es ``{"nvidia": ..., "cloudflare": ...}``. ``info`` registra
    proveedor/modelo e intentos incluso cuando no se obtiene JSON válido.
    """
    info = {"proveedor": None, "modelo": None, "intentos": 0, "errores": []}
    for proveedor in _orden_proveedores():
        modelo = modelos.get(proveedor)
        if not modelo or not _disponible(proveedor):
            continue
        if not llm_cuota.puede(quien, proveedor):
            continue
        for intento in range(2):
            info["intentos"] += 1
            llm_cuota.registrar(quien, proveedor=proveedor)
            try:
                salida = (_nvidia(messages, modelo, timeout) if proveedor == "nvidia"
                          else _cloudflare(messages, modelo, timeout))
                objeto = _json_de_texto(salida)
                if objeto is not None:
                    info.update(proveedor=proveedor, modelo=modelo)
                    return objeto, info
                info["errores"].append(f"{proveedor}: JSON inválido")
                break
            except urllib.error.HTTPError as e:
                info["errores"].append(f"{proveedor}: HTTP {e.code}")
                if e.code == 429 and intento == 0:
                    time.sleep(10)
                    continue
                if e.code == 429 and proveedor == "cloudflare":
                    llm_cuota.marcar_agotada(proveedor)
                break
            except Exception as e:
                info["errores"].append(f"{proveedor}: {type(e).__name__}")
                break
    return None, info
