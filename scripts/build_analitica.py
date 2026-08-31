"""Agrega todo el histórico (eventos + comentarios_llm) en web/data/analitica.json.
También genera resumen diario y análisis de patrones con deepseek-v4-flash de NaN.

Se corre en el cron después de estado.py (los datos del día ya están completos).

La lectura de la DB es INCREMENTAL: lo ya leído vive en data/analitica_raw.json
(commiteado), y cada corrida solo baja lo nuevo desde el último id + una ventana
de mensajes recientes para recoger ediciones de la Empresa. Ese caché es también
la copia de seguridad del histórico que scripts/purga.py borra de la DB.
"""

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from supabase import create_client

NAN_BASE_URL = os.environ.get("NAN_BASE_URL", "https://api.nan.builders/v1")
MODELO_NAN = os.environ.get("MODELO_NAN_PARTES", "deepseek-v4-flash")

RAIZ = os.path.join(os.path.dirname(__file__), "..")
CACHE_RAW = os.path.join(RAIZ, "data", "analitica_raw.json")
# La Empresa edita partes ya publicados (ingest.py re-sub los DAF editados):
# los últimos N mensajes del canal se re-leen SIEMPRE para recoger la versión final.
VENTANA_EDICIONES = 50
# comentarios_llm se re-procesa (upsert) durante 48 h (VENTANA_H de comentarios_llm.py,
# que re-encuadra o mejora la geocodificación): esa ventana se re-lee y sobreescribe.
HORAS_RE_LEER_COMENTARIOS = 48

RE_MW = re.compile(r"(\d{2,4})\s*MW")
RE_BLOQUE_H = re.compile(r"Bloque\s*(\d)\s*(\d{1,3})\s*horas?(?:\s*y\s*(\d{1,2})\s*minutos?)?")
# Circuito con horas: "R454 - 27 horas y 33 minutos", "1243 - 5 horas" (código con
# prefijo de letra o número puro de 3-4 dígitos; no confunde con "Bloque N horas").
# admite "(Municipio)" opcional entre código y horas (formato UNE jul/2026)
RE_CIRC_H = re.compile(r"([A-Za-z]{1,3}\d{1,4}|\d{3,4})\s*(?:\([^)]{2,30}\))?\s*-?\s*(\d+)\s*horas?(?:\s*y\s*(\d{1,2})\s*minutos?)?")
RE_AV_TIPO = re.compile(r"[🚨🛑]\s*(.+?)\s*:")
RE_AV_DIR = re.compile(r"(?:[👉💥]\s*Direcci[oó]n|📈\s*Afecta)\s*:\s*(.+)")


def _paginar_query(hacer_query):
    """Página una consulta (Supabase corta en 1000 por página)."""
    filas, off = [], 0
    while True:
        lote = hacer_query(off)
        filas += lote
        if len(lote) < 1000:
            return filas
        off += 1000


def _cargar_cache():
    try:
        c = json.load(open(CACHE_RAW))
        if isinstance(c.get("eventos"), dict) and isinstance(c.get("comentarios"), dict) \
                and isinstance(c.get("canal"), dict):
            return c
    except Exception:
        pass
    # caché nuevo o corrupto: se reconstruye leyendo todo el histórico una vez
    return {"version": 1, "cursor_eventos": 0, "cursor_comentarios": 0,
            "cursor_canal": 0, "eventos": {}, "comentarios": {}, "canal": {}}


def _guardar_cache(cache):
    tmp = CACHE_RAW + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f, ensure_ascii=False)
    os.replace(tmp, CACHE_RAW)


