"""Segunda pasada de build_lineas: zonas con prefijo de localidad ("Cojímar: ...",
"En Kohly: ...") buscan sus calles acotadas al área de esa localidad (~1.2 km
alrededor de su punto OSM), no al municipio entero — imprescindible en municipios
grandes (Habana del Este) donde "Calle 84" existe en tres repartos distintos.

Actualiza web/data/zonas_lineas.geojson: si la pasada local encuentra calles
para una zona, sus features anteriores (potencialmente mal ubicadas) se
reemplazan. Correr después de build_lineas.py; luego regenerar cuadrantes.
"""

import json
import os
import re
import sys
import time
import unicodedata

import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geocode_fallos2 import calles_de  # noqa: E402

ORDINAL = {"1": "ra", "2": "da", "3": "ra", "4": "ta", "5": "ta", "6": "ta", "7": "ma", "8": "va", "9": "na"}


def canon(nombre):
    """Forma canónica tolerante: sin acentos, sin prefijos viales, sin espacios,
    con ordinales expandidos ('7A' == '7ma A' == '7maA')."""
    t = sin_acentos(nombre)
    t = re.sub(r"\(.*?\)", " ", t)  # fuera alias entre paréntesis
    t = re.sub(r"^(calles?|avenidas?|ave\.?|via|paseo( de la?)?|carretera( de la?| del?)?|la |el )\s*", "", t.strip())
    t = re.sub(r"\b(\d{1,2})([a-f])\b", lambda m: m.group(1) + ORDINAL.get(m.group(1)[-1], "") + m.group(2), t)
    return re.sub(r"[^a-z0-9]", "", t)


def vias_de_zona(caja):
    """Todas las vías con nombre del área de la localidad."""
    s, w, n, e = caja
    q = f"[out:json][timeout:60];way[highway][name]({s},{w},{n},{e});out geom;"
    req = urllib.request.Request(
        "https://overpass-api.de/api/interpreter",
        data=urllib.parse.urlencode({"data": q}).encode(),
        headers={"User-Agent": "apagones-habana/0.1 (proyecto comunitario)"},
    )
    return json.load(urllib.request.urlopen(req, timeout=90)).get("elements", [])

RAIZ = os.path.join(os.path.dirname(__file__), "..")
RADIO = 0.012  # grados ~1.2 km

import correcciones  # noqa: E402
CAJAS_MANUALES = correcciones.bbox_localidad()


def sin_acentos(t):
    return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")


def localidad_de(zona):
    m = re.match(r"(?:En (?:el |la |los |las )?)?([^:.;]{3,35}):", zona.strip())
    if not m:
        return None
    loc = m.group(1).strip()
    loc = re.sub(r"^(el |la )?(reparto|rpto\.?)\s+", "", loc, flags=re.IGNORECASE)
    if re.search(r"cuadrantes?|calles?|zonas?\b", loc, re.IGNORECASE):
        return None
    return loc


def main():
    with open(os.path.join(RAIZ, "data", "bloques.json")) as f:
        bloques = json.load(f)["bloques"]
    with open(os.path.join(RAIZ, "data", "barrios_osm.json")) as f:
        osm = {}
        for b in json.load(f):
            osm.setdefault(sin_acentos(b["nombre"]), b)

    ruta = os.path.join(RAIZ, "web", "data", "zonas_lineas.geojson")
    with open(ruta) as f:
        gj = json.load(f)

    nuevas, reemplazadas = [], set()
    for b in bloques:
        for g in b["municipios"]:
            for zona in g["zonas"]:
                loc = localidad_de(zona)
                if not loc:
                    continue
                nodo = osm.get(sin_acentos(loc))
                if not nodo:
                    continue
                nombres = [c for c in calles_de(zona) if sin_acentos(c) != sin_acentos(loc)]
                if not nombres:
                    continue
                caja = CAJAS_MANUALES.get(loc) or (
                    nodo["lat"] - RADIO, nodo["lon"] - RADIO, nodo["lat"] + RADIO, nodo["lon"] + RADIO
                )
                try:
                    vias = vias_de_zona(caja)
                except Exception as ex:
                    print(f"  ERROR {loc}: {ex}", flush=True)
                    time.sleep(15)
                    continue
                time.sleep(8)
                buscados = {canon(n) for n in nombres if len(canon(n)) >= 2}
                grupos = {}
                for via in vias:
                    nombre_osm = via["tags"]["name"]
                    # también matchea el alias entre paréntesis: "Martí Real (152)"
                    alias = re.findall(r"\((.*?)\)", nombre_osm)
                    formas = {canon(nombre_osm)} | {canon(a) for a in alias}
                    if not (formas & buscados):
                        continue
                    coords = [[round(p["lon"], 5), round(p["lat"], 5)] for p in via["geometry"]]
                    grupos.setdefault(nombre_osm, []).append(coords)
                if not grupos:
                    continue
                for calle, lineas in grupos.items():
                    nuevas.append(
                        {
                            "type": "Feature",
                            "properties": {
                                "bloque": b["bloque"], "municipio": g["municipio"],
                                "zona": zona[:160], "calle": f"{calle} ({loc})",
                            },
                            "geometry": {"type": "MultiLineString", "coordinates": lineas},
                        }
                    )
                reemplazadas.add(f"{g['municipio']}|{zona[:160]}")
                print(f"{loc} ({g['municipio']}): {len(grupos)} calles", flush=True)

    conservadas = [
        f for f in gj["features"]
        if f"{f['properties']['municipio']}|{f['properties']['zona']}" not in reemplazadas
    ]
    gj["features"] = conservadas + nuevas
    json.dump(gj, open(ruta, "w"), ensure_ascii=False)
    print(f"{len(reemplazadas)} zonas re-ubicadas localmente, {len(nuevas)} features nuevas, total {len(gj['features'])}")


if __name__ == "__main__":
    main()
