"""Geocodifica las zonas de data/bloques.json con Nominatim (OSM) y genera
web/data/zonas.geojson: un punto por zona, con su bloque y municipio.

- Respeta el rate limit de Nominatim (1 req/seg).
- Cachea resultados en data/geocache.json: se puede interrumpir y reanudar,
  y las corridas siguientes solo consultan lo que falta.
- Las zonas sin resultado quedan listadas en data/geocode_fallos.txt para
  corrección manual (agregar coordenadas a mano en data/geocache.json).
"""

import json
import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request

RAIZ = os.path.join(os.path.dirname(__file__), "..")
CACHE = os.path.join(RAIZ, "data", "geocache.json")
FALLOS = os.path.join(RAIZ, "data", "geocode_fallos.txt")
SALIDA = os.path.join(RAIZ, "web", "data", "zonas.geojson")
UA = {"User-Agent": "apagones-habana/0.1 (proyecto comunitario)"}

MUNICIPIOS_NORM = {
    "playa", "plaza", "plaza de la revolucion", "centro habana", "habana vieja",
    "la habana vieja", "regla", "habana del este", "guanabacoa",
    "san miguel del padron", "10 de octubre", "diez de octubre", "cerro",
    "marianao", "la lisa", "boyeros", "arroyo naranjo", "cotorro", "la habana",
}

RUIDO = re.compile(
    r"^(en (el |la |los |las )?|cuadrantes?( de las calles)?[:,]?\s*|reparto |rpto\.? "
    r"|calles? |avenidas? |ave\.? |alrededores de (calle )?|parte de )",
    re.IGNORECASE,
)


def normalizar(t):
    # minúsculas + sin acentos: base para comparaciones de nombres insensibles a
    # mayúsculas (municipios, circuitos). Sin .lower() fallaban el guardia de
    # centroides y el emparejamiento de restablecimientos por nombre.
    return "".join(
        c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn"
    )


def bbox_municipios():
    with open(os.path.join(RAIZ, "web", "data", "municipios.geojson")) as f:
        gj = json.load(f)
    cajas = {}
    for feat in gj["features"]:
        xs, ys = [], []

        def walk(c):
            if isinstance(c[0], (int, float)):
                xs.append(c[0]); ys.append(c[1])
            else:
                for x in c:
                    walk(x)

        walk(feat["geometry"]["coordinates"])
        cajas[feat["properties"]["municipio"]] = (min(xs), min(ys), max(xs), max(ys))
    return cajas


def candidatos(zona: str) -> list:
    """Nombres geocodificables extraídos de la descripción de la zona."""
    cands = []
    # "En Santo Suárez: ..." / "Zamora: ..." -> el nombre antes de ':'
    antes = zona.split(":", 1)[0].strip()
    if 2 < len(antes) < 45:
        cands.append(antes)
    # primera oración partida por comas y ' y ': "Alturas de Lotería, Reparto Modelo y ..."
    primera = re.split(r"[.;]", zona)[0]
    for parte in re.split(r",| y ", primera)[:3]:
        parte = parte.strip()
        if 2 < len(parte) < 45:
            cands.append(parte)
    # limpiar prefijos de ruido y calles "X desde A hasta B" -> "X"
    limpios, vistos = [], set()
    for c in cands:
        c = RUIDO.sub("", c).strip()
        c = re.split(r"\s+(desde|entre|de|hasta)\s+", c)[0].strip(" .:-")
        clave = normalizar(c.lower())
        if len(c) > 2 and not c.isdigit() and clave not in vistos:
            vistos.add(clave)
            limpios.append(c)
    return limpios[:3]


def nominatim(consulta: str, caja):
    x0, y0, x1, y1 = caja
    q = urllib.parse.urlencode(
        {
            "q": consulta,
            "format": "json",
            "limit": 1,
            "viewbox": f"{x0},{y1},{x1},{y0}",
            "bounded": 1,
        }
    )
    req = urllib.request.Request(f"https://nominatim.openstreetmap.org/search?{q}", headers=UA)
    try:
        data = json.load(urllib.request.urlopen(req, timeout=20))
    except Exception:
        return None
    if not data:
        return None
    match = data[0]["display_name"].split(",")[0]
    # rechaza el fallback basura de Nominatim: el municipio/provincia entero
    if normalizar(match) in MUNICIPIOS_NORM:
        return None
    return {"lat": float(data[0]["lat"]), "lon": float(data[0]["lon"]), "match": match}


