"""Genera embeddings del histórico de partes con qwen3-embedding de NaN.

Se ejecuta diariamente en el pipeline (build_analitica). Produce:
  - web/data/chatbot_embeddings.json: fragmentos con embedding vector
  - web/data/chatbot_metadata.json: fragmentos sin vector (para el worker)

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, NAN_API_KEY
"""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from supabase import create_client

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RAIZ = os.path.join(os.path.dirname(__file__), "..", "..")
CACHE_FILE = os.path.join(RAIZ, "data", "partes_llm.json")
EMBED_FILE = os.path.join(RAIZ, "web", "data", "chatbot_embeddings.json")
META_FILE = os.path.join(RAIZ, "web", "data", "chatbot_metadata.json")

NAN_BASE_URL = os.environ.get("NAN_BASE_URL", "https://api.nanbuilders.ai/v1")
MODELO_EMBED = os.environ.get("MODELO_EMBED", "qwen3-embedding")
DIAS_HISTORICO = 90  # últimos 90 días


def embed(texto, api_key):
    body = json.dumps({
        "model": MODELO_EMBED,
        "input": texto[:2048],
    }).encode()
    req = urllib.request.Request(
        f"{NAN_BASE_URL}/embeddings", data=body,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
    )
    data = json.load(urllib.request.urlopen(req, timeout=30))
    return data["data"][0]["embedding"]


def fragmentos_partes():
    partes_cache = {}
    if os.path.exists(CACHE_FILE):
        try:
            partes_cache = json.load(open(CACHE_FILE))
        except Exception:
            pass
    fragmentos = []
    for mid, v in partes_cache.items():
        if v.get("via") != "llm":
            continue
        fecha = v.get("fecha", "")
        tipo = v.get("tipo", "otro")
        circuitos = [c.get("codigos", []) for c in v.get("circuitos") or []]
        circuitos_flat = [c for sub in circuitos for c in sub]
        calles = [c.get("calles") or "" for c in v.get("circuitos") or []]
        calles_txt = "; ".join(filter(None, calles))
        texto = f"Tipo: {tipo}. Circuitos: {', '.join(circuitos_flat)}. Calles: {calles_txt}. Bloque: {v.get('bloques', [])}."
        fragmentos.append({
            "id": mid,
            "fecha": fecha,
            "tipo": tipo,
            "texto": texto,
            "circuitos": circuitos_flat,
            "bloques": v.get("bloques", []),
            "calles": calles_txt,
        })
    return fragmentos


def fragmentos_comentarios(sb):
    desde = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    comentarios = (sb.table("comentarios_llm").select("message_id,fecha,reporta,lugar,bloque,horas")
                   .gte("fecha", desde).limit(500).execute().data)
    fragmentos = []
    for c in comentarios:
        texto = f"Reporte: {c.get('reporta', '')}. Lugar: {c.get('lugar', '')}. Bloque: {c.get('bloque', '')}. Horas: {c.get('horas', '')}."
        fragmentos.append({
            "id": f"com_{c['message_id']}",
            "fecha": c["fecha"],
            "tipo": "reporte",
            "texto": texto,
            "reporta": c.get("reporta"),
            "lugar": c.get("lugar"),
            "bloque": c.get("bloque"),
        })
    return fragmentos


def main():
    api_key = os.environ.get("NAN_API_KEY")
    if not api_key:
        print("chatbot_embeddings: sin NAN_API_KEY, se omite")
        return

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    fragmentos = fragmentos_partes() + fragmentos_comentarios(sb)
    print(f"chatbot_embeddings: {len(fragmentos)} fragmentos a embedder")

    embeddings = []
    metadata = []
    for i, f in enumerate(fragmentos):
        try:
            vec = embed(f["texto"], api_key)
            embeddings.append({"id": f["id"], "embedding": vec})
            time.sleep(0.05)
        except Exception as e:
            print(f"  fallo en {f['id']}: {e}")
        meta = {k: v for k, v in f.items() if k != "embedding"}
        metadata.append(meta)

    json.dump(embeddings, open(EMBED_FILE, "w"))
    json.dump(metadata, open(META_FILE, "w"), ensure_ascii=False)
    print(f"chatbot_embeddings: {len(embeddings)} embeddings generados")


if __name__ == "__main__":
    main()