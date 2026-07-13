"""Presupuesto DIARIO compartido de Workers AI entre partes_llm (prioridad) y
comentarios_llm (mejor esfuerzo), para que los comentarios no dejen sin cuota a
los partes oficiales (pasó: 429 todo el día por ~1.100 llamadas de comentarios).

Estado en data/llm_cuota.json (lo commitea el cron, mismo patrón que las cachés):
  {"dia": "YYYY-MM-DD", "partes": n, "comentarios": n, "agotada": true|false}
Se resetea solo al cambiar el día UTC (la cuota de Cloudflare también).

Uso:
  import llm_cuota
  if llm_cuota.puede("comentarios"): ...  # respeta tope diario y bandera agotada
  llm_cuota.registrar("comentarios", 3)   # suma llamadas hechas
  llm_cuota.marcar_agotada()              # ante 429 definitivo: nadie más insiste hoy
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
        d = json.load(open(ARCHIVO))
    except Exception:
        d = {}
    if d.get("dia") != _hoy():  # nuevo día UTC = cuota fresca
        d = {"dia": _hoy(), "partes": 0, "comentarios": 0, "agotada": False}
    return d


def _guardar(d):
    os.makedirs(os.path.dirname(ARCHIVO), exist_ok=True)
    json.dump(d, open(ARCHIVO, "w"))


def puede(quien):
    d = _leer()
    return not d.get("agotada") and d.get(quien, 0) < TOPES[quien]


def restante(quien):
    d = _leer()
    return 0 if d.get("agotada") else max(0, TOPES[quien] - d.get(quien, 0))


def registrar(quien, n=1):
    d = _leer()
    d[quien] = d.get(quien, 0) + n
    _guardar(d)


def marcar_agotada():
    d = _leer()
    d["agotada"] = True
    _guardar(d)
