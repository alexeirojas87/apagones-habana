"""Precomputa los agregados que el chatbot necesita para hablar del pasado.

web/data/analitica.json pesa ~4 MB (42.930 eventos, 2.788 registros de
circuitos): el worker no puede descargarlo por mensaje. Aquí se reduce a unos
pocos KB con las respuestas ya calculadas — sumas y rankings hechos en Python,
no por el LLM contando líneas a mano, que es donde estos modelos fallan callados.

Deliberadamente NO se copian resumen_diario ni patrones de analitica.json:
son textos generados que todavía razonan por bloque, y la Empresa dejó de
reportar así. Aquí solo entran cifras derivadas de eventos reales.

Entrada:  web/data/analitica.json
Salida:   web/data/bot_datos.json
"""

import json
import os
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

RAIZ = os.path.join(os.path.dirname(__file__), "..")
ANALITICA = os.path.join(RAIZ, "web", "data", "analitica.json")
ESTADO = os.path.join(RAIZ, "web", "data", "estado.json")
CATALOGO = os.path.join(RAIZ, "web", "data", "circuitos.json")
SALIDA = os.path.join(RAIZ, "web", "data", "bot_datos.json")

DIAS = 30          # ventana de histórico que ofrece el bot
TOP_CIRCUITOS = 40  # ranking de peores

# Respaldo si estado.json no está disponible: los 15 municipios de La Habana.
MUNICIPIOS_FALLBACK = [
    "10 de Octubre", "Arroyo Naranjo", "Boyeros", "Centro Habana", "Cerro",
    "Cotorro", "Guanabacoa", "Habana Vieja", "Habana del Este", "La Lisa",
    "Marianao", "Playa", "Plaza", "Regla", "San Miguel del Padrón",
]


def _norm(s):
    """Minúsculas, sin acentos y con los espacios colapsados (incluido NBSP)."""
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip().lower()


def canonicos():
    try:
        with open(ESTADO) as fh:
            nombres = list(json.load(fh).get("municipios") or {})
    except Exception:
        nombres = []
    return [(n, _norm(n)) for n in (nombres or MUNICIPIOS_FALLBACK)]


def episodios(regs):
    """Duración de cada corte, a partir de los partes de un circuito.

    OJO: cada parte declara las horas ACUMULADAS del corte en curso, no un
    incremento. Un mismo apagón aparece varias veces con el contador subiendo
    (8.4, 11.6, 14.4, ... 26.4) y sumar esos valores multiplica la cifra real
    por 3 o 4. Un valor MENOR que el anterior significa que aquel corte
    terminó y empezó otro, así que cada tramo creciente cuenta una sola vez,
    por su máximo.
    """
    eps, actual = [], None
    for _fecha, h in sorted(regs):
        if actual is not None and h < actual:
            eps.append(actual)   # el contador se reinició: cierra el corte
            actual = h
        else:
            actual = h
    if actual is not None:
        eps.append(actual)
    return eps


HUECO_MAX_H = 12  # sin parte nuevo en 12 h, asumimos que el corte terminó


def cortes_intervalos(regs, ahora):
    """Tramos (inicio, fin) de cada corte, en UTC, a partir de las declaraciones.

    Cada parte (fecha t, horas h) dice que el corte estuvo vivo durante
    [t - h, t]. Declaraciones consecutivas del MISMO corte se solapan (el
    contador crece menos rápido que el reloj), así que se fusionan; un contador
    que baja significa que el corte anterior terminó y empezó otro: NUNCA se
    fusiona con el anterior, ni aunque el nuevo empiece cerca (una
    restablecida de minutos entre dos partes no borra el corte cerrado).
    Si el último tramo termina a menos de HUECO_MAX_H de *ahora*, el corte
    sigue abierto y se extiende hasta ahora: el apagón continúa aunque la UNE
    no haya publicado el parte de la hora en curso.
    """
    tramos = []
    prev_h = None
    for fecha, h in sorted(regs):
        # fechas de analitica.json son naive en UTC (sin segundos)
        t = datetime.fromisoformat(fecha).replace(tzinfo=timezone.utc)
        tramos.append((prev_h is not None and h < prev_h, t - timedelta(hours=h), t))
        prev_h = h
    cortes = []
    for reinicio, ini, fin in tramos:
        if (cortes and not reinicio
                and ini <= cortes[-1][1] + timedelta(hours=1)):
            cortes[-1] = (cortes[-1][0], max(cortes[-1][1], fin))
        else:
            cortes.append((ini, fin))
    if cortes and ahora - cortes[-1][1] < timedelta(hours=HUECO_MAX_H):
        cortes[-1] = (cortes[-1][0], max(cortes[-1][1], ahora))
    return cortes


