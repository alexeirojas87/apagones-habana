#!/usr/bin/env python3
"""Serie 24 h por circuito para los sparklines de la web.

Autónomo y offline: lee los JSON del ÁRBOL DE TRABAJO (web/data/partes.json
recién generado por build_partes, web/data/circuitos.json recién generado por
build_circuitos y web/data/estado.json por las horas oficiales del parte de
déficit) y reescribe circuitos.json con la clave nueva `serie_24h` en cada
circuito, preservando el resto de las claves. Idempotente: cada corrida
recalcula la serie desde los partes, nunca desde la serie ya escrita.

OJO orden del pipeline: tiene que correr DESPUÉS de build_partes. Si lee el
partes.json del checkout (semanas de antigüedad, porque el workflow despliega
sin commitear web/data), el carry de partes pinta con eventos viejos y los
bloques contradicen el estado real.

Regla aprobada ("mezcla"/carry): 24 cubos horarios en America/Havana (0 = hace
23 h … 23 = la hora actual). El valor de cada cubo es el estado del evento más
reciente con fecha <= fin del cubo, buscado en TODO el histórico. "con" solo
de ✅ Restablecimiento; "sin" de las etiquetas de afectación; los partes del
SEN se ignoran. "nd" (ambar) únicamente antes de la primera mención histórica
del circuito: despues de eso, el último estado se arrastra a traves de los
huecos.

Sobre ese carry se superpone el ESTADO VIGENTE del catálogo, que es la fuente
autoritativa que ya usa el titular "X h sin luz": muchos cortes no van
etiquetados con "👉 CODE" en el parte, así que el carry de partes puede
quedarse anclado en un ✅ antiguo y pintar de verde un circuito que hoy está
apagado (el bug de PZ7).

El ancla del estado vigente es el MISMO reloj que el titular: `estado_desde`
(fecha del parte que CAMBIÓ el estado), con respaldo en `estado_fecha` (última
MENCIÓN) para catálogos viejos, y la señal vecinal contraria si es posterior.
Usar `estado_fecha` era el bug de los bloques verdes: una mención que repite
el mismo "sin servicio" refresca `estado_fecha` pero NO cambia el estado, así
que un corte de 34 h podía pintar solo las últimas 3 h en rojo. Los cubos con
fin >= ancla toman el estado vigente; los anteriores conservan el carry, y si
el carry contradice la alternancia (deja el mismo estado justo antes de un
cambio) se corrige al contrario hasta la última mención previa.

Si el parte de déficit vigente declara horas para el circuito (la MISMA fuente
del titular "lleva Xh (según la UNE)"), ese inicio manda cuando implica un
corte más viejo que el reloj del estado: un parpadeo de un minuto no borra
23 horas sin corriente. Los códigos se comparan sin distinguir mayúsculas
porque la UNE escribe "AL53" y "Al53" indistintamente.
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
PARTES_JSON = ROOT / "web" / "data" / "partes.json"
CIRCUITOS_JSON = ROOT / "web" / "data" / "circuitos.json"
ESTADO_JSON = ROOT / "web" / "data" / "estado.json"

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

# Estado vigente del catálogo -> valor de cubo. Fuente autoritativa del titular.
ESTADO_MAP = {"sin servicio": "sin", "con servicio": "con"}


def cargar_eventos(known_codes, partes):
    """{codigo: [(dt, "con"|"sin"), ...]} ordenado por dt ascendente.

    Los códigos se normalizan a mayúsculas porque la UNE escribe tanto "AL53"
    como "Al53" en el mismo parte, y el catálogo (y build_circuitos) los trata
    como el mismo circuito. Sin esto el carry se perdía justo el ✅ que marca
    el retorno y el sparkline quedaba anclado en el estado viejo.
    """
    por_mayusculas = {c.upper(): c for c in known_codes}
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
        for raw in CODE_RE.findall(p.get("texto", "")):
            code = por_mayusculas.get(raw.upper())
            if code:
                eventos.setdefault(code, []).append((dt, state))
    for ev in eventos.values():
        ev.sort(key=lambda x: x[0])
    return eventos


def cargar_inicios_oficiales(estado):
    """{codigo: inicio del corte} según las horas declaradas por la UNE.

    El parte de déficit publica "horas" de afectación por circuito y es la
    MISMA fuente del titular "lleva Xh (según la UNE)". `horas` está declarado
    a la hora del parte, así que el inicio absoluto es `fecha - horas` (no
    `generado - horas`: el parte puede tener horas de antigüedad).
    """
    deficit = (estado or {}).get("deficit") or {}
    fecha = deficit.get("fecha")
    if not fecha:
        return {}
    try:
        base = datetime.fromisoformat(fecha)
    except ValueError:
        return {}
    inicio = {}
    for d in deficit.get("circuitos") or []:
        code = d.get("codigo")
        horas = d.get("horas")
        if not code or horas is None:
            continue
        try:
            inicio[code] = base - timedelta(hours=float(horas))
        except (TypeError, ValueError):
            continue
    return inicio


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


def reloj_estado(c):
    """Estado vigente y momento de su último CAMBIO (tz-aware).

    Mismas reglas que `ultimoCambio`/`circuitoVigente` de app.js y circuitos.js:
    el reloj es `estado_desde` (fecha del parte que CAMBIÓ el estado), con
    respaldo en `estado_fecha` (última MENCIÓN) para catálogos viejos; una
    señal vecinal CONTRARIA posterior manda sobre ambas.

    Devuelve (estado, momento, es_cambio_real); (None, None, False) si el
    circuito no tiene estado conocido. `es_cambio` es True cuando hubo una
    transición real (parte o señal vecinal): antes de ese momento el estado
    era el contrario.
    """
    estado = ESTADO_MAP.get(c.get("estado"))
    t0 = None
    es_cambio = False
    for clave in ("estado_desde", "estado_fecha"):
        v = c.get(clave)
        if not v:
            continue
        try:
            t0 = datetime.fromisoformat(v)
        except ValueError:
            continue
        es_cambio = clave == "estado_desde"
        break

    cu = c.get("conteo_usuario") or {}
    if c.get("discrepado") and cu.get("desde"):
        # La UNE dice "con" y los vecinos reportan sin corriente.
        try:
            d = datetime.fromisoformat(cu["desde"])
        except ValueError:
            d = None
        if d and (t0 is None or d > t0):
            estado, t0, es_cambio = "sin", d, True
    elif c.get("estado") == "sin servicio" and c.get("reportado_con") and cu.get("ultimo_con"):
        # La UNE lo mantiene "sin" y los vecinos reportan que volvió.
        try:
            d = datetime.fromisoformat(cu["ultimo_con"])
        except ValueError:
            d = None
        if d and (t0 is None or d > t0):
            estado, t0, es_cambio = "con", d, True

    return estado, t0, es_cambio


def serie_para_circuito(eventos, fines, c, inicios_oficiales=None):
    """Sparkline que respeta el mismo reloj que el titular "X h sin luz"."""
    estado, t0, es_cambio = reloj_estado(c)
    if estado is None:
        return serie_para(eventos, fines)

    # Las horas que declara la UNE (misma fuente del titular "lleva Xh") mandan
    # cuando implican un corte MÁS VIEJO que el reloj del estado. Caso real:
    # AL53 vuelve a las 08:14 y se va a las 08:15 (parpadeo de un minuto); el
    # reloj del estado dice "1 minuto" y el parte de déficit dice "23 h".
    if estado == "sin":
        inicio_oficial = (inicios_oficiales or {}).get(c.get("codigo"))
        if inicio_oficial is not None and (t0 is None or inicio_oficial < t0):
            t0 = inicio_oficial

    if t0 is None:
        serie = serie_para(eventos, fines)
        serie[-1] = estado
        return serie

    previos = [e for e in eventos if e[0] < t0]
    serie = serie_para(previos, fines)
    contrario = "con" if estado == "sin" else "sin"

    if previos:
        ult_dt, ult_estado = previos[-1]
        if ult_estado == estado:
            # Los estados alternan: justo antes del cambio NO puede estar el
            # mismo estado. Es carry rancio (parte sin "👉 CODE" o mención
            # duplicada), así que lo corregimos desde la última mención previa.
            for i, fin in enumerate(fines):
                if ult_dt <= fin < t0 and serie[i] == estado:
                    serie[i] = contrario
    elif es_cambio:
        # Cambio real sin ninguna mención anterior: antes era el contrario.
        for i, fin in enumerate(fines):
            if fin < t0:
                serie[i] = contrario

    for i, fin in enumerate(fines):
        if fin >= t0:
            serie[i] = estado

    return serie


def main():
    with open(CIRCUITOS_JSON, encoding="utf-8") as f:
        circuitos = json.load(f)
    with open(PARTES_JSON, encoding="utf-8") as f:
        partes = json.load(f)
    try:
        with open(ESTADO_JSON, encoding="utf-8") as f:
            estado = json.load(f)
    except (OSError, ValueError):
        estado = {}

    known_codes = {c["codigo"] for c in circuitos.get("circuitos", [])}
    eventos = cargar_eventos(known_codes, partes)
    inicios_oficiales = cargar_inicios_oficiales(estado)
    fines = fin_de_cubos(datetime.fromisoformat(circuitos["generado"]))

    con_serie = 0
    for c in circuitos["circuitos"]:
        serie = serie_para_circuito(
            eventos.get(c["codigo"], []), fines, c, inicios_oficiales
        )
        c["serie_24h"] = serie
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
