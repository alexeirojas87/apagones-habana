"""Extrae el catálogo oficial de circuitos (data/circuitos_oficial.json) desde
los PDF de la UNE en docs/ ("Tabla Circuito {MUNICIPIO}.pdf").

Usa pdfplumber (extracción por CELDAS de tabla, con las líneas del cuadro), no
texto plano: pdftotext partía las celdas multilínea y pegaba la primera línea de
una fila al circuito anterior (GC14 se quedaba con la mitad de las calles de
MB930) — así se contaminaron 104 de 168 circuitos en la primera extracción.

Uso: pip install pdfplumber && python scripts/extraer_oficial.py
Los PDF no van a git (docs/*.pdf está ignorado); este JSON es el resultado.
"""

import glob
import json
import os
import re
import sys
import unicodedata

try:
    import pdfplumber
except ImportError:
    sys.exit("falta pdfplumber: pip install pdfplumber")

RAIZ = os.path.join(os.path.dirname(__file__), "..")

MUNIS = {
    "10 DE OCTUBRE": "10 de Octubre", "ARROYO NARANJO": "Arroyo Naranjo",
    "BOYEROS": "Boyeros", "CENTRO HABANA": "Centro Habana", "CERRO": "Cerro",
    "COTORRO": "Cotorro", "GUANABACOA": "Guanabacoa",
    "HABANA DEL ESTE": "Habana del Este", "HABANA VIEJA": "Habana Vieja",
    "LA LISA": "La Lisa", "MARIANAO": "Marianao", "PLAYA": "Playa",
    "PLAZA": "Plaza", "REGLA": "Regla", "SAN MIGUEL DEL PADRON": "San Miguel del Padrón",
}
RE_COD = re.compile(r"^[A-Za-z]{1,3}\s?\d{1,4}$|^\d{1,4}$")

# Celdas de código ilegibles en el PDF (glifos perdidos), resueltas cruzando con
# los partes de Telegram que describen las MISMAS calles.
CODIGOS_ROTOS = {("San Miguel del Padrón", "1"): "H343"}


def sin_tildes(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def main():
    nuevo = {}
    rutas = sorted(glob.glob(os.path.join(RAIZ, "docs", "*.pdf")))
    if not rutas:
        sys.exit("no hay PDFs en docs/")
    for ruta in rutas:
        nombre = re.sub(r"Tabla Circuito\s*|\s*\(\d+\)|\.pdf", "", os.path.basename(ruta)).strip()
        muni = MUNIS.get(sin_tildes(nombre).upper())
        if not muni:
            print(f"¡PDF sin municipio conocido: {ruta} — se salta!")
            continue
        filas_ok = 0
        with pdfplumber.open(ruta) as pdf:
            for page in pdf.pages:
                for tabla in page.extract_tables():
                    for fila in tabla:
                        cells = [re.sub(r"\s+", " ", (c or "")).strip() for c in fila]
                        no_vacias = [c for c in cells if c]
                        if not no_vacias:
                            continue
                        # celda de código: admite dobles ("L317/1244" = mismo feeder)
                        cods = None
                        for c in no_vacias:
                            piezas = [p.strip() for p in c.split("/")]
                            if all(RE_COD.match(p) for p in piezas):
                                cods = [CODIGOS_ROTOS.get((muni, p.replace(" ", "").upper()),
                                                          p.replace(" ", "").upper())
                                        for p in piezas]
                                break
                        if not cods:
                            continue  # cabecera u otra fila sin código
                        textos = [c for c in no_vacias
                                  if not all(RE_COD.match(p.strip()) for p in c.split("/"))]
                        calles = max(textos, key=len) if textos else ""
                        if calles.upper() in ("DIRECCIONES", "UBICACIÓN", "UBICACION"):
                            calles = ""
                        for cod in cods:
                            e = nuevo.setdefault(cod, {"municipios": [], "calles": {}})
                            if muni not in e["municipios"]:
                                e["municipios"].append(muni)
                            if calles:
                                e["calles"][muni] = calles
                        filas_ok += 1
        print(f"{muni:22} {filas_ok:3} circuitos")

    destino = os.path.join(RAIZ, "data", "circuitos_oficial.json")
    json.dump(nuevo, open(destino, "w"), ensure_ascii=False, indent=1)
    print(f"\n{destino}: {len(nuevo)} circuitos oficiales")


if __name__ == "__main__":
    main()
