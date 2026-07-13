"""Enriquece los comentarios de vecinos con el LLM (Cloudflare Workers AI) para
extraer señal ubicable que las reglas no capturan: lugar, bloque, horas sin luz.
Los que reportan sin/con corriente y tienen un lugar geocodificable se guardan en
comentarios_llm con lat/lon, para pintarlos en el mapa como reportes vecinales.

Guardas de coste (Workers AI free = 10.000 neuronas/día):
  - solo comentarios recientes aún no procesados,
  - pre-filtro que descarta ruido obvio antes de gastar una llamada,
  - tope MAX_LLM por corrida.

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_AI_TOKEN
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from supabase import create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geocode_zonas import nominatim, normalizar, resolver_zonas_numeradas  # noqa: E402
import comentarios_reglas  # noqa: E402  (fallback determinista)
import llm_cuota  # noqa: E402  (presupuesto diario compartido con partes_llm)

BBOX_HABANA = (-82.70, 22.90, -81.90, 23.35)

MODELO = os.environ.get("MODELO", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
# El LLM es la BASE; MAX_LLM acota el gasto por corrida (cuota gratis ~10.000
# neuronas/día). Lo que exceda el tope, o si el LLM falla/agota cuota, lo procesa
# el fallback determinista (comentarios_reglas) — así nunca se pierde la señal.
MAX_LLM = int(os.environ.get("MAX_LLM", "8"))
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
    "'pregunta'=solo pregunta cuándo. 'queja'=protesta sin dato útil. 'irrelevante'=spam/saludo/config. "
    "'lugar' debe ser un topónimo real (reparto/calle), NO una frase ni la palabra 'bloque'."
)

# Pre-filtro: descarta lo que casi seguro no aporta señal ubicable, sin gastar LLM.
RUIDO = re.compile(r"^@|configura tu @username|bienvenid|para evitar ser silenciad", re.IGNORECASE)


def prometedor(texto):
    t = texto.strip()
    if len(t) < 12 or RUIDO.search(t):
        return False
    return True


def llm(texto, account, token):
    body = json.dumps({
        "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": texto[:800]}],
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{MODELO}",
        data=body, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    r = json.load(urllib.request.urlopen(req, timeout=60)).get("result", {})
    salida = r.get("response")
    if not isinstance(salida, str):
        try:
            salida = r["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None
    salida = salida.replace("```json", "").replace("```", "")
    m = re.search(r"\{.*\}", salida, re.DOTALL)
    try:
        return json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None


def geocodificar_lugar(lugar, osm, cache):
    """Ubica el lugar: regla Alamar -> catálogo OSM por nombre -> Nominatim (caché)."""
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
    account, token = os.environ["CLOUDFLARE_ACCOUNT_ID"], os.environ["CLOUDFLARE_AI_TOKEN"]
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
    catalogo = comentarios_reglas.catalogo_barrios()

    pendientes = [m for m in recientes if m["message_id"] not in ya and prometedor(m["texto"])]
    filas, usados_llm, por_reglas, llm_ok = [], 0, 0, True
    for m in pendientes:
        # BASE: reglas deterministas (sin cuota, sin red). Si resuelven el
        # comentario COMPLETO (reporta + lugar ubicado), no se gasta LLM.
        fila_r = comentarios_reglas.fila_determinista(m, catalogo)
        if fila_r and fila_r["lat"] is not None:
            filas.append(fila_r)
            por_reglas += 1
            continue

        # RESCATE con LLM: solo lo que las reglas no resolvieron del todo
        # (sin señal clara, o con señal pero sin lugar ubicable). El presupuesto
        # diario protege la cuota de los partes oficiales.
        r = None
        if llm_ok and usados_llm < MAX_LLM and llm_cuota.puede("comentarios"):
            try:
                llm_cuota.registrar("comentarios")
                r = llm(m["texto"], account, token)
                usados_llm += 1
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    print("Workers AI 429 (cuota): se sigue solo con reglas")
                    llm_ok = False
                    llm_cuota.marcar_agotada()
                else:
                    raise
            except Exception:
                llm_ok = False  # cualquier fallo del LLM -> solo reglas

        if isinstance(r, dict):
            fila = {
                "message_id": m["message_id"], "fecha": m["fecha"],
                "reporta": r.get("reporta"),
                "lugar": (r.get("lugar") or None),
                "bloque": r.get("bloque") if isinstance(r.get("bloque"), int) else None,
                "horas": r.get("horas_sin_luz") if isinstance(r.get("horas_sin_luz"), int) else None,
                "lat": None, "lon": None,
            }
            if fila["reporta"] in ("sin_corriente", "con_corriente") and fila["lugar"]:
                fila["lat"], fila["lon"] = geocodificar_lugar(fila["lugar"], osm, cache)
            # si el LLM tampoco ubicó pero las reglas tenían señal, gana la de reglas
            filas.append(fila if fila["lat"] is not None or not fila_r else fila_r)
        elif fila_r:
            filas.append(fila_r)  # sin LLM: la señal de reglas igual cuenta
            por_reglas += 1

    json.dump(cache, open(CACHE, "w"), ensure_ascii=False)
    if filas:
        sb.table("comentarios_llm").upsert(filas, on_conflict="message_id").execute()
    ubicados = sum(1 for f in filas if f["lat"])
    print(f"Comentarios: {len(filas)} guardados ({usados_llm} por LLM, {por_reglas} por reglas), {ubicados} ubicados")


if __name__ == "__main__":
    main()
