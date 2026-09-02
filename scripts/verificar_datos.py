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
  10. Punto pintado lejos de la evidencia de sus propias calles (sin red) ->
      REPARABLE si la entrada 'circ|' de la caché conserva el hit malo: el
      control cruzado de estado.py ('descarta POI lejano') solo corre en
      geocodificaciones NUEVAS, así que aquí se re-triangula retroactivamente
      con lo ya cachado (hermanos 'circ|', geometría de líneas, barrios OSM —
      ver evidencia_calles.py) y se purga para que el cron re-geocodifique.
      Con el punto respaldado por autoridad pero la GEOMETRÍA en caché lejos,
      se purga la geometría (y sus intentos) para que se rebusque junto al
      punto bueno.

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
CACHE_INTENTOS = os.path.join(RAIZ, "data", "geocache_lineas_intentos.json")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import circuitos_id  # noqa: E402
import evidencia_calles as evc  # noqa: E402


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
    return evc.clave_cache(calles)


def chequeo_evidencia_calles(circuitos, autoridad, cache_geo, cache_lineas):
    """Chequeo 10: triangulación retroactiva punto-vs-calles, SIN red.

    El control cruzado de estado.py ('descarta POI lejano', dd13548) solo corre
    en geocodificaciones NUEVAS: una entrada envenenada antes de ese commit
    nunca se re-valida (CCP20 y 15 casos más). Aquí se vuelve a preguntar a la
    evidencia ya cachada —hermanos 'circ|' con >=2 calles distintivas, geometría
    en caché, barrios OSM— y si el racimo mayoritario niega el punto pintado, se
    purga la clave propia para que el cron la re-geocodifique. Conservador por
    diseño: exige >=MIN_CONCORDANTES puntos concordantes y que el racimo
    contrario gane al que coincide con el punto (por eso callan los homónimos
    de una sola familia —'El Trébol', 'Embalse La Coca'— y los twins que se
    acusan entre sí cuando el mayoritario coincide con el punto). Desde el
    caso SG316, la familia va más lejos que el gate: hermanos 'circ|' con el
    MISMO match de POI son una sola evidencia (evidencia_calles deduplica por
    valor de match), no 30 votos clonados que se auto-certifican.

    Devuelve (problemas, purgar_geo, purgar_lineas, purgar_intentos)."""
    entradas = evc.entradas_hermanas(cache_geo)
    problemas, purgar_geo, purgar_lineas, purgar_intentos = [], set(), set(), set()
    for c in circuitos:
        lat, lon = c.get("lat"), c.get("lon")
        calles = c.get("calles") or ""
        if lat is None or not calles or evc.ALAMAR.search(calles):
            continue
        punto = (lat, lon)
        ev = evc.evidencia_de_calles(c["codigo"], calles, punto, entradas, cache_lineas)
        hit = cache_geo.get(clave_cache(calles))
        lc = evc.centro_lineas(cache_lineas, c["codigo"]) if ev["tiene_lineas"] else None
        d_ev = evc.dist_m(punto, ev["centro"]) if ev["centro"] else None
        d_lin = evc.dist_m(punto, lc) if lc else None
        mayoritario = (d_ev and ev["n"] >= evc.MIN_CONCORDANTES
                       and d_ev > evc.DIST_MINIMA_M and ev["n_coincide"] < ev["n"])
        # sin autoridad (los cheques 1-2 ya la vigilan) y con hit-POI: las
        # líneas propias, buscadas por nombres de calle reales, valen aunque
        # no formen racimo (el caso L323: líneas en Almendares, punto en
        # Arroyo Naranjo por un homónimo 'Almendares').
        geometria_negando = (d_lin and d_lin > evc.DIST_MINIMA_M and not autoridad(c)
                             and hit and hit.get("match") and not mayoritario
                             and not ev["puntos_barrio"])
        if mayoritario or geometria_negando:
            d = d_ev if mayoritario else d_lin
            detalle = (f"{c['codigo']}: pintado a {d/1000:.1f} km de la evidencia de sus "
                       f"propias calles ({ev['n']} puntos concordantes)"
                       if mayoritario else
                       f"{c['codigo']}: pintado a {d/1000:.1f} km de sus calles dibujadas "
                       f"en caché, sin autoridad de municipio")
            if hit and hit.get("match"):
                detalle += f" — hit '{hit['match'][:60]}'"
            problemas.append(("punto lejos de sus calles", detalle))
            # Purga solo si la caché guarda el punto Y el hit conservado es el
            # que discrepa de la evidencia; sin clave 'circ|' el punto vino de
            # un atajo (manual/centroide/legacy) y purgar no re-geocodifica
            # nada (PG940, SF584, C11 -> se reportan, no se purgan).
            ref = ev["centro"] if mayoritario else lc
            if hit and ref and evc.dist_m((hit["lat"], hit["lon"]), ref) > evc.DIST_MINIMA_M:
                purgar_geo.add(c["codigo"])
        elif d_lin and d_lin > evc.DIST_MINIMA_M and autoridad(c):
            # El punto manda (autoridad lo respalda, cheques 1-2 OK): la
            # geometría en caché es la envenenada (homónimos lejanos: L317,
            # PZ13). Se purga junto con sus intentos para que build_circuitos
            # la rebusque alrededor del punto bueno. Gate de 5 km (no los 3
            # del chequeo 3 sobre líneas SERVIDAS): aquí la geometría está sin
            # publicar, y un 3.3 km legítimo de una calle larga no debe
            # gastar presupuesto de Overpass todos los días.
            problemas.append(("líneas de caché lejos del punto",
                              f"{c['codigo']}: geometría en caché a {d_lin/1000:.1f} km del "
                              "punto (respaldado por su municipio); se purga para rebuscarla"))
            purgar_lineas.add(c["codigo"])
            purgar_intentos.add(c["codigo"])
    return problemas, purgar_geo, purgar_lineas, purgar_intentos


