"""Presupuesto DIARIO por proveedor entre partes_llm y comentarios_llm.

Estado en data/llm_cuota.json (lo commitea el cron, mismo patrón que las cachés):
  {"dia": "YYYY-MM-DD", "proveedores": {"cloudflare": {...}, "nvidia": {...}}}
Se resetea solo al cambiar el día UTC (la cuota de Cloudflare también).

Uso:
  import llm_cuota
  if llm_cuota.puede("comentarios", "nvidia"): ...
  llm_cuota.registrar("comentarios", 3, proveedor="nvidia")
  llm_cuota.marcar_agotada("cloudflare")
"""

import json
import os
from datetime import datetime, timezone

RAIZ = os.path.join(os.path.dirname(__file__), "..")
ARCHIVO = os.path.join(RAIZ, "data", "llm_cuota.json")

# Topes de llamadas/día. Los partes son pocos y prioritarios (corren primero en
# el pipeline y con el tope grande); los comentarios van con reglas primero y
# LLM solo de rescate, así que un tope chico les alcanza.
TOPES = {"partes": 600, "comentarios": 250}


def _hoy():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _leer():
    try:
        with open(ARCHIVO) as f:
            d = json.load(f)
    except Exception:
        d = {}
    if d.get("dia") != _hoy():  # nuevo día UTC = cuota fresca
        d = {"dia": _hoy(), "proveedores": {}}
    elif "proveedores" not in d:
        # Migra el formato anterior, que correspondía exclusivamente a Cloudflare.
        d = {"dia": d["dia"], "proveedores": {"cloudflare": {
            "partes": d.get("partes", 0),
            "comentarios": d.get("comentarios", 0),
            "agotada": d.get("agotada", False),
        }}}
    return d


def _guardar(d):
    os.makedirs(os.path.dirname(ARCHIVO), exist_ok=True)
    with open(ARCHIVO, "w") as f:
        json.dump(d, f)


def _estado(d, proveedor):
    return d["proveedores"].setdefault(
        proveedor, {"partes": 0, "comentarios": 0, "agotada": False})


def puede(quien, proveedor="cloudflare"):
    d = _leer()
    p = _estado(d, proveedor)
    return not p.get("agotada") and p.get(quien, 0) < TOPES[quien]


def restante(quien, proveedor="cloudflare"):
    d = _leer()
    p = _estado(d, proveedor)
    return 0 if p.get("agotada") else max(0, TOPES[quien] - p.get(quien, 0))


def registrar(quien, n=1, proveedor="cloudflare"):
    d = _leer()
    p = _estado(d, proveedor)
    p[quien] = p.get(quien, 0) + n
    _guardar(d)


def marcar_agotada(proveedor="cloudflare"):
    d = _leer()
    _estado(d, proveedor)["agotada"] = True
    _guardar(d)
