"""Cliente común para NaN Builders, NVIDIA NIM y Cloudflare Workers AI.

NaN es el proveedor preferido cuando existe ``NAN_API_KEY``. NVIDIA queda como
respaldo y Cloudflare como último recurso. Las llamadas a NaN NO tienen cuota
diaria. Las llamadas a NVIDIA/Cloudflare se contabilizan en llm_cuota.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request

import llm_cuota

NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NAN_BASE_URL = os.environ.get("NAN_BASE_URL", "https://api.nan.builders/v1")


def proveedor_preferido():
    pedido = os.environ.get("LLM_PROVIDER", "auto").strip().lower()
    if pedido in ("nan", "nvidia", "cloudflare"):
        return pedido
    if os.environ.get("NAN_API_KEY"):
        return "nan"
    return "nvidia" if os.environ.get("NVIDIA_API_KEY") else "cloudflare"


def _orden_proveedores():
    orden = ["nan", "nvidia", "cloudflare"]
    preferido = proveedor_preferido()
    return [p for p in orden if p == preferido] + [p for p in orden if p != preferido]


def _disponible(proveedor):
    if proveedor == "nan":
        return bool(os.environ.get("NAN_API_KEY"))
    if proveedor == "nvidia":
        return bool(os.environ.get("NVIDIA_API_KEY"))
    return bool(os.environ.get("CLOUDFLARE_ACCOUNT_ID") and
                os.environ.get("CLOUDFLARE_AI_TOKEN"))


def _nan(messages, modelo, timeout):
    body = json.dumps({
        "model": modelo,
        "messages": messages,
        "temperature": 0,
        "max_tokens": 4096,
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        f"{NAN_BASE_URL}/chat/completions", data=body,
        headers={"Authorization": f"Bearer {os.environ['NAN_API_KEY']}",
                 "Content-Type": "application/json"},
    )
    data = json.load(urllib.request.urlopen(req, timeout=timeout))
    return data["choices"][0]["message"]["content"]


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


def _llamar(proveedor, messages, modelo, timeout):
    if proveedor == "nan":
        return _nan(messages, modelo, timeout)
    if proveedor == "nvidia":
        return _nvidia(messages, modelo, timeout)
    return _cloudflare(messages, modelo, timeout)


def extraer_json(messages, quien, modelos, timeout=90):
    """Devuelve ``(objeto, info)`` probando NaN, luego NVIDIA y luego Cloudflare.

    ``modelos`` es ``{"nan": ..., "nvidia": ..., "cloudflare": ...}``. ``info``
    registra proveedor/modelo e intentos incluso cuando no se obtiene JSON
    válido. NaN no tiene cuota ni sleep; NVIDIA/Cloudflare sí se contabilizan.
    """
    info = {"proveedor": None, "modelo": None, "intentos": 0, "errores": []}
    for proveedor in _orden_proveedores():
        modelo = modelos.get(proveedor)
        if not modelo or not _disponible(proveedor):
            continue
        if proveedor != "nan" and not llm_cuota.puede(quien, proveedor):
            continue
        for intento in range(2):
            info["intentos"] += 1
            if proveedor != "nan":
                llm_cuota.registrar(quien, proveedor=proveedor)
            try:
                salida = _llamar(proveedor, messages, modelo, timeout)
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
