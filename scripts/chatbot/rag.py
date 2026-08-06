"""Motor RAG para el bot de Telegram. Usa NaN Builders:
  - qwen3-embedding: búsqueda semántica
  - rerank: re-ranking de resultados
  - deepseek-v4-flash: generación de respuesta

Flujo:
  1. Embed la consulta del usuario
  2. Coseno-similitud contra embeddings precomputados → top 20
  3. Rerank con NaN rerank → top 5
  4. deepseek genera respuesta con los fragmentos como contexto
"""

import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RAIZ = os.path.join(os.path.dirname(__file__), "..", "..")
EMBED_FILE = os.path.join(RAIZ, "web", "data", "chatbot_embeddings.json")
META_FILE = os.path.join(RAIZ, "web", "data", "chatbot_metadata.json")

NAN_BASE_URL = os.environ.get("NAN_BASE_URL", "https://api.nanbuilders.ai/v1")
MODELO_EMBED = "qwen3-embedding"
MODELO_RERANK = "rerank"
MODELO_GEN = os.environ.get("MODELO_NAN_PARTES", "deepseek-v4-flash")


def _nan_api(path, body, api_key):
    req = urllib.request.Request(
        f"{NAN_BASE_URL}/{path}", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req, timeout=30))


def _coseno(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _cargar():
    embeddings = json.load(open(EMBED_FILE)) if os.path.exists(EMBED_FILE) else []
    metadata = json.load(open(META_FILE)) if os.path.exists(META_FILE) else {}
    if isinstance(metadata, list):
        metadata = {m["id"]: m for m in metadata}
    return embeddings, metadata


PROMPT = (
    "Eres el bot de Apagones Habana, un asistente que informa sobre el estado "
    "eléctrico de La Habana en tiempo real. Usa la información proporcionada "
    "para responder de forma precisa y concisa. Si no tienes datos relevantes, "
    "díselo al usuario y sugiérele /estado para ver el mapa o /suscribir para "
    "recibir alertas. Responde en español, informal pero informativo."
)


def responder(consulta, proveedor="nan"):
    api_key = os.environ.get("NAN_API_KEY") if proveedor == "nan" else \
              os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        return "No hay proveedor LLM disponible."

    embeddings, metadata = _cargar()
    if not embeddings:
        return "Todavía no hay datos indexados. Prueba más tarde."

    # 1. Embed consulta
    try:
        resp = _nan_api("embeddings", {
            "model": MODELO_EMBED, "input": consulta[:512],
        }, api_key)
        vec_q = resp["data"][0]["embedding"]
    except Exception as e:
        return f"Error al procesar la consulta: {e}"

    # 2. Coseno-similitud
    scores = []
    for e in embeddings:
        sim = _coseno(vec_q, e["embedding"])
        scores.append((sim, e["id"]))
    scores.sort(reverse=True)
    top20 = scores[:20]

    # 3. Rerank
    docs = [metadata.get(eid, {}).get("texto", "") for _, eid in top20 if metadata.get(eid)]
    try:
        resp = _nan_api("rerank", {
            "model": MODELO_RERANK,
            "query": consulta,
            "documents": docs,
        }, api_key)
        rankings = resp.get("results", [])
        rankings.sort(key=lambda r: r.get("relevance_score", 0), reverse=True)
        top5_indices = [r["index"] for r in rankings[:5]]
        top5_ids = [top20[i][1] for i in top5_indices]
    except Exception:
        top5_ids = [eid for _, eid in top20[:5]]

    # 4. Generar respuesta
    contexto = []
    for eid in top5_ids:
        m = metadata.get(eid, {})
        fecha = (m.get("fecha") or "")[:16]
        texto = m.get("texto", "")
        contexto.append(f"[{fecha}] {texto}")
    contexto_str = "\n".join(contexto)

    messages = [
        {"role": "system", "content": PROMPT},
        {"role": "user", "content": f"Contexto:\n{contexto_str}\n\nPregunta: {consulta}"},
    ]
    try:
        resp = _nan_api("chat/completions", {
            "model": MODELO_GEN, "messages": messages,
            "temperature": 0.3, "max_tokens": 1024,
        }, api_key)
        return resp["choices"][0]["message"]["content"]
    except Exception as e:
        return f"Error al generar respuesta: {e}"


if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(responder(" ".join(sys.argv[1:])))
    else:
        print("Uso: python rag.py 'consulta del usuario'")