def horas_por_dia(cortes, tz=ZoneInfo("America/Havana")):
    """Reparte las horas de cada corte en días LOCALES habaneros.

    Un corte que cruza la medianoche cuenta a cada día sus horas: es la serie
    que responde '¿cuántas horas sin corriente lleva el circuito X hoy?'.
    Devuelve {dia: horas} con solo los días con >0.05 h.
    """
    por_dia = defaultdict(float)
    for ini, fin in cortes:
        t = ini.astimezone(tz)
        while t < fin:
            dia = t.date()
            medianoche = (datetime.combine(dia, datetime.min.time(), tzinfo=tz)
                          + timedelta(days=1))
            tramo_fin = min(fin.astimezone(tz), medianoche)
            h = (tramo_fin - t).total_seconds() / 3600
            if h > 0.05:
                por_dia[dia.isoformat()] += h
            t = tramo_fin
    return {d: round(h, 1) for d, h in sorted(por_dia.items())}


def declaracion_corte_abierto(estado, estado_fecha, ahora, max_h=24):
    """(fecha, horas) sintética si el catálogo marca el circuito SIN servicio.

    Los partes de déficit declaran horas, pero llegan tarde: un circuito puede
    estar caído desde hace horas (eventos de afectación, lo que la ficha de la
    web muestra como 'lleva Nh') sin declaración nueva. Sin esta cola, el bot
    respondía '0.2 h hoy' mientras la web decía 'sin servicio hace 1.5 h'.
    Devuelve None si no está sin servicio, sin fecha o hace más de max_h.
    """
    if estado != "sin servicio" or not estado_fecha:
        return None
    try:
        t = datetime.fromisoformat(estado_fecha)
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    horas = (ahora - t).total_seconds() / 3600
    if not 0 < horas <= max_h:
        return None
    return (ahora.isoformat(), horas)


def resolver_municipios(bruto, canon):
    """Mapea un municipio crudo a los canónicos que menciona.

    Los partes traen variantes ("10 de octubre", "10  de Octubre") y también
    compuestos que nombran DOS municipios ("Centro Habana - Habana Vieja",
    "Plaza/Cerro"). Sin esto salían 29 municipios en una provincia que tiene 15,
    con los conteos partidos entre variantes de la misma zona.
    """
    t = _norm(bruto)
    if not t:
        return []
    hallados = [nombre for nombre, n in canon if n and n in t]
    # "Plaza de la Revolución" contiene "Plaza": nos quedamos con lo hallado.
    return hallados or [str(bruto).replace("\xa0", " ").strip()]


