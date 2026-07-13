"""Clasifica los barrios de La Habana (catálogo OSM en data/barrios_osm.json)
según su relación con los apagones -> web/data/barrios.json

Categorías:
  bloque     aparece en las zonas de los bloques 1-6 del PDF oficial (no se
             exporta: esas zonas ya se pintan en el mapa con sus puntos propios)
  daf        aparece en las zonas de los posts de Disparo Automático por
             Frecuencia (microcortes) del canal — catálogo aprendido del histórico
  candidata_protegida  no aparece ni en bloques ni en DAF: candidata a zona que
             no rota (la lista de Circuitos de Emergencia no es pública, así que
             esto es por exclusión y aproximado)

Necesita SUPABASE_URL / SUPABASE_SERVICE_KEY para leer el histórico DAF.
"""

import json
import os
import re
import unicodedata

from supabase import create_client

RAIZ = os.path.join(os.path.dirname(__file__), "..")


def normalizar(t):
    t = "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")
    t = re.sub(r"\b(reparto|rpto\.?|barrio|residencial|el|la|los|las|de|del)\b", " ", t)
    t = re.sub(r"\balturas\b", "altura", t)  # el PDF usa singular y plural indistintamente
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def texto_bloques():
    with open(os.path.join(RAIZ, "data", "bloques.json")) as f:
        bloques = json.load(f)["bloques"]
    return normalizar(" ".join(z for b in bloques for g in b["municipios"] for z in g["zonas"]))


def texto_daf(sb):
    """Zonas mencionadas históricamente en posts DAF del canal."""
    filas = (
        sb.table("eventos")
        .select("zonas")
        .eq("chat", "canal")
        .eq("causa", "DAF")
        .eq("tipo", "afectacion")
        .execute()
        .data
    )
    return normalizar(" ".join(z for f in filas for z in (f["zonas"] or [])))


def main():
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    t_bloques = texto_bloques()
    t_daf = texto_daf(sb)

    with open(os.path.join(RAIZ, "data", "barrios_osm.json")) as f:
        barrios = json.load(f)

    import correcciones
    overrides = correcciones.clasificacion_barrios()

    # Membresía aprendida del canal: barrios que el PDF omitió pero que los avisos
    # oficiales sí ubican en un bloque (p. ej. Aldabó -> B4). Corrige falsos azules.
    ruta_apr = os.path.join(RAIZ, "data", "bloques_aprendidos.json")
    aprendidos = json.load(open(ruta_apr)) if os.path.exists(ruta_apr) else {}

    salida, conteo, vistos = [], {"bloque": 0, "daf": 0, "candidata_protegida": 0}, set()
    for b in barrios:
        n = normalizar(b["nombre"])
        if len(n) < 4 or n in vistos:  # OSM trae algunos barrios duplicados
            continue
        vistos.add(n)
        if b["nombre"] in overrides:
            cat = overrides[b["nombre"]]
        elif re.fullmatch(r"zona \d+[a-z]?", n):
            # Las "Zona N" de OSM son las zonas de Alamar: todas rotan en los
            # bloques, pero el PDF las lista como enumeración ("Zonas: 12; 13...")
            # que el matching por nombre no puede casar.
            cat = "bloque"
        elif n in t_bloques:
            cat = "bloque"
        elif n in t_daf:
            cat = "daf"
        else:
            cat = "candidata_protegida"
        # El canal manda sobre el PDF: si un aviso oficial ubicó este barrio en un
        # bloque, no es protegida ni DAF — rota en ese bloque.
        if cat in ("candidata_protegida", "daf") and b["nombre"] in aprendidos:
            cat = "bloque"
        conteo[cat] += 1
        if cat != "bloque":
            salida.append({"nombre": b["nombre"], "lat": b["lat"], "lon": b["lon"], "cat": cat})

    # Zonas protegidas confirmadas por usuarios (direcciones puntuales, no barrios OSM)
    for p in correcciones.protegidas_confirmadas():
        salida.append(
            {"nombre": p["nombre"], "lat": p["lat"], "lon": p["lon"],
             "cat": "candidata_protegida", "confirmada": True}
        )
        conteo["candidata_protegida"] += 1

    destino = os.path.join(RAIZ, "web", "data", "barrios.json")
    json.dump(salida, open(destino, "w"), ensure_ascii=False)
    print(f"Barrios: {conteo}")


if __name__ == "__main__":
    main()