def refrescar_cache(sb, cache):
    """Baja solo las filas nuevas y las fusiona en el caché. Devuelve el conteo."""
    n = 0

    # eventos: extract.py solo INSERTA (nunca edita): el cursor por id basta
    nuevos = _paginar_query(lambda off: sb.table("eventos")
                            .select("id,tipo,bloque,causa,municipios,fecha")
                            .gt("id", cache["cursor_eventos"]).order("id")
                            .range(off, off + 999).execute().data)
    for e in nuevos:
        cache["eventos"][str(e["id"])] = e
    if nuevos:
        cache["cursor_eventos"] = max(e["id"] for e in nuevos)
    n += len(nuevos)

    # comentarios_llm: delta por message_id + ventana de re-lectura (upserts)
    nuevos = _paginar_query(lambda off: sb.table("comentarios_llm")
                            .select("message_id,reporta,lugar,bloque,horas,fecha")
                            .gt("message_id", cache["cursor_comentarios"])
                            .order("message_id")
                            .range(off, off + 999).execute().data)
    desde_re = (datetime.now(timezone.utc)
                - timedelta(hours=HORAS_RE_LEER_COMENTARIOS)).isoformat()
    re_leidos = _paginar_query(lambda off: sb.table("comentarios_llm")
                               .select("message_id,reporta,lugar,bloque,horas,fecha")
                               .gte("fecha", desde_re).order("message_id")
                               .range(off, off + 999).execute().data)
    for c in nuevos + re_leidos:
        cache["comentarios"][str(c["message_id"])] = c
    if nuevos:
        cache["cursor_comentarios"] = max(c["message_id"] for c in nuevos)
    n += len(nuevos) + len(re_leidos)

    # canal: delta por message_id + ventana de ediciones recientes
    nuevos = _paginar_query(lambda off: sb.table("mensajes")
                            .select("message_id,fecha,texto").eq("chat", "canal")
                            .gt("message_id", cache["cursor_canal"]).order("message_id")
                            .range(off, off + 999).execute().data)
    recientes = (sb.table("mensajes").select("message_id,fecha,texto")
                 .eq("chat", "canal").order("message_id", desc=True)
                 .limit(VENTANA_EDICIONES).execute().data)
    for m in nuevos + recientes:
        cache["canal"][str(m["message_id"])] = m
    if nuevos:
        cache["cursor_canal"] = max(m["message_id"] for m in nuevos)
    n += len(nuevos) + len(recientes)

    return n


def canal_ordenado(cache):
    """Mensajes del canal en orden cronológico (el orden importa: las averías se
    deduplican y los estados de circuito ganan en orden)."""
    return sorted(cache["canal"].values(),
                  key=lambda m: (m.get("fecha") or "", m.get("message_id") or 0))


def posts_en(canal, patron):
    """El ilike de PostgREST, aplicado en memoria sobre el caché del canal."""
    p = patron.strip("%").lower()
    return [f for f in canal if p in (f.get("texto") or "").lower()]


try:
    from zoneinfo import ZoneInfo
    HABANA = ZoneInfo("America/Havana")  # respeta el horario de verano (-4 verano, -5 invierno)
except Exception:
    HABANA = timezone(timedelta(hours=-4))  # respaldo si falta tzdata
MAX_GAP_H = 8  # tope solo para el tramo final abierto o huecos sin parte (respaldo)