def main():
    with open(ANALITICA) as fh:
        a = json.load(fh)

    corte = (datetime.now(timezone.utc) - timedelta(days=DIAS)).strftime("%Y-%m-%dT%H:%M")
    canon = canonicos()

    # --- Actividad por municipio y por día, desde los eventos oficiales ---
    municipios = defaultdict(lambda: {"afectaciones": 0, "restablecimientos": 0,
                                      "averias": 0, "ultima_afectacion": None})
    dias = defaultdict(lambda: {"afectaciones": 0, "restablecimientos": 0, "averias": 0})
    causas = defaultdict(int)

    for ev in a.get("eventos", []):
        # [fecha, tipo, bloque(obsoleto), causa, municipios]
        fecha, tipo = ev[0], ev[1]
        if fecha < corte:
            continue
        causa = ev[3] if len(ev) > 3 else None
        muns = ev[4] if len(ev) > 4 and isinstance(ev[4], list) else []
        clave = "afectaciones" if tipo == "afectacion" else "restablecimientos"
        dias[fecha[:10]][clave] += 1
        if causa:
            causas[causa] += 1
        for bruto in muns:
            for m in resolver_municipios(bruto, canon):
                municipios[m][clave] += 1
                if tipo == "afectacion":
                    prev = municipios[m]["ultima_afectacion"]
                    if not prev or fecha > prev:
                        municipios[m]["ultima_afectacion"] = fecha

    for av in a.get("averias", []):
        fecha, _tipo, mun = av[0], av[1], (av[2] if len(av) > 2 else None)
        if fecha < corte:
            continue
        dias[fecha[:10]]["averias"] += 1
        for m in resolver_municipios(mun, canon):
            municipios[m]["averias"] += 1

    # --- Horas sin servicio por circuito ---
    horas = defaultdict(list)
    ultima = {}
    for reg in a.get("circuitos_partes", []):
        fecha, codigo, h = reg[0], reg[1], reg[2]
        if fecha < corte or not codigo:
            continue
        try:
            horas[codigo].append((fecha, float(h)))
        except (TypeError, ValueError):
            continue
        if codigo not in ultima or fecha > ultima[codigo]:
            ultima[codigo] = fecha

    circuitos = {}
    horas_dia = {}
    # referencia para el corte abierto: el builder corre recién hecho el volcado,
    # así que now() es el mejor "ahora" disponible
    ahora = datetime.now(timezone.utc)
    # estado por circuito del catálogo (en CI es el de la corrida anterior:
    # build_circuitos corre después; como mucho va atrasado un ciclo de ingesta)
    estado_cat = {}
    try:
        for c in json.load(open(CATALOGO)).get("circuitos", []):
            estado_cat[c.get("codigo")] = (c.get("estado"), c.get("estado_fecha"))
    except Exception:
        estado_cat = {}
    for codigo, regs in horas.items():
        eps = episodios(regs)
        if not eps:
            continue
        circuitos[codigo] = {
            "cortes": len(eps),
            "horas_max": round(max(eps), 1),
            "horas_media": round(sum(eps) / len(eps), 1),
            "horas_total": round(sum(eps), 1),
            "ultima": ultima.get(codigo),
        }
        # serie diaria (días habaneros): lo que consume el bot para
        # "¿cuántas horas lleva sin corriente HOY?" — suma la cola del
        # corte abierto (declarada o por estado del catálogo), por eso
        # puede diferir de horas_total (que solo cuenta lo declarado).
        regs_serie = list(regs)
        sintetica = declaracion_corte_abierto(*estado_cat.get(codigo, (None, None)),
                                              ahora)
        if sintetica:
            regs_serie.append(sintetica)
        serie = horas_por_dia(cortes_intervalos(regs_serie, ahora))
        if serie:
            horas_dia[codigo] = serie

    # Peores por horas acumuladas reales: más representativo que la media, que
    # premia a un circuito con un único corte largo.
    ranking = sorted(circuitos.items(), key=lambda kv: kv[1]["horas_total"], reverse=True)
    ranking_peores = [{"codigo": c, **v} for c, v in ranking[:TOP_CIRCUITOS]]

    # --- Déficit de generación ---
    mw = [m for m in a.get("mw", []) if m[0] >= corte]
    mw_serie = [{"fecha": m[0], "mw": m[1]} for m in mw[-60:]]
    valores = [m[1] for m in mw if isinstance(m[1], (int, float))]

    salida = {
        "generado": datetime.now(timezone.utc).isoformat(),
        "ventana_dias": DIAS,
        "municipios": {k: v for k, v in sorted(municipios.items())},
        "circuitos": circuitos,
        "horas_dia": horas_dia,
        "ranking_peores": ranking_peores,
        "serie_diaria": [{"fecha": d, **v} for d, v in sorted(dias.items())],
        "causas": dict(sorted(causas.items(), key=lambda kv: -kv[1])),
        "deficit_mw": {
            "reciente": mw_serie,
            "max": max(valores) if valores else None,
            "min": min(valores) if valores else None,
            "media": round(sum(valores) / len(valores)) if valores else None,
        },
    }

    with open(SALIDA, "w") as fh:
        json.dump(salida, fh, ensure_ascii=False)

    kb = os.path.getsize(SALIDA) / 1024
    print(f"bot_datos: {len(circuitos)} circuitos, {len(municipios)} municipios, "
          f"{len(salida['serie_diaria'])} días, {kb:.0f} KB")


if __name__ == "__main__":
    main()
