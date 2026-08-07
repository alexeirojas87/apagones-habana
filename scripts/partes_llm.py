"""Extractor LLM de PARTES OFICIALES con NaN Builders (deepseek-v4-flash).
NaN elimina las restricciones de cuota y contexto del diseño anterior, así que
procesamos los posts sin truncar y sin sleep.

Sí hay tope por corrida: el canal genera ~7 posts/hora (pico 21), así que un
tope de 50 nunca se alcanza en régimen normal y solo actúa al recuperar
backlog, repartiéndolo entre corridas. Sin él, un bump de VALIDADOR_VERSION
invalidaba la caché entera y la corrida agotaba el timeout del workflow.

NVIDIA NIM y Cloudflare Workers AI quedan como respaldo (fallback) por si
NaN no está disponible. La caché por message_id sigue vigente para no
reprocesar lo ya visto.
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

MODELO_NAN = os.environ.get("MODELO_NAN_PARTES", "deepseek-v4-flash")
MODELO = os.environ.get("MODELO_PARTES", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
MODELO_NVIDIA = os.environ.get("MODELO_NVIDIA_PARTES", "openai/gpt-oss-120b")
VENTANA_H = 24
VALIDADOR_VERSION = 3

# Topes por corrida. MAX_LLM acota el trabajo; MAX_SEGUNDOS acota el reloj, que
# es lo que de verdad protege el timeout del workflow: 50 posts contra un
# proveedor lento (timeout 120 s, tres en cadena) no caben en 20 minutos.
MAX_LLM = int(os.environ.get("MAX_LLM_PARTES", "50"))
MAX_SEGUNDOS = int(os.environ.get("MAX_SEGUNDOS_PARTES", "480"))

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
    "Reglas: extrae únicamente circuitos que el texto enumera como tales; no "
    "inventes ni deduzcas códigos a partir de nombres de lugares. Los nombres de "
    "centrales termoeléctricas o unidades de generación (por ejemplo Antonio "
    "Guiteras, Máximo Gómez, Carlos Manuel de Céspedes, Diez de Octubre, Felton o "
    "Ernesto Guevara) NO son circuitos. Un código de circuito es letras+números "
    "(P318, AL56, CPP20) o un número "
    "de 3-4 cifras pegado a las calles (1243). Los números de una lista de ZONAS "
    "(Zonas: 13; 15...) NO son códigos. 'tipo' refleja el propósito principal del "
    "parte. En restablecimientos, estado='con servicio'; en afectaciones/averías/"
    "déficit, estado='sin servicio'. Incluye TODOS los circuitos mencionados."
)

# Pre-filtro: solo posts que parecen partes con datos (evita gastar en saludos).
RELEVANTE = re.compile(
    r"circuito|bloque|afectaci|restablec|aver[ií]a|d[eé]ficit|desconexi|MW|disparo",
    re.IGNORECASE)

# Los partes de recuperación del SEN también hablan de "restablecimiento", pero
# arrancar una unidad de generación no restablece un circuito de distribución.
RE_GENERACION = re.compile(
    r"\bCTE\b|central(?:es)?\s+termoel[eé]ctrica|arranque\s+de\s+unidades?|"
    r"(?:iniciando|preparad[ao]s?\s+para)\s+(?:el\s+)?arranque|"
    r"(?:unidad|bloque)\s+\d+\s+(?:de\s+la\s+)?CTE|sincronizaci[oó]n\s+de\s+la\s+unidad",
    re.IGNORECASE)
RE_DISTRIBUCION = re.compile(r"\bcircuitos?\b", re.IGNORECASE)


def llm(texto):
    return llm_provider.extraer_json(
        [{"role": "system", "content": PROMPT},
         {"role": "user", "content": texto[:8000]}],
        "partes", {"nan": MODELO_NAN, "nvidia": MODELO_NVIDIA, "cloudflare": MODELO}, timeout=120)


def codigo_explicito_en(codigo, texto):
    """Verdadero si *codigo* está escrito como código de circuito en el post.

    Los códigos con letras son inequívocos. Los puramente numéricos requieren
    contexto para no confundir años, MW, calles o números de zonas.
    """
    codigo = str(codigo or "").upper()
    texto = texto or ""
    m = re.fullmatch(r"([A-Z]{1,3})(\d{1,4})", codigo)
    if m:
        patron = rf"(?<![A-Za-z0-9]){re.escape(m.group(1))}\s*{m.group(2)}(?![A-Za-z0-9])"
        return bool(re.search(patron, texto, re.IGNORECASE))
    if not re.fullmatch(r"\d{3,4}", codigo):
        return False
    patrones = (
        rf"\bcircuitos?\s*(?:n[oú]mero\s*)?(?:[:#-]\s*)?{codigo}\b",
        rf"(?m)^\s*[^\w\n]*{codigo}\s*[-–:]",
        rf"[A-Za-z]{{1,3}}\s*\d{{1,4}}\s*/\s*{codigo}\b",
        rf"\b{codigo}\s*/\s*[A-Za-z]{{1,3}}\s*\d{{1,4}}",
    )
    return any(re.search(p, texto, re.IGNORECASE) for p in patrones)


def validar(extraccion, texto=""):
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
    # Una noticia sobre CTE/unidades sin circuitos de distribución explícitos no
    # puede producir circuitos, aunque el LLM confunda nombres propios con calles.
    if RE_GENERACION.search(texto or "") and not RE_DISTRIBUCION.search(texto or ""):
        return out

    for c in (extraccion.get("circuitos") or []):
        if not isinstance(c, dict):
            continue
        cods = circuitos_id.normalizar_codigo(c.get("codigo") or "")
        calles = (c.get("calles") or "").strip() or None
        if not cods and calles:
            cod, conf = circuitos_id.casar_por_calles(calles)
            if cod:
                cods = [cod]
        codigos_estado = [cod for cod in cods if codigo_explicito_en(cod, texto)]
        horas = c.get("horas")
        item = {
            "codigos": cods, "calles": calles,
            "municipio": (c.get("municipio") or "").strip() or None,
            # El matching por calles puede enriquecer el catálogo, pero nunca
            # constituye evidencia suficiente para cambiar el estado eléctrico.
            "estado": c.get("estado") if codigos_estado and c.get("estado") in
                      ("con servicio", "sin servicio") else None,
            "codigos_estado": codigos_estado,
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

    def guardar():
        # Escritura atómica: un job cancelado a mitad de volcado dejaría un
        # JSON truncado que la próxima corrida descartaría entera.
        tmp = CACHE_FILE + ".tmp"
        json.dump(cache, open(tmp, "w"), ensure_ascii=False)
        os.replace(tmp, CACHE_FILE)

    inicio = time.monotonic()
    nuevos = fallos = 0
    corte = None
    try:
        for p in posts:
            mid = str(p["message_id"])
            if cache.get(mid, {}).get("validador_version") == VALIDADOR_VERSION:
                continue
            if not RELEVANTE.search(p["texto"] or ""):
                cache[mid] = {"fecha": p["fecha"], "tipo": "otro", "circuitos": [],
                              "por_confirmar": [], "bloques": [],
                              "mw_deficit": None, "pct_restablecido": None,
                              "via": "prefiltro", "validador_version": VALIDADOR_VERSION}
                continue
            if nuevos >= MAX_LLM:
                corte = f"tope de {MAX_LLM} posts"
                break
            if time.monotonic() - inicio > MAX_SEGUNDOS:
                corte = f"presupuesto de {MAX_SEGUNDOS}s agotado"
                break
            crudo, uso = llm(p["texto"])
            if crudo is None:
                print(f"LLM falló en {mid}: {', '.join(uso['errores']) or 'sin proveedor disponible'}")
                fallos += 1
                if fallos >= 3:
                    corte = "3 fallos consecutivos de proveedor"
                    break
                continue
            valido = validar(crudo, p["texto"])
            if valido is None:
                fallos += 1
                continue
            cache[mid] = {"fecha": p["fecha"], **valido, "via": "llm",
                          "proveedor": uso["proveedor"], "modelo": uso["modelo"],
                          "validador_version": VALIDADOR_VERSION}
            nuevos += 1
            # Incremental: el step que commitea la caché va después de este
            # script, así que sin esto un fallo tardío tira toda la corrida.
            if nuevos % 10 == 0:
                guardar()
    finally:
        guardar()

    pendientes = sum(
        1 for p in posts
        if cache.get(str(p["message_id"]), {}).get("validador_version") != VALIDADOR_VERSION)
    tot_circ = sum(len(v.get("circuitos") or []) for v in cache.values())
    print(f"partes_llm: {nuevos} posts nuevos procesados, {len(cache)} en caché, "
          f"{tot_circ} circuitos extraídos, {fallos} fallos")
    if corte:
        print(f"partes_llm: corte por {corte}; {pendientes} pendientes "
              f"para la próxima corrida")


if __name__ == "__main__":
    main()
