#!/usr/bin/env python3
"""Serie 24 h por circuito para los sparklines de la web.

Autónomo y offline: lee SOLO los JSON commiteados (web/data/partes.json y
web/data/circuitos.json) y reescribe circuitos.json en el sitio con la clave
nueva `serie_24h` en cada circuito, preservando el resto de las claves.
Idempotente: cada corrida recalcula la serie desde los partes, nunca desde la
serie ya escrita.

Regla aprobada ("mezcla"/carry): 24 cubos horarios en America/Havana (0 = hace
23 h … 23 = la hora actual). El valor de cada cubo es el estado del evento más
reciente con fecha <= fin del cubo, buscado en TODO el histórico. "con" solo
de ✅ Restablecimiento; "sin" de las etiquetas de afectación; los partes del
SEN se ignoran. "nd" (ambar) únicamente antes de la primera mención histórica
del circuito: despues de eso, el último estado se arrastra a traves de los
huecos.
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
PARTES_JSON = ROOT / "web" / "data" / "partes.json"
CIRCUITOS_JSON = ROOT / "web" / "data" / "circuitos.json"

HABANA = ZoneInfo("America/Havana")

# Etiquetas que significan "el circuito perdió la corriente".
TAGS_SIN = {
    "📢 Aviso",
    "📊 Parte de afectación",
    "🔻 Afectación",
    "🟡 DAF",
    "🚧 Averías",
    "⚠️ Emergencia",
}

# Códigos en el texto del parte: "👉 CODE:" o "👉CODE(...)".
CODE_RE = re.compile(r"👉\s*([A-Za-z0-9]+)")


def cargar_eventos(known_codes, partes):
    """{codigo: [(dt, "con"|"sin"), ...]} ordenado por dt ascendente."""
    eventos = {}
    for p in partes.get("partes", []):
        tag = p.get("tag", "")
        if tag.startswith("✅"):
            state = "con"
        elif tag in TAGS_SIN:
            state = "sin"
        else:
            continue  # "⚡ Parte del SEN" y etiquetas desconocidas: se ignoran
        dt = datetime.fromisoformat(p["fecha"])
        for code in CODE_RE.findall(p.get("texto", "")):
            if code in known_codes:
                eventos.setdefault(code, []).append((dt, state))
    for ev in eventos.values():
        ev.sort(key=lambda x: x[0])
    return eventos


def fin_de_cubos(now):
    """Fines de los 24 cubos horarios, ascendentes (tz-aware, marco Havana).

    El cubo i cubre [h0 - (23-i)h, +1h), siendo h0 la hora en punto actual.
    """
    h0 = now.astimezone(HABANA).replace(minute=0, second=0, microsecond=0)
    return [h0 - timedelta(hours=23 - i) + timedelta(hours=1) for i in range(24)]


def serie_para(eventos, fines):
    """24 valores: estado del evento <= fin de cubo más reciente (carry)."""
    serie = []
    idx = 0
    estado = None
    for fin in fines:  # fines ascendentes -> un solo paseo por los eventos
        while idx < len(eventos) and eventos[idx][0] <= fin:
            estado = eventos[idx][1]
            idx += 1
        serie.append(estado if estado is not None else "nd")
    return serie


def main():
    with open(CIRCUITOS_JSON, encoding="utf-8") as f:
        circuitos = json.load(f)
    with open(PARTES_JSON, encoding="utf-8") as f:
        partes = json.load(f)

    known_codes = {c["codigo"] for c in circuitos.get("circuitos", [])}
    eventos = cargar_eventos(known_codes, partes)
    fines = fin_de_cubos(datetime.fromisoformat(circuitos["generado"]))

    con_serie = 0
    for c in circuitos["circuitos"]:
        c["serie_24h"] = serie_para(eventos.get(c["codigo"], []), fines)
        if any(v != "nd" for v in c["serie_24h"]):
            con_serie += 1

    # Mismo formato que el archivo commiteado: compacto, ensure_ascii=False,
    # sin salto final.
    with open(CIRCUITOS_JSON, "w", encoding="utf-8") as f:
        f.write(json.dumps(circuitos, ensure_ascii=False))

    total = len(circuitos["circuitos"])
    print(f"serie_24h: {con_serie}/{total} circuitos con serie no-nd (24 cubos horarios, America/Havana)")


if __name__ == "__main__":
    main()
