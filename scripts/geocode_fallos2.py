"""Segunda pasada de geocodificación para las zonas que fallaron en la primera.

Estrategia: las descripciones fallidas son mayormente tramos/cuadrantes de calles
("Belascoaín desde Carlos III hasta San Miguel"). Se extraen todos los nombres de
calle de la descripción, se geocodifica cada uno acotado al municipio, y la zona
se ubica en el centroide de las calles encontradas (aproxima el cuadrante).

Actualiza data/geocache.json en el mismo formato; después hay que correr
geocode_zonas.py para regenerar web/data/zonas.geojson (no re-consulta nada).
"""

import json
import os
import re
import time

from geocode_zonas import RAIZ, CACHE, bbox_municipios, nominatim

RUIDO = {
    "cuadrante", "cuadrantes", "lfc", "linea del ferrocarril", "línea del ferrocarril",
    "zonas", "zona", "km", "el", "la", "los", "las", "y", "a", "de", "del",
    "reparto", "rpto", "calle", "calles", "avenida", "avenidas", "ave",
}
ORDINAL = re.compile(r"^\d{1,3}[a-z]?$|^\d{1,2}(ra|da|ta|ma|na|va)[a-z]?$", re.IGNORECASE)


def calles_de(zona: str) -> list:
    """Todos los posibles nombres de calle de la descripción."""
    # separa por puntuación y conectores de tramo
    trozos = re.split(r"[,;.:()]| desde | hasta | entre | y | a | e | en ", zona, flags=re.IGNORECASE)
    calles, vistas = [], set()
    for t in trozos:
        t = t.strip(" .-ºª")
        t = re.sub(r"^(calles?|avenidas?|ave\.?|calzada del?|carretera del?)\s+", "", t, flags=re.IGNORECASE).strip()
        if not (1 <= len(t) <= 35):
            continue
        if len(t) == 1:
            # calles de una letra (Vedado, Guiteras...): solo mayúsculas sueltas
            if not (t.isalpha() and t.isupper()):
                continue
        elif t.lower() in RUIDO or re.fullmatch(r"[\d\s/]+", t) and len(t) > 4:
            continue
        clave = t.lower()
        if clave in vistas:
            continue
        vistas.add(clave)
        calles.append(t)
    return calles[:10]


def consultas(nombre: str) -> list:
    """Variantes de consulta para un nombre de calle."""
    if ORDINAL.fullmatch(nombre):
        return [f"Calle {nombre}", f"Avenida {nombre}"]
    return [nombre, f"Calle {nombre}"]


def main():
    cajas = bbox_municipios()
    cache = json.load(open(CACHE))
    fallidas = [k for k, v in cache.items() if not v]
    print(f"{len(fallidas)} zonas fallidas a reintentar")

    for i, clave in enumerate(fallidas):
        municipio, zona = clave.split("|", 1)
        caja = cajas[municipio]
        puntos, usadas = [], []
        for calle in calles_de(zona):
            hit = None
            for q in consultas(calle):
                hit = nominatim(f"{q}, {municipio}, La Habana, Cuba", caja)
                time.sleep(1.1)
                if hit:
                    break
            if hit:
                puntos.append((hit["lat"], hit["lon"]))
                usadas.append(calle)
            if len(puntos) >= 3:  # con 3 calles el centroide ya es estable
                break
        if puntos:
            lat = sum(p[0] for p in puntos) / len(puntos)
            lon = sum(p[1] for p in puntos) / len(puntos)
            cache[clave] = {
                "lat": lat, "lon": lon,
                "match": " × ".join(usadas),
                "candidato": f"centroide de {len(puntos)} calles",
            }
        if (i + 1) % 10 == 0:
            json.dump(cache, open(CACHE, "w"), ensure_ascii=False)
            ok = sum(1 for k in fallidas if cache[k])
            print(f"  {i+1}/{len(fallidas)} reintentadas, {ok} recuperadas")

    json.dump(cache, open(CACHE, "w"), ensure_ascii=False)
    ok = sum(1 for k in fallidas if cache[k])
    print(f"Recuperadas {ok}/{len(fallidas)}")


if __name__ == "__main__":
    main()