def candidatos_por_confirmar(llm_cache, umbral=3):
    """Códigos 'por_confirmar' recurrentes (chequeo 9): el LLM los ve en los
    partes pero el catálogo no los registra. Con `umbral`+ apariciones son
    candidatos a promover, no alucinaciones. Se filtra SIEMPRE contra
    es_conocido del momento —la lista `por_confirmar` del caché se congeló a
    la fecha de la extracción—: SR850 se recomendaba a diario estando ya en
    el catálogo servido, y los códigos que aprende el mismo cron
    (aprende_circuitos) deben dejar de recomendersen el día que entran."""
    conteo_pc = {}
    for v in (llm_cache or {}).values():
        for cod in v.get("por_confirmar") or []:
            conteo_pc[cod] = conteo_pc.get(cod, 0) + 1
    out = []
    for cod, n in sorted(conteo_pc.items(), key=lambda x: -x[1]):
        if n >= umbral and not circuitos_id.es_conocido(cod):
            out.append(("código por confirmar recurrente",
                        f"{cod}: visto {n} veces por el LLM y no está en el catálogo "
                        "— candidato a añadir"))
    return out


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
    purgar_geo, purgar_lineas, purgar_intentos = set(), set(), set()
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

    # 10: punto pintado lejos de la evidencia de sus propias calles (sin red):
    # la revisión retroactiva de las cachés envenenadas antes de dd13548.
    cache_geo, cache_lin = evc.cargar_caches()
    p10, g10, l10, i10 = chequeo_evidencia_calles(circuitos, autoridad, cache_geo, cache_lin)
    problemas += p10
    purgar_geo |= g10
    purgar_lineas |= l10
    purgar_intentos |= i10

    # 4: códigos duplicados
    vistos = set()
    for c in circuitos:
        if c["codigo"] in vistos:
            problemas.append(("código duplicado", c["codigo"]))
        vistos.add(c["codigo"])

    # 5: déficit con circuitos fuera del catálogo (un alias o código aprendido
    # del día ya es conocido: canonico/es_conocido, no solo la clave literal).
    for d in (estado.get("deficit") or {}).get("circuitos", []):
        if d["codigo"] not in vistos and not circuitos_id.es_conocido(d["codigo"]):
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
    problemas += candidatos_por_confirmar(llm_cache)

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

    # 8: cambios de dirección detectados por build_circuitos (últimas 24h)
    try:
        cambios = json.load(open(os.path.join(RAIZ, "data", "cambios_direccion.json")))
    except Exception:
        cambios = []
    corte_cambios = (ahora - timedelta(hours=24)).isoformat()
    for c in cambios:
        if (c.get("detectado") or "") >= corte_cambios:
            problemas.append(("cambio de dirección",
                              f"{c['codigo']}: '{(c.get('antes') or '')[:40]}' → "
                              f"'{(c.get('ahora') or '')[:40]}' "
                              f"(solapamiento {c.get('solapamiento', 0)})"))
            purgar_lineas.add(c["codigo"])

    # 9: discrepancias UNE vs usuarios (circuitos marcados discrepado)
    for c in circuitos:
        if c.get("discrepado") and c.get("conteo_usuario"):
            cu = c["conteo_usuario"]
            problemas.append(("discrepancia UNE/usuarios",
                              f"{c['codigo']}: UNE dice 'con servicio' pero "
                              f"usuarios reportan sin corriente"
                              f"{' (' + str(cu.get('horas', '?')) + 'h)' if cu.get('horas') else ''}"))

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
        if purgar_intentos:
            intentos = (json.load(open(CACHE_INTENTOS)) if os.path.exists(CACHE_INTENTOS)
                        else {})
            for cod in sorted(purgar_intentos):
                if cod in intentos:
                    del intentos[cod]  # mismo trato que un cambio de dirección:
                    reparados.append(f"{cod}: contador de intentos reiniciado (reintenta Overpass)")
            json.dump(intentos, open(CACHE_INTENTOS, "w"), ensure_ascii=False)
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