def _atribuir(sin, b, ini, fin):
    """Suma el tramo apagado [ini, fin) a los días locales (parte por medianoche)."""
    while ini < fin:
        loc = ini.astimezone(HABANA)
        medianoche = (loc + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        corte = min(fin, medianoche.astimezone(timezone.utc))
        sin[(loc.strftime("%Y-%m-%d"), b)] = sin.get((loc.strftime("%Y-%m-%d"), b), 0) + \
            (corte - ini).total_seconds() / 3600
        ini = corte


def horas_sin_por_dia(parte_horas, snapshots):
    """Horas SIN luz por bloque y día, usando las HORAS ACUMULADAS que declara cada
    parte (dato oficial). Para cada bloque se recorre la secuencia de partes:
      - si el bloque aparece con 'h' horas de corte, esas horas se atribuyen hacia
        atrás desde el parte (acotadas al tiempo desde el parte anterior);
      - si estaba listado y DESAPARECE (volvió la luz entre dos partes), se cuenta
        la COLA del corte: estuvo apagado hasta ~el punto medio del hueco (no sabemos
        el minuto exacto de restablecimiento). Corrige el sesgo optimista.
    Devuelve {dia: {bloque: horas_sin_luz}}."""
    horas_en = {}  # (fecha[:16], bloque) -> horas declaradas
    for f, b, h in parte_horas:
        horas_en[(f, b)] = h
    tiempos = sorted({f for f, _ in snapshots})
    ult_parte = {b: None for b in range(1, 7)}   # hora del parte anterior
    listado_prev = {b: False for b in range(1, 7)}  # ¿estaba apagado en el parte anterior?
    intervalos = {b: [] for b in range(1, 7)}      # tramos SIN luz (ini, fin) en UTC
    for f in tiempos:
        t = datetime.fromisoformat(f)
        for b in range(1, 7):
            h = horas_en.get((f[:16], b))
            prev = ult_parte[b]
            if h is not None:
                # corte en curso: horas oficiales hacia atrás, acotadas al hueco
                elapsed = (t - prev).total_seconds() / 3600 if prev else MAX_GAP_H
                off = min(h, elapsed, 24.0)
                intervalos[b].append((t - timedelta(hours=off), t))
                listado_prev[b] = True
            else:
                # el bloque ya no está apagado; si lo estaba, contamos la cola del
                # corte hasta el restablecimiento estimado (punto medio del hueco)
                if listado_prev[b] and prev:
                    cola = min((t - prev).total_seconds() / 3600 / 2, MAX_GAP_H)
                    intervalos[b].append((prev, prev + timedelta(hours=cola)))
                listado_prev[b] = False
            ult_parte[b] = t

    # Corte AÚN ABIERTO: si en el último parte el bloque seguía listado (apagado) y
    # no ha salido un parte nuevo, ese apagón continúa hasta ahora. Sin esto, las
    # horas entre el último parte y "ahora" se contaban como si hubiera luz (era el
    # bug de "6.8 h de luz hoy" cuando el bloque llevaba apagado toda la tarde).
    # Se acota con MAX_GAP_H por si los datos están viejos (sin parte reciente).
    ahora = datetime.now(timezone.utc)
    for b in range(1, 7):
        if listado_prev[b] and ult_parte[b]:
            fin = min(ahora, ult_parte[b] + timedelta(hours=MAX_GAP_H))
            if fin > ult_parte[b]:
                intervalos[b].append((ult_parte[b], fin))

    # resumen por día (para el gráfico) y export de intervalos (para consultas por hora)
    sin = {}
    export = {}
    for b, tramos in intervalos.items():
        for ini, fin in tramos:
            _atribuir(sin, b, ini, fin)
        export[b] = [[ini.isoformat(), fin.isoformat()] for ini, fin in tramos if fin > ini]
    out = {}
    for (dia, b), h in sin.items():
        out.setdefault(dia, {})[b] = min(round(h, 1), 24.0)
    return out, export


def normalizar_tipo_averia(t):
    """Agrupa las muchas variantes de redacción en categorías limpias."""
    t = t.lower()
    if "transformador" in t:
        return "Transformador dañado"
    if "subestaci" in t:
        return "Subestación"
    if "primario" in t or ("conductor" in t and "part" in t):
        return "Primario/conductor partido"
    if "puente" in t:
        return "Puente partido"
    if "poste" in t:
        return "Poste partido"
    if "cable" in t:
        return "Cable con fallo"
    if "soterrad" in t:
        return "Soterrado"
    if "circuito" in t and "dispar" in t:
        return "Circuito disparado"
    if "combusti" in t or "linea" in t or "línea" in t:
        return "Línea/combustión"
    if "secundari" in t:
        return "Avería secundaria"
    if "primaria" in t:
        return "Avería primaria"
    return t.capitalize()[:28]


def _nan_chat(messages, api_key):
    body = json.dumps({
        "model": MODELO_NAN, "messages": messages,
        "temperature": 0.3, "max_tokens": 1024,
    }).encode()
    req = urllib.request.Request(
        f"{NAN_BASE_URL}/chat/completions", data=body,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json",
                 "User-Agent": "apagones-habana/1.0"},
    )
    data = json.load(urllib.request.urlopen(req, timeout=60))
    return data["choices"][0]["message"]["content"]


