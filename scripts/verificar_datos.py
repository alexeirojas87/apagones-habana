"""Verificador diario de calidad de datos: audita lo que la web SIRVE (los JSON
publicados en Pages) buscando inconsistencias y cosas mal pintadas en el mapa.

Chequeos:
  1. Circuito pintado fuera de su municipio oficial/manual (>800 m) -> REPARABLE:
     se purga su entrada de caché (punto y líneas) y el próximo cron lo
     re-geocodifica acotado al municipio (bounded).
  2. Punto ubicado fuera de todo municipio de La Habana (mar/bahía) -> reparable
     si hay municipio de autoridad; si no, se reporta.
  3. Líneas de calles lejos del punto del circuito (>3 km) -> reparable (se purga
     la geometría, se vuelve a buscar alrededor del punto bueno).
  4. Códigos duplicados en el catálogo.
  5. Circuitos del déficit vigente que no existen en el catálogo.
  6. Frescura: estado.json con más de 2 h (el cron de 10 min está caído) o
     analitica.json con más de 26 h.
  7. Estados/fechas inválidos en el catálogo.

Uso: python scripts/verificar_datos.py [--reparar] [--informe informe.md]
Sale con código 1 si encontró problemas (reparados o no), 0 si todo bien.
"""

import argparse
import json
import math
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

RAIZ = os.path.join(os.path.dirname(__file__), "..")
BASE = os.environ.get("APAGONES_URL", "https://apagones-habana.pages.dev")
CACHE_GEO = os.path.join(RAIZ, "data", "geocache_averias.json")
CACHE_LINEAS = os.path.join(RAIZ, "data", "geocache_circuitos_lineas.json")


