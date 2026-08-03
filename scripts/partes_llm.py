"""Extractor LLM de PARTES OFICIALES (NVIDIA NIM + respaldo Cloudflare): entiende el
contenido del parte aunque la UNE cambie la redacción/emojis/formato, que es lo
que rompe los regex una y otra vez.

Diseño (docs/plan-extraccion-llm.md, tarea 2):
  - Cada post del canal se procesa UNA sola vez: caché data/partes_llm.json por
    message_id (mismo patrón que las geocachés; la commitea el cron).
  - Salida JSON estricta por post: tipo, circuitos (codigo/calles/municipio/
    estado/horas/causa), bloques, mw_deficit, pct_restablecido.
  - Todo código pasa por el resolutor (circuitos_id): se normaliza; si no es
    conocido y tampoco casa por calles -> va a 'por_confirmar' (mitiga
    alucinaciones del LLM). Si el LLM no da código pero sí calles, se intenta
    casar por calles.
  - MAX_LLM por corrida acota el gasto (free tier). Si el LLM falla, el post
    queda sin procesar y se reintenta en la próxima corrida (los regex de
    estado.py siguen siendo la base mientras tanto — tarea 4 invierte eso).

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, NVIDIA_API_KEY y credenciales Cloudflare
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

from supabase import create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import circuitos_id  # noqa: E402
import llm_provider  # noqa: E402

RAIZ = os.path.join(os.path.dirname(__file__), "..")
CACHE_FILE = os.path.join(RAIZ, "data", "partes_llm.json")

MODELO = os.environ.get("MODELO_PARTES", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
MODELO_NVIDIA = os.environ.get("MODELO_NVIDIA_PARTES", "openai/gpt-oss-120b")
MAX_LLM = int(os.environ.get("MAX_LLM_PARTES", "15"))
VENTANA_H = 24

PROMPT = (
    "Eres un analista de partes OFICIALES de la Empresa Eléctrica de La Habana "
    "sobre apagones. Extrae los datos del parte. Devuelve SOLO un objeto JSON, sin texto extra:\n"
    '{"tipo": uno de ["afectacion","restablecimiento","averia","deficit","caida_sen","daf","otro"],\n'
    ' "circuitos": [{"codigo": "P318" o null si no lo dice,\n'
    '               "calles": "texto de calles/zonas/repartos" o null,\n'
    '               "municipio": nombre del municipio si lo dice, o null,\n'
    '               "estado": "con servicio" | "sin servicio" | null,\n'
    '               "horas": horas de afectación acumuladas (número) o null,\n'
    '               "causa": "déficit" | "avería" | "DAF" | "emergencia" | null}],\n'
    ' "bloques": [enteros 1-6 mencionados como bloques afectados],\n'
    ' "mw_deficit": MW de déficit si los menciona (número) o null,\n'
    ' "pct_restablecido": % restablecido si lo menciona (número) o null}\n'
    "Reglas: un código de circuito es letras+números (P318, AL56, CPP20) o un número "
    "de 3-4 cifras pegado a las calles (1243). Los números de una lista de ZONAS "
    "(Zonas: 13; 15...) NO son códigos. 'tipo' refleja el propósito principal del "
    "parte. En restablecimientos, estado='con servicio'; en afectaciones/averías/"
    "déficit, estado='sin servicio'. Incluye TODOS los circuitos mencionados."
)

# Pre-filtro: solo posts que parecen partes con datos (evita gastar en saludos).
RELEVANTE = re.compile(
    r"circuito|bloque|afectaci|restablec|aver[ií]a|d[eé]ficit|desconexi|MW|disparo",
    re.IGNORECASE)


def llm(texto):
    return llm_provider.extraer_json(
        [{"role": "system", "content": PROMPT},
         {"role": "user", "content": texto[:2500]}],
        "partes", {"nvidia": MODELO_NVIDIA, "cloudflare": MODELO}, timeout=90)


def validar(extraccion):
    """Normaliza y valida la salida del LLM con el resolutor de identidad.
    Códigos desconocidos que tampoco casan por calles -> por_confirmar."""
    if not isinstance(extraccion, dict):
        return None
    out = {
        "tipo": extraccion.get("tipo") if extraccion.get("tipo") in (
            "afectacion", "restablecimiento", "averia", "deficit",
            "caida_sen", "daf", "otro") else "otro",
        "circuitos": [], "por_confirmar": [],
        "bloques": [b for b in (extraccion.get("bloques") or [])
                    if isinstance(b, int) and 1 <= b <= 6],
    }
    for k in ("mw_deficit", "pct_restablecido"):
        v = extraccion.get(k)
        out[k] = v if isinstance(v, (int, float)) else None
    for c in (extraccion.get("circuitos") or []):
        if not isinstance(c, dict):
            continue
        cods = circuitos_id.normalizar_codigo(c.get("codigo") or "")
        calles = (c.get("calles") or "").strip() or None
        if not cods and calles:
            cod, conf = circuitos_id.casar_por_calles(calles)
            if cod:
                cods = [cod]
        horas = c.get("horas")
        item = {
            "codigos": cods, "calles": calles,
            "municipio": (c.get("municipio") or "").strip() or None,
            "estado": c.get("estado") if c.get("estado") in
                      ("con servicio", "sin servicio") else None,
            "horas": horas if isinstance(horas, (int, float)) else None,
            "causa": (c.get("causa") or "").strip() or None,
        }
        out["circuitos"].append(item)
        out["por_confirmar"] += [x for x in cods if not circuitos_id.es_conocido(x)]
    return out


def main():
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    cache = {}
    if os.path.exists(CACHE_FILE):
        try:
            cache = json.load(open(CACHE_FILE))
        except Exception:
            cache = {}

    desde = (datetime.now(timezone.utc) - timedelta(hours=VENTANA_H)).isoformat()
    posts = (sb.table("mensajes").select("message_id,fecha,texto")
             .eq("chat", "canal").gte("fecha", desde)
             .order("fecha", desc=True).limit(200).execute().data)

    nuevos = fallos = 0
    for p in posts:
        mid = str(p["message_id"])
        if mid in cache:
            continue
        if not RELEVANTE.search(p["texto"] or ""):
            cache[mid] = {"fecha": p["fecha"], "tipo": "otro", "circuitos": [],
                          "por_confirmar": [], "bloques": [],
                          "mw_deficit": None, "pct_restablecido": None,
                          "via": "prefiltro"}
            continue
        if nuevos >= MAX_LLM:
            continue  # tope por corrida; la próxima sigue donde quedó
        crudo, uso = llm(p["texto"])
        if crudo is None:
            print(f"LLM falló en {mid}: {', '.join(uso['errores']) or 'sin proveedor disponible'}")
            fallos += 1
            if fallos >= 3:
                break  # cuota agotada o servicio caído: no insistir esta corrida
            continue
        time.sleep(1.5)  # respeta el límite por minuto del free tier
        valido = validar(crudo)
        if valido is None:
            fallos += 1
            continue
        cache[mid] = {"fecha": p["fecha"], **valido, "via": "llm",
                      "proveedor": uso["proveedor"], "modelo": uso["modelo"]}
        nuevos += 1

    json.dump(cache, open(CACHE_FILE, "w"), ensure_ascii=False)
    tot_circ = sum(len(v.get("circuitos") or []) for v in cache.values())
    print(f"partes_llm: {nuevos} posts nuevos procesados, {len(cache)} en caché, "
          f"{tot_circ} circuitos extraídos, {fallos} fallos")


if __name__ == "__main__":
    main()