def generar_resumen_diario(eventos, comentarios, api_key):
    total_eventos = len(eventos)
    tipos = {}
    bloques = set()
    municipios = set()
    for e in eventos:
        tipos[e[1]] = tipos.get(e[1], 0) + 1
        if e[2]: bloques.add(e[2])
        for m in e[4]: municipios.add(m)
    reportes_sin = sum(1 for c in comentarios if c[1] == "sin_corriente")
    reportes_con = sum(1 for c in comentarios if c[1] == "con_corriente")
    prompt = (
        f"Resume la situación eléctrica de La Habana en las últimas 24 horas. "
        f"Datos: {total_eventos} eventos oficiales ({', '.join(f'{k}: {v}' for k, v in tipos.items())}), "
        f"{len(bloques)} bloques afectados, {len(municipios)} municipios mencionados, "
        f"{reportes_sin} reportes de sin corriente, {reportes_con} de con corriente. "
        f"Genera 3-4 líneas en español informal. No des datos de días anteriores."
    )
    try:
        return _nan_chat([
            {"role": "system", "content": "Eres un analista de datos eléctricos de La Habana. Sé conciso."},
            {"role": "user", "content": prompt},
        ], api_key)
    except Exception:
        return None


def detectar_patrones(parte_horas, evento_counts, api_key):
    if len(parte_horas) < 10:
        return None
    horas_por_bloque = {}
    for f, b, h in parte_horas:
        horas_por_bloque.setdefault(b, []).append(h)
    resumen = {}
    for b, hs in horas_por_bloque.items():
        if len(hs) >= 3:
            resumen[b] = {"veces": len(hs), "promedio_h": round(sum(hs) / len(hs), 1),
                          "max_h": max(hs)}
    prompt = (
        f"Analiza estos datos de cortes eléctricos en La Habana (últimos 30 días):\n"
        f"Horas promedio por bloque: {json.dumps(resumen)}\n"
        f"Eventos por tipo: {json.dumps(evento_counts)}\n"
        f"¿Hay algún patrón destacable? Responde en 2-3 líneas en español."
    )
    try:
        return _nan_chat([
            {"role": "system", "content": "Eres un analista de datos."},
            {"role": "user", "content": prompt},
        ], api_key)
    except Exception:
        return None


def generar_alertas(parte_horas, eventos, api_key):
    if len(parte_horas) < 5:
        return []
    alertas = []
    frecuencias = {}
    for f, b, h in parte_horas:
        frecuencias[b] = frecuencias.get(b, 0) + 1
    for b, n in frecuencias.items():
        if n >= 3:
            alertas.append({
                "bloque": b, "tipo": "cortes_frecuentes",
                "mensaje": f"Bloque {b}: {n} cortes en las últimas horas.",
                "severidad": "alta" if n >= 5 else "media",
            })
    return alertas