def vivo(nombre):
    req = urllib.request.Request(f"{BASE}/data/{nombre}",
                                 headers={"User-Agent": "apagones-verificador/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _anillos(f):
    g = f["geometry"]
    return [g["coordinates"][0]] if g["type"] == "Polygon" else [p[0] for p in g["coordinates"]]


def _en_poly(lat, lon, anillo):
    dentro, n = False, len(anillo)
    for i in range(n):
        x1, y1 = anillo[i]
        x2, y2 = anillo[(i + 1) % n]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            dentro = not dentro
    return dentro


def cargar_municipios():
    gj = json.load(open(os.path.join(RAIZ, "web", "data", "municipios.geojson")))
    return [(f["properties"]["municipio"], _anillos(f)) for f in gj["features"]]


def muni_de(munis, lat, lon):
    for nom, ans in munis:
        if any(_en_poly(lat, lon, a) for a in ans):
            return nom
    return None


def dist_a_muni(munis, lat, lon, nombre):
    """Distancia mínima aprox (m) del punto al borde del municipio (muestreado)."""
    best = float("inf")
    for nom, ans in munis:
        if nom != nombre:
            continue
        for a in ans:
            for x, y in a[::3]:
                best = min(best, math.hypot((y - lat) * 111000, (x - lon) * 102000))
    return best


def dist_a_cualquiera(munis, lat, lon):
    return min(dist_a_muni(munis, lat, lon, nom) for nom, _ in munis)


def clave_cache(calles):
    return "circ|" + re.sub(r"\s+", " ", calles or "").strip(" .;,")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reparar", action="store_true",
                    help="purga de las cachés las entradas malas (el cron re-geocodifica)")
    ap.add_argument("--informe", help="escribe el informe markdown en este archivo")
    args = ap.parse_args()

    munis = cargar_municipios()
    oficial = json.load(open(os.path.join(RAIZ, "data", "circuitos_oficial.json")))
    manual = json.load(open(os.path.join(RAIZ, "data", "correcciones.json"))).get(
        "circuitos_municipio", {})
    cat = vivo("circuitos.json")
    estado = vivo("estado.json")
    analitica = vivo("analitica.json")

    problemas = []   # (chequeo, detalle)
    purgar_geo, purgar_lineas = set(), set()
    circuitos = cat.get("circuitos", [])
    ahora = datetime.now(timezone.utc)

    def autoridad(c):
        info = oficial.get(c["codigo"])
        if info and info.get("municipios"):
            return info["municipios"]
        if c["codigo"] in manual:
            return [manual[c["codigo"]]]
        return []

    # 1 y 2: puntos fuera de su municipio o fuera de La Habana
    for c in circuitos:
        lat, lon = c.get("lat"), c.get("lon")
        if lat is None:
            continue
        esperados = autoridad(c)
        real = muni_de(munis, lat, lon)
        if esperados and real not in esperados:
            d = min(dist_a_muni(munis, lat, lon, m) for m in esperados)
            if d > 800:
                problemas.append(("fuera de su municipio",
                                  f"{c['codigo']}: debía estar en {'/'.join(esperados)}, "
                                  f"está en {real or 'el agua'} ({d/1000:.1f} km)"))
                purgar_geo.add(c["codigo"])
                purgar_lineas.add(c["codigo"])
        elif real is None and dist_a_cualquiera(munis, lat, lon) > 500:
            problemas.append(("punto fuera de La Habana",
                              f"{c['codigo']}: ({lat:.4f}, {lon:.4f}) no cae en ningún municipio"))
            if esperados:
                purgar_geo.add(c["codigo"])
                purgar_lineas.add(c["codigo"])

    # 3: líneas de calles lejos del punto del circuito
    for c in circuitos:
        if not c.get("lineas") or c.get("lat") is None:
            continue
        pts = [p for l in c["lineas"] for p in l]
        if not pts:
            continue
        cx = sum(p[1] for p in pts) / len(pts)
        cy = sum(p[0] for p in pts) / len(pts)
        d = math.hypot((cx - c["lat"]) * 111000, (cy - c["lon"]) * 102000)
        if d > 3000:
            problemas.append(("líneas lejos de su punto",
                              f"{c['codigo']}: calles dibujadas a {d/1000:.1f} km del punto"))
            purgar_lineas.add(c["codigo"])

    # 4: códigos duplicados
    vistos = set()
    for c in circuitos:
        if c["codigo"] in vistos:
            problemas.append(("código duplicado", c["codigo"]))
        vistos.add(c["codigo"])

    # 5: déficit con circuitos fuera del catálogo
    for d in (estado.get("deficit") or {}).get("circuitos", []):
        if d["codigo"] not in vistos:
            problemas.append(("déficit sin catálogo",
                              f"{d['codigo']} aparece en el parte pero no en circuitos.json"))

    # 6: frescura
    for nombre, doc, tope_h in (("estado.json", estado, 2), ("analitica.json", analitica, 26)):
        gen = doc.get("generado")
        edad = (ahora - datetime.fromisoformat(gen)).total_seconds() / 3600 if gen else None
        if edad is None or edad > tope_h:
            problemas.append(("datos viejos",
                              f"{nombre} generado hace {edad:.1f} h (tope {tope_h} h)"
                              if edad is not None else f"{nombre} sin campo 'generado'"))

    # 8: discrepancias regex vs LLM (comparar_extraccion.py) de las últimas 24 h
    try:
        discrepancias = json.load(open(os.path.join(RAIZ, "data",
                                                    "discrepancias_extraccion.json")))
    except Exception:
        discrepancias = {}
    corte_d = (ahora - timedelta(hours=24)).isoformat()
    for mid, d in discrepancias.items():
        if (d.get("fecha") or "") < corte_d:
            continue
        detalle = []
        if d.get("solo_llm"):
            detalle.append(f"solo el LLM vio {', '.join(d['solo_llm'])}")
        if d.get("solo_regex"):
            detalle.append(f"solo el regex vio {', '.join(d['solo_regex'])}")
        problemas.append(("discrepancia regex/LLM",
                          f"post {mid}: {'; '.join(detalle)} — «{d.get('extracto', '')[:80]}»"))

    # 9: códigos 'por_confirmar' recurrentes (el LLM los ve pero no están en el
    # catálogo): con 3+ apariciones son candidatos a promover, no alucinaciones.
    try:
        llm_cache = json.load(open(os.path.join(RAIZ, "data", "partes_llm.json")))
    except Exception:
        llm_cache = {}
    conteo_pc = {}
    for v in llm_cache.values():
        for cod in v.get("por_confirmar") or []:
            conteo_pc[cod] = conteo_pc.get(cod, 0) + 1
    for cod, n in sorted(conteo_pc.items(), key=lambda x: -x[1]):
        if n >= 3:
            problemas.append(("código por confirmar recurrente",
                              f"{cod}: visto {n} veces por el LLM y no está en el catálogo "
                              "— candidato a añadir"))

    # 7: estados/fechas inválidos
    for c in circuitos:
        if c.get("estado") not in (None, "con servicio", "sin servicio"):
            problemas.append(("estado inválido", f"{c['codigo']}: {c['estado']!r}"))
        for campo in ("primera", "ultima", "estado_fecha"):
            v = c.get(campo)
            if v:
                try:
                    datetime.fromisoformat(v)
                except ValueError:
                    problemas.append(("fecha inválida", f"{c['codigo']}.{campo}: {v!r}"))

    # Reparación: purgar cachés para que el cron re-geocodifique acotado
    reparados = []
    if args.reparar and (purgar_geo or purgar_lineas):
        g = json.load(open(CACHE_GEO)) if os.path.exists(CACHE_GEO) else {}
        lin = json.load(open(CACHE_LINEAS)) if os.path.exists(CACHE_LINEAS) else {}
        calles = {c["codigo"]: c.get("calles") for c in circuitos}
        for cod in sorted(purgar_geo):
            k = clave_cache(calles.get(cod))
            if k in g:
                del g[k]
                reparados.append(f"{cod}: punto purgado (se re-geocodifica acotado)")
        for cod in sorted(purgar_lineas):
            if cod in lin:
                del lin[cod]
                reparados.append(f"{cod}: líneas purgadas (se rebuscan junto al punto bueno)")
        json.dump(g, open(CACHE_GEO, "w"), ensure_ascii=False)
        json.dump(lin, open(CACHE_LINEAS, "w"), ensure_ascii=False)

    # Informe
    lineas_inf = [f"# Verificación de datos — {ahora.strftime('%Y-%m-%d %H:%M')} UTC", ""]
    if not problemas:
        lineas_inf.append("✅ Sin problemas: los datos publicados son consistentes.")
    else:
        lineas_inf.append(f"⚠️ {len(problemas)} problema(s) encontrados:\n")
        por_tipo = {}
        for tipo, det in problemas:
            por_tipo.setdefault(tipo, []).append(det)
        for tipo, dets in por_tipo.items():
            lineas_inf.append(f"## {tipo} ({len(dets)})")
            lineas_inf += [f"- {d}" for d in dets]
            lineas_inf.append("")
    if reparados:
        lineas_inf.append(f"## 🔧 Auto-reparados ({len(reparados)})")
        lineas_inf += [f"- {r}" for r in reparados]
    informe = "\n".join(lineas_inf)
    print(informe)
    if args.informe:
        open(args.informe, "w").write(informe)
    sys.exit(1 if problemas else 0)


if __name__ == "__main__":
    main()
