"""Genera web/data/bloques_por_municipio.json a partir de data/bloques.json.

Estructura de salida: { municipio: { "1": [zonas...], "2": [...] } }
Se ejecuta a mano cuando cambie el PDF de bloques (no forma parte del cron).
"""

import json
import os

RAIZ = os.path.join(os.path.dirname(__file__), "..")

with open(os.path.join(RAIZ, "data", "bloques.json")) as f:
    datos = json.load(f)

salida = {}
for bloque in datos["bloques"]:
    n = str(bloque["bloque"])
    for grupo in bloque["municipios"]:
        salida.setdefault(grupo["municipio"], {})[n] = grupo["zonas"]

destino = os.path.join(RAIZ, "web", "data", "bloques_por_municipio.json")
with open(destino, "w") as f:
    json.dump(salida, f, ensure_ascii=False)

print(f"{len(salida)} municipios ->", destino)