def main():
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

    cache = _cargar_cache()
    leidas = refrescar_cache(sb, cache)
    _guardar_cache(cache)
    total_cache = sum(len(v) for v in
                      (cache["eventos"], cache["comentarios"], cache["canal"]))
    print(f"analitica: caché con {total_cache} filas, {leidas} leídas de la DB")

    eventos = []
    for e in sorted(cache["eventos"].values(), key=lambda x: x.get("fecha") or ""):
        if not e.get("fecha"):
            continue
        eventos.append([
            e["fecha"][:16],                 # 0 fecha-hora (minuto)
            e["tipo"],                        # 1
            e["bloque"],                      # 2 (int o null)
            e["causa"],                       # 3
            e.get("municipios") or [],        # 4
        ])

    comentarios = []
    for c in sorted(cache["comentarios"].values(), key=lambda x: x.get("fecha") or ""):
        if c.get("reporta") in ("sin_corriente", "con_corriente") and c.get("lugar"):
            comentarios.append([c["fecha"][:16], c["reporta"], c["lugar"], c.get("bloque"), c.get("horas")])

    # Partes de "Actualización de afectaciones": MW del déficit, horas por bloque,
    # y un SNAPSHOT del estado de los 6 bloques (listados = sin luz) por instante.
    canal = canal_ordenado(cache)
    mw, parte_horas, snapshots, circuitos_partes = [], [], [], []
    for p in posts_en(canal, "%Actualización de afectaciones%"):
        f = p["fecha"][:16]
        m = RE_MW.search(p["texto"])
        if m:
            mw.append([f, int(m.group(1))])
        listados = set()
        for g in RE_BLOQUE_H.finditer(p["texto"]):
            nb, hh, mm = int(g.group(1)), int(g.group(2)), int(g.group(3) or 0)
            if 1 <= nb <= 6:
                parte_horas.append([f, nb, round(hh + mm / 60, 1)])
                listados.add(nb)
        if listados:  # snapshot válido: sabemos qué bloques estaban sin luz
            snapshots.append((p["fecha"], listados))
        # Formato nuevo por CIRCUITO ("✅R454 - 27 horas y 33 minutos"): horas
        # declaradas por circuito en cada parte -> [fecha, codigo, horas].
        for cg in RE_CIRC_H.finditer(p["texto"]):
            hh, mm = int(cg.group(2)), int(cg.group(3) or 0)
            circuitos_partes.append([f, cg.group(1).upper(), round(hh + mm / 60, 1)])

    # Averías DISTINTAS: cada avería física (tipo + dirección) se cuenta UNA vez,
    # aunque reaparezca en cada parte hasta que la reparen (una avería sin arreglar
    # NO es una avería nueva cada día). Guardamos fecha (primera vez), tipo y municipio.
    vistas, averias = set(), []
    for p in posts_en(canal, "%Averías existentes%"):
        tipo, municipio = None, ""
        for linea in p["texto"].split("\n"):
            li = linea.strip()
            mt = RE_AV_TIPO.match(li)
            if mt:
                tipo = normalizar_tipo_averia(mt.group(1))
                municipio = ""
                continue
            mm = re.match(r"📌\s*Municipios?\s*:\s*(.+)", li)
            if mm:
                municipio = re.sub(r"\s*\(.*?\)", "", mm.group(1)).strip(" .")
                if municipio.lower() == "lisa":
                    municipio = "La Lisa"
                continue
            md = RE_AV_DIR.match(li)
            if md and tipo:
                clave = f"{tipo}|{md.group(1).strip().lower()[:40]}"
                if clave not in vistas:
                    vistas.add(clave)
                    averias.append([p["fecha"][:16], tipo, municipio])

    horas_sin_dia, _ = horas_sin_por_dia(parte_horas, snapshots)

    salida = {
        "generado": datetime.now(timezone.utc).isoformat(),
        "eventos": eventos,
        "comentarios": comentarios,
        "mw": mw,
        "parte_horas": parte_horas,
        "averias": averias,
        "horas_sin_dia": horas_sin_dia,
        "circuitos_partes": circuitos_partes,
        "resumen_diario": None,
        "patrones": None,
        "alertas": [],
    }
    api_key = os.environ.get("NAN_API_KEY")
    if api_key:
        salida["resumen_diario"] = generar_resumen_diario(eventos, comentarios, api_key)
        evento_counts = {}
        for e in eventos:
            evento_counts[e[1]] = evento_counts.get(e[1], 0) + 1
        salida["patrones"] = detectar_patrones(parte_horas, evento_counts, api_key)
        salida["alertas"] = generar_alertas(parte_horas, eventos, api_key)
    destino = os.path.join(RAIZ, "web", "data", "analitica.json")
    json.dump(salida, open(destino, "w"), ensure_ascii=False)
    kb = os.path.getsize(destino) // 1024
    print(f"analitica.json: {len(eventos)} eventos, {len(comentarios)} comentarios ({kb} KB)")


if __name__ == "__main__":
    main()
