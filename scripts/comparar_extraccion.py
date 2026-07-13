"""Comparador regex vs LLM (plan tarea 3): para cada parte procesado por el LLM
en las últimas 24 h, extrae también los circuitos por el camino REGEX clásico y
compara. Coinciden -> confianza alta. Difieren -> se registra la discrepancia en
data/discrepancias_extraccion.json (la verificación diaria las reporta).

Así vemos qué formatos nuevos aparecen y cuál de los dos caminos falla, ANTES
de decidir a quién creerle en el pipeline (tarea 4).

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

from supabase import create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "extractor"))
import circuitos_id  # noqa: E402
from extract import zonas_en  # noqa: E402

RAIZ = os.path.join(os.path.dirname(__file__), "..")
LLM_FILE = os.path.join(RAIZ, "data", "partes_llm.json")
DISCREPANCIAS_FILE = os.path.join(RAIZ, "data", "discrepancias_extraccion.json")
RETENCION_DIAS = 7

# Códigos en el cuerpo del parte al estilo de build_circuitos/estado:
# bullets 👉/💥 y déficit "COD - N horas".
RE_DEFICIT = re.compile(r"([A-Za-z]{1,3}\d{1,4}|\d{3,4})\s*(?:\([^)]{2,30}\))?\s*-?\s*\d+\s*horas?")


def codigos_regex(texto):
    """Circuitos que el camino regex clásico ve en el post."""
    cods = set()
    for zona in zonas_en(texto):
        r = circuitos_id.resolver(zona)
        if r["via"] in ("codigo", "codigo_catalogo"):  # solo explícitos (comparable)
            cods.update(r["codigos"])
    for m in RE_DEFICIT.finditer(texto):
        for c in circuitos_id.normalizar_codigo(m.group(1)):
            cods.add(c)
    return cods


def main():
    try:
        llm_cache = json.load(open(LLM_FILE))
    except Exception:
        print("sin caché LLM aún; nada que comparar")
        return

    ahora = datetime.now(timezone.utc)
    corte = (ahora - timedelta(hours=24)).isoformat()
    pendientes = {mid: v for mid, v in llm_cache.items()
                  if v.get("via") == "llm" and (v.get("fecha") or "") >= corte}
    if not pendientes:
        print("sin partes LLM en 24 h; nada que comparar")
        return

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    textos = {}
    ids = list(pendientes)
    for i in range(0, len(ids), 100):
        for fila in (sb.table("mensajes").select("message_id,texto").eq("chat", "canal")
                     .in_("message_id", ids[i:i + 100]).execute().data):
            textos[str(fila["message_id"])] = fila["texto"] or ""

    try:
        registro = json.load(open(DISCREPANCIAS_FILE))
    except Exception:
        registro = {}

    iguales = distintos = 0
    for mid, v in pendientes.items():
        texto = textos.get(mid)
        if texto is None:
            continue
        del_llm = {c for item in v.get("circuitos") or [] for c in item.get("codigos") or []}
        del_regex = codigos_regex(texto)
        solo_llm = sorted(del_llm - del_regex)
        solo_regex = sorted(del_regex - del_llm)
        if solo_llm or solo_regex:
            distintos += 1
            registro[mid] = {
                "fecha": v.get("fecha"), "tipo_llm": v.get("tipo"),
                "solo_llm": solo_llm, "solo_regex": solo_regex,
                "extracto": re.sub(r"\s+", " ", texto)[:160],
            }
        else:
            iguales += 1
            registro.pop(mid, None)  # resuelta (p.ej. reprocesado)

    # retención: solo la última semana (el issue diario ya las reportó)
    corte_ret = (ahora - timedelta(days=RETENCION_DIAS)).isoformat()
    registro = {k: v for k, v in registro.items() if (v.get("fecha") or "") >= corte_ret}

    json.dump(registro, open(DISCREPANCIAS_FILE, "w"), ensure_ascii=False, indent=1)
    print(f"comparación: {iguales} coinciden, {distintos} con discrepancias "
          f"({len(registro)} registradas en total)")


if __name__ == "__main__":
    main()
