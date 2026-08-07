"""Indexa el histórico en Supabase/pgvector para la búsqueda semántica del bot.

Solo entra aquí el TEXTO LIBRE —partes oficiales y reportes vecinales—, que es
donde la similitud semántica gana: el usuario describe su zona con sus palabras
y no sabe el código del circuito. El estado actual y los conteos NO se indexan;
eso el worker lo responde con filtros exactos, porque la búsqueda vectorial
devuelve los k más parecidos y nunca garantiza completitud.

Es incremental: cada fragmento lleva el sha1 de su texto y solo se re-embebe lo
nuevo o lo que cambió. Y está acotado por cantidad y por reloj, como partes_llm,
para no volver a ser el paso que agota el timeout del workflow.

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, NAN_API_KEY
Requiere haber ejecutado ingestor/schema_chatbot.sql una vez.
"""

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from supabase import create_client

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RAIZ = os.path.join(os.path.dirname(__file__), "..", "..")
CACHE_FILE = os.path.join(RAIZ, "data", "partes_llm.json")

NAN_BASE_URL = os.environ.get("NAN_BASE_URL", "https://api.nan.builders/v1")
MODELO_EMBED = os.environ.get("MODELO_EMBED", "qwen3-embedding")
# qwen3-embedding devuelve 4096 dimensiones por defecto (variante 8B), pero los
# índices ivfflat de pgvector solo admiten hasta 2000. El modelo es Matryoshka,
# así que se piden 1024 explícitamente: entra en el índice y ocupa 4x menos.
# El worker debe pedir la MISMA dimensión o los vectores no serán comparables.
DIM = int(os.environ.get("EMBED_DIM", "1024"))

DIAS_HISTORICO = int(os.environ.get("DIAS_HISTORICO_BOT", "90"))
HORAS_COMENTARIOS = 48
MAX_EMBEDS = int(os.environ.get("MAX_EMBEDS_BOT", "300"))
MAX_SEGUNDOS = int(os.environ.get("MAX_SEGUNDOS_BOT", "240"))
LOTE = 32  # fragmentos por llamada; el endpoint acepta lista en "input"


def _sha1(texto):
    return hashlib.sha1(texto.encode("utf-8")).hexdigest()


def embed_lote(textos, api_key):
    """Embebe hasta LOTE textos en una llamada. Devuelve lista de vectores."""
    body = json.dumps({
        "model": MODELO_EMBED,
        "input": [t[:2048] for t in textos],
        "dimensions": DIM,
    }).encode()
    req = urllib.request.Request(
        f"{NAN_BASE_URL}/embeddings", data=body,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json",
                 "User-Agent": "apagones-habana/1.0"},
    )
    data = json.load(urllib.request.urlopen(req, timeout=60))
    # El orden de "data" no está garantizado por la spec de OpenAI: se reordena
    # por "index" antes de emparejar cada vector con su fragmento.
    filas = sorted(data["data"], key=lambda d: d.get("index", 0))
    return [f["embedding"] for f in filas]


def fragmentos_partes():
    """Partes oficiales ya extraídos por LLM, como texto legible."""
    try:
        cache = json.load(open(CACHE_FILE))
    except Exception:
        return []

    limite = (datetime.now(timezone.utc) - timedelta(days=DIAS_HISTORICO)).isoformat()
    fragmentos = []
    for mid, v in cache.items():
        if v.get("via") != "llm":
            continue
        fecha = v.get("fecha") or ""
        if fecha < limite:
            continue
        circuitos = v.get("circuitos") or []
        codigos = [c for x in circuitos for c in (x.get("codigos") or [])]
        calles = "; ".join(filter(None, ((x.get("calles") or "").strip() for x in circuitos)))
        municipios = sorted({(x.get("municipio") or "").strip()
                             for x in circuitos if x.get("municipio")})
        tipo = v.get("tipo") or "otro"

        # Un parte sin zona, sin circuito, sin municipio y sin cifra no dice
        # nada recuperable ("Parte oficial del 10-07: afectacion.") y encima
        # compite por un hueco del top-k contra fragmentos que sí informan.
        if not (codigos or calles or municipios or v.get("mw_deficit")):
            continue

        # Redactado en prosa: el embedding captura mejor una frase natural que
        # una lista de campos, y es también lo que leerá el LLM como contexto.
        partes_txt = [f"Parte oficial del {fecha[:16].replace('T', ' ')}: {tipo}."]
        if municipios:
            partes_txt.append(f"Municipios: {', '.join(municipios)}.")
        if calles:
            partes_txt.append(f"Zonas y calles afectadas: {calles}.")
        if codigos:
            partes_txt.append(f"Circuitos: {', '.join(codigos)}.")
        if v.get("mw_deficit"):
            partes_txt.append(f"Déficit: {v['mw_deficit']} MW.")
        texto = " ".join(partes_txt)

        fragmentos.append({
            "id": str(mid),
            "tipo": tipo,
            "fecha": fecha,
            "texto": texto,
            "metadatos": {"circuitos": codigos, "municipios": municipios,
                          "calles": calles[:500]},
        })
    return fragmentos


