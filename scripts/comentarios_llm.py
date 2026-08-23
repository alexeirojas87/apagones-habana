"""Enriquece comentarios con NaN Builders (deepseek-v4-flash) para extraer
señal ubicable de reportes vecinales: lugar, bloque, horas sin luz, estado
corriente. Sin límite de cuota ni truncado — procesa todos los comentarios
recientes con deepseek, que entiende lenguaje natural y jerga cubana.

Los que reportan sin/con corriente y tienen un lugar geocodificable se guardan
en comentarios_llm con lat/lon, para pintarlos en el mapa como reportes
vecinales.

NVIDIA NIM y Cloudflare Workers AI quedan como respaldo por si NaN falla.

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, NAN_API_KEY
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

from supabase import create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geocode_zonas import nominatim, normalizar, resolver_zonas_numeradas  # noqa: E402
from circuitos_id import es_conocido  # noqa: E402
import llm_provider  # noqa: E402

BBOX_HABANA = (-82.70, 22.90, -81.90, 23.35)

MODELO_NAN = os.environ.get("MODELO_NAN_COMENTARIOS", "deepseek-v4-flash")
MODELO = os.environ.get("MODELO", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
MODELO_NVIDIA = os.environ.get("MODELO_NVIDIA_COMENTARIOS", "openai/gpt-oss-20b")
MAX_LLM = int(os.environ.get("MAX_LLM_COMENTARIOS", "50"))

# El tope de 50 acota el trabajo pero no el reloj: cada iteración puede sumar
# la llamada al LLM más una geocodificación contra Nominatim. Medido en CI, el
# paso llegó a 6m57s y se comió la ventana del job. Igual que en partes_llm, lo
# que de verdad protege la ingesta es el presupuesto de tiempo.
MAX_SEGUNDOS = int(os.environ.get("MAX_SEGUNDOS_COMENTARIOS", "300"))
TIMEOUT_LLM = int(os.environ.get("TIMEOUT_LLM_COMENTARIOS", "45"))
VENTANA_H = 4
CACHE = os.path.join(os.path.dirname(__file__), "..", "data", "geocache_averias.json")

PROMPT = (
    "Eres un analista de reportes ciudadanos sobre apagones en La Habana. "
    "Cada mensaje es un comentario de un vecino. Devuelve SOLO un objeto JSON, sin texto extra:\n"
    '{"reporta": uno de ["sin_corriente","con_corriente","pregunta","queja","irrelevante"],\n'
    ' "lugar": nombre del reparto/barrio/calle que menciona, o null,\n'
    ' "bloque": entero 1-6 si lo menciona explícitamente, o null,\n'
    ' "horas_sin_luz": horas sin electricidad si lo dice o se deduce, o null}\n'
    "'sin_corriente'=afirma no tener luz. 'con_corriente'=dice que ya llegó. "
    "'pregunta'=solo pregunta cuándo. 'queja'=protesta sin dato útil. "
    "'irrelevante'=spam/saludo/config. Entiende jerga: 'esto está durísimo' -> sin_corriente, "
    "'gracias ya llegó el circo' -> con_corriente, 'se pasaron otra vez' -> queja. "
    "'lugar' debe ser un topónimo real (reparto/calle), NO una frase ni la palabra 'bloque'."
)

RUIDO = re.compile(r"^@|configura tu @username|bienvenid|para evitar ser silenciad", re.IGNORECASE)

# Código de circuito en texto libre: prefijo de letras + 1-4 dígitos (NX4, AL56).
# Los números puros son ambiguos en lenguaje libre ("300" puede ser cualquier cosa),
# así que solo se aceptan con prefijo alfabético.
RE_COD_COMENT = re.compile(r"\b([A-Za-z]{1,3})\s*(\d{1,4})\b")


def extraer_codigos(texto):
    """Códigos de circuito conocidos que aparecen en el texto de un comentario.
    Filtra por catálogo (es_conocido) para descartar ruido."""
    codigos = []
    for m in RE_COD_COMENT.finditer(texto or ""):
        cod = (m.group(1) + m.group(2)).upper().replace(" ", "")
        if es_conocido(cod) and cod not in codigos:
            codigos.append(cod)
    return codigos


def prometedor(texto):
    t = texto.strip()
    if len(t) < 12 or RUIDO.search(t):
        return False
    return True


def llm(texto):
    return llm_provider.extraer_json(
        [{"role": "system", "content": PROMPT},
         {"role": "user", "content": texto[:2000]}],
        "comentarios", {"nan": MODELO_NAN, "nvidia": MODELO_NVIDIA, "cloudflare": MODELO},
        timeout=TIMEOUT_LLM)


def geocodificar_lugar(lugar, osm, cache):
    alamar = resolver_zonas_numeradas(lugar)
    if alamar:
        return alamar["lat"], alamar["lon"]
    nodo = osm.get(normalizar(lugar))
    if nodo:
        return nodo["lat"], nodo["lon"]
    clave = f"COM|{lugar}"
    if clave not in cache:
        cache[clave] = nominatim(f"{lugar}, La Habana, Cuba", BBOX_HABANA)
        time.sleep(1.1)
    hit = cache[clave]
    return (hit["lat"], hit["lon"]) if hit else (None, None)


def main():
    ahora = datetime.now(timezone.utc)
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    desde = (ahora - timedelta(hours=VENTANA_H)).isoformat()

    recientes = (
        sb.table("mensajes").select("message_id,texto,fecha")
        .eq("chat", "comentarios").gte("fecha", desde)
        .order("message_id", desc=True).limit(400).execute().data
    )
    ya = {r["message_id"] for r in
          sb.table("comentarios_llm").select("message_id").gte("fecha", desde).execute().data}

    ruta = os.path.join(os.path.dirname(__file__), "..", "data", "barrios_osm.json")
    with open(ruta) as f:
        osm = {}
        for b in json.load(f):
            osm.setdefault(normalizar(b["nombre"]), b)
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}

    pendientes = [m for m in recientes if m["message_id"] not in ya and prometedor(m["texto"])]
    filas, llm_ok, procesados = [], True, 0
    inicio, corte = time.monotonic(), None
    for m in pendientes:
        if procesados >= MAX_LLM:
            corte = f"tope de {MAX_LLM} comentarios"
            break
        if time.monotonic() - inicio > MAX_SEGUNDOS:
            corte = f"presupuesto de {MAX_SEGUNDOS}s agotado"
            break
        r, uso = llm(m["texto"])
        if r is None:
            # Sin proveedor no tiene sentido seguir iterando la lista entera.
            llm_ok = False
            corte = "sin proveedor LLM disponible"
            break
        procesados += 1
        fila = {
            "message_id": m["message_id"], "fecha": m["fecha"],
            "reporta": r.get("reporta"),
            "lugar": (r.get("lugar") or None),
            "bloque": r.get("bloque") if isinstance(r.get("bloque"), int) else None,
            "horas": r.get("horas_sin_luz") if isinstance(r.get("horas_sin_luz"), int) else None,
            "lat": None, "lon": None,
            "codigos": extraer_codigos(m["texto"]),
        }
        if fila["reporta"] in ("sin_corriente", "con_corriente") and fila["lugar"]:
            fila["lat"], fila["lon"] = geocodificar_lugar(fila["lugar"], osm, cache)
        filas.append(fila)

    json.dump(cache, open(CACHE, "w"), ensure_ascii=False)
    if filas:
        sb.table("comentarios_llm").upsert(filas, on_conflict="message_id").execute()
    ubicados = sum(1 for f in filas if f["lat"])
    print(f"Comentarios: {len(filas)} guardados (max {MAX_LLM} por corrida), {ubicados} ubicados")
    if corte:
        print(f"Comentarios: corte por {corte}; "
              f"{len(pendientes) - procesados} quedan para la próxima corrida")


if __name__ == "__main__":
    main()