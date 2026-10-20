#!/usr/bin/env python3
"""Audita la coherencia entre el catálogo y la capa de geometrías.

Uso:
    python3 scripts/auditar_circuitos.py [ruta/circuitos.json]

Reporta:
- circuitos con polígono y sin polígono (y sin ubicación);
- centroides del catálogo lejos de su polígono;
- municipios del catálogo que no toca el polígono;
- puntos compartidos por varios códigos;
- los casos más graves para revisión manual.

Es de solo lectura: no modifica nada.
"""

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from alinear_circuitos_geometria import anillos, cargar_municipios, en_poly  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
CATALOGO = RAIZ / "web" / "data" / "circuitos.json"
GEOMETRIAS = RAIZ / "web" / "data" / "circuitos_geometrias.geojson"


def distancia_km(lat, lon, lat2, lon2):
    return math.hypot((lat - lat2) * 111.0, (lon - lon2) * 102.0)


def punto_mas_cercano(lat, lon, ring):
    return min(ring, key=lambda p: distancia_km(lat, lon, p[1], p[0]))


def main():
    ruta = Path(sys.argv[1]) if len(sys.argv) > 1 else CATALOGO
    with open(ruta, encoding="utf-8") as f:
        datos = json.load(f)
    circuitos = datos.get("circuitos") or datos
    with open(GEOMETRIAS, encoding="utf-8") as f:
        features = json.load(f)["features"]
    municipios = cargar_municipios()

    por_codigo = defaultdict(list)
    for f in features:
        for cod in (f.get("properties") or {}).get("codigos") or []:
            por_codigo[cod.upper()].append(f)

    lejos, muni_mal, sin_poly, sin_ubic = [], [], [], []
    for c in circuitos:
        cod = str(c.get("codigo") or "").upper()
        fs = por_codigo.get(cod)
        if not fs:
            sin_poly.append(cod)
            if c.get("lat") is None:
                sin_ubic.append(cod)
            continue
        if c.get("lat") is None:
            continue
        rings = [r for f in fs for r in anillos(f)]
        if any(en_poly(c["lat"], c["lon"], r) for r in rings):
            d = 0.0
        else:
            d = min(
                distancia_km(c["lat"], c["lon"], *reversed(punto_mas_cercano(c["lat"], c["lon"], r)))
                for r in rings
            )
        if d > 0.4:
            lejos.append((d, cod, c.get("municipio"), c.get("calles", "")[:55]))

        toca = set()
        for f in fs:
            for r in anillos(f):
                for lon, lat in r[:-1]:
                    for nombre, mring in municipios:
                        if en_poly(lat, lon, mring):
                            toca.add(nombre)
                            break
        if toca and c.get("municipio") not in toca:
            muni_mal.append((cod, c.get("municipio"), sorted(toca)))

    puntos = defaultdict(list)
    for c in circuitos:
        if c.get("lat") is not None:
            puntos[(round(c["lat"], 5), round(c["lon"], 5))].append(c["codigo"])
    dups = {k: v for k, v in puntos.items() if len(v) > 1}

    print(f"catálogo: {ruta} ({len(circuitos)} circuitos)")
    print(f"con polígono: {len(por_codigo) and sum(1 for c in circuitos if str(c.get('codigo','')).upper() in por_codigo)}")
    print(f"sin polígono: {len(sin_poly)} · sin ubicación: {len(sin_ubic)}")
    print(f"\ncentroides a >400 m de su polígono: {len(lejos)}")
    for d, cod, muni, calles in sorted(lejos, reverse=True)[:20]:
        print(f"  {cod:<8} {d:5.1f} km  {muni or '—':<18} {calles}")
    print(f"\nmunicipios que el polígono no toca: {len(muni_mal)}")
    for cod, actual, toca in muni_mal[:20]:
        print(f"  {cod:<8} catálogo={actual or '—':<18} polígono={', '.join(toca)}")
    print(f"\npuntos compartidos por 2+ códigos: {len(dups)}")
    for punto, cods in dups.items():
        print(f"  {punto}: {', '.join(cods)}")


if __name__ == "__main__":
    main()