def fragmentos_comentarios(sb):
    """Reportes de vecinos: lenguaje libre, el caso donde el RAG más aporta."""
    desde = (datetime.now(timezone.utc) - timedelta(hours=HORAS_COMENTARIOS)).isoformat()
    filas = (sb.table("comentarios_llm")
             .select("message_id,fecha,reporta,lugar,horas")
             .gte("fecha", desde).limit(500).execute().data)
    fragmentos = []
    for c in filas:
        lugar = (c.get("lugar") or "").strip()
        if not lugar:
            continue  # sin lugar no es recuperable por zona: no aporta al índice
        estado = {"sin_corriente": "sin corriente",
                  "con_corriente": "con corriente"}.get(c.get("reporta"), c.get("reporta") or "")
        horas = f" Lleva unas {c['horas']} horas." if c.get("horas") else ""
        texto = (f"Reporte vecinal del {(c.get('fecha') or '')[:16].replace('T', ' ')} "
                 f"en {lugar}: {estado}.{horas}")
        fragmentos.append({
            "id": f"com_{c['message_id']}",
            "tipo": "reporte",
            "fecha": c["fecha"],
            "texto": texto,
            "metadatos": {"lugar": lugar, "reporta": c.get("reporta")},
        })
    return fragmentos


def hashes_existentes(sb):
    """id -> hash de lo ya indexado, para no re-embeber lo que no cambió."""
    out, desde = {}, 0
    while True:
        filas = (sb.table("chatbot_fragmentos").select("id,hash")
                 .range(desde, desde + 999).execute().data)
        if not filas:
            break
        out.update({f["id"]: f["hash"] for f in filas})
        if len(filas) < 1000:
            break
        desde += 1000
    return out


def main():
    api_key = os.environ.get("NAN_API_KEY")
    if not api_key:
        print("chatbot_embeddings: sin NAN_API_KEY, se omite")
        return

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

    try:
        previos = hashes_existentes(sb)
    except Exception as e:
        print(f"chatbot_embeddings: no se pudo leer el índice ({e}). "
              "¿Ejecutaste ingestor/schema_chatbot.sql? Se omite.")
        return

    fragmentos = fragmentos_partes() + fragmentos_comentarios(sb)
    for f in fragmentos:
        f["hash"] = _sha1(f["texto"])
    pendientes = [f for f in fragmentos if previos.get(f["id"]) != f["hash"]]

    print(f"chatbot_embeddings: {len(fragmentos)} fragmentos, "
          f"{len(pendientes)} por indexar ({len(previos)} ya en el índice)")
    if not pendientes:
        return

    inicio = time.monotonic()
    subidos = fallos = 0
    corte = None
    for i in range(0, min(len(pendientes), MAX_EMBEDS), LOTE):
        if time.monotonic() - inicio > MAX_SEGUNDOS:
            corte = f"presupuesto de {MAX_SEGUNDOS}s agotado"
            break
        lote = pendientes[i:i + LOTE]
        try:
            vectores = embed_lote([f["texto"] for f in lote], api_key)
        except Exception as e:
            print(f"  fallo al embeber lote {i // LOTE}: {e}")
            fallos += 1
            if fallos >= 3:
                corte = "3 lotes fallidos seguidos"
                break
            continue
        if len(vectores) != len(lote):
            print(f"  lote {i // LOTE}: se pidieron {len(lote)} vectores y "
                  f"llegaron {len(vectores)}; se omite")
            fallos += 1
            continue
        if vectores and len(vectores[0]) != DIM:
            print(f"ABORTA: el modelo devuelve vectores de {len(vectores[0])} "
                  f"dimensiones y el schema espera {DIM}. Ajusta vector({DIM}) "
                  "en ingestor/schema_chatbot.sql (los dos sitios) y re-ejecútalo.")
            return
        filas = [{"id": f["id"], "tipo": f["tipo"], "fecha": f["fecha"],
                  "texto": f["texto"], "metadatos": f["metadatos"],
                  "hash": f["hash"], "embedding": v}
                 for f, v in zip(lote, vectores)]
        try:
            sb.table("chatbot_fragmentos").upsert(filas).execute()
        except Exception as e:
            print(f"  fallo al subir lote {i // LOTE}: {e}")
            fallos += 1
            continue
        fallos = 0
        subidos += len(filas)

    if len(pendientes) > MAX_EMBEDS and not corte:
        corte = f"tope de {MAX_EMBEDS} por corrida"
    print(f"chatbot_embeddings: {subidos} fragmentos indexados")
    if corte:
        print(f"chatbot_embeddings: corte por {corte}; "
              f"{len(pendientes) - subidos} quedan para la próxima corrida")


if __name__ == "__main__":
    main()