def resolver_zonas_numeradas(zona: str):
    """Regla Alamar: un texto que es una lista de zonas numeradas ('Zonas: 1, 2,
    3', 'En Alamar las Zonas: 12; 13...') o que menciona 'Alamar' se ubica en el
    centroide de esas zonas (coords OSM), sin geocodificador. Independiente del
    municipio: aplica tanto al PDF como a los circuitos de posts de Telegram."""
    t = zona.strip().lower()
    # la lista puede venir precedida por el código del circuito ("AL56 - Zonas: 13...")
    if not (re.search(r"\bzonas?\s*[:\d]", t) or "alamar" in t):
        return None
    with open(os.path.join(RAIZ, "data", "barrios_osm.json")) as f:
        osm = {b["nombre"].lower(): b for b in json.load(f)}
    numeros = re.findall(r"\b(\d{1,2})\b", re.sub(r"micro\s*\w+", "", zona, flags=re.IGNORECASE))
    puntos = []
    for n in dict.fromkeys(numeros):  # únicos, en orden
        b = osm.get(f"zona {n}")
        if b:
            puntos.append((b["lat"], b["lon"]))
    if not puntos:
        b = osm.get("alamar")
        puntos = [(b["lat"], b["lon"])] if b else [(23.1655, -82.2705)]  # centro de Alamar
    return {
        "lat": sum(p[0] for p in puntos) / len(puntos),
        "lon": sum(p[1] for p in puntos) / len(puntos),
        "match": f"Alamar ({len(puntos)} zonas)",
        "candidato": "regla Alamar",
    }


def resolver_alamar(municipio: str, zona: str):
    """Variante del PDF: solo aplica dentro de Habana del Este."""
    if municipio != "Habana del Este":
        return None
    return resolver_zonas_numeradas(zona)


def main():
    with open(os.path.join(RAIZ, "data", "bloques.json")) as f:
        bloques = json.load(f)["bloques"]
    cajas = bbox_municipios()
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}

    tareas = []
    for b in bloques:
        for grupo in b["municipios"]:
            for zona in grupo["zonas"]:
                tareas.append((b["bloque"], grupo["municipio"], zona))

    pendientes = [t for t in tareas if f"{t[1]}|{t[2]}" not in cache]
    print(f"{len(tareas)} zonas, {len(pendientes)} por geocodificar")

    for i, (bloque, municipio, zona) in enumerate(pendientes):
        clave = f"{municipio}|{zona}"
        resultado = resolver_alamar(municipio, zona)
        if resultado:
            cache[clave] = resultado
            continue
        for cand in candidatos(zona):
            resultado = nominatim(f"{cand}, {municipio}, La Habana, Cuba", cajas[municipio])
            time.sleep(1.1)
            if resultado:
                resultado["candidato"] = cand
                break
        cache[clave] = resultado  # None también se cachea (fallo conocido)
        if (i + 1) % 20 == 0:
            json.dump(cache, open(CACHE, "w"), ensure_ascii=False)
            ok = sum(1 for v in cache.values() if v)
            print(f"  {i+1}/{len(pendientes)} (aciertos acumulados: {ok}/{len(cache)})")

    json.dump(cache, open(CACHE, "w"), ensure_ascii=False)

    features, fallos = [], []
    for bloque, municipio, zona in tareas:
        r = cache.get(f"{municipio}|{zona}")
        if not r:
            fallos.append(f"B{bloque} | {municipio} | {zona}")
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "bloque": bloque,
                    "municipio": municipio,
                    "zona": zona[:160],
                    "match": r.get("match", ""),
                },
                "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
            }
        )

    json.dump({"type": "FeatureCollection", "features": features}, open(SALIDA, "w"), ensure_ascii=False)
    open(FALLOS, "w").write("\n".join(fallos))
    print(f"zonas.geojson: {len(features)} puntos | fallos: {len(fallos)} (ver data/geocode_fallos.txt)")


if __name__ == "__main__":
    main()
