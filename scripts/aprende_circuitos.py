"""Aprende CIRCUITOS RECURRENTES del embudo 'por_confirmar' de partes_llm.py.

El LLM ve códigos que la tabla UNE no trae y el catálogo de Telegram aún no
registra (A1328: 65 partes). Como no son conocidos, partes_llm los deja en
`por_confirmar` y estado.circuitos_llm los descarta: nunca entran al catálogo
y la auditoría diaria (chequeo 9) los recomienda cada día. Este script cierra
el embudo SIN RED, leyendo data/partes_llm.json, y emite
data/circuitos_aprendidos.json {CODIGO: {calles, municipio, alias_de, posts,
ejemplo_mensaje}} — commiteado y editable a mano (precedente:
bloques_aprendidos.json).

Un código se PROMUEVE a registro propio solo si cumple todo:
  * aparece en >= 3 mensajes distintos;
  * tiene forma segura (letras+dígitos, o número puro de 4 dígitos — los de
    3 sueltos como '581' colisionan con calles y zonas: son SOLO candidatos a
    alias, nunca registro nuevo);
  * sus calles son ESTABLES: el racimo mayoritario de textos (Jaccard de
    tokens normalizados >= 0.5) cubre la mayoría estricta de los posts con
    calles. Los códigos con calles que rotan post a post son ruido del LLM y
    se descartan;
  * no está en circuitos_falsos (el veto humano de correcciones.json manda)
    ni es ya conocido.

Antes de promover resuelve ALIAS: la UNE a veces escribe el código sin el
prefijo de subestación ('581' por SF581, 'P325' por OP325). Si los dígitos
coinciden con un código conocido y el prefijo es compatible, y las calles
solapan >= 0.8, se registra el alias (es_conocido lo da por conocido y
canonico() enruta la evidencia futura al canónico) en vez de crear un gemelo.
Para los números puros de 3 dígitos —que no pueden promoverse— se acepta
también el alias débil: candidato único con las mismas cifras, municipio de
los partes coincidente con su autoridad y algún topónimo de solape.

build_circuitos llama a main() ANTES de las fases que consumen el catálogo;
el aprendizaje es idempotente (lo ya aprendido o conocido no se reescribe).
"""

import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "extractor"))
import circuitos_id  # noqa: E402
import correcciones  # noqa: E402
from extract import municipios_en  # noqa: E402

RAIZ = os.path.join(os.path.dirname(__file__), "..")
PARTES_FILE = os.path.join(RAIZ, "data", "partes_llm.json")
APRENDIDOS_FILE = circuitos_id.APRENDIDOS_FILE

MIN_POSTS = 3          # apariciones en mensajes distintos
JACCARD_ESTABLE = 0.5  # similitud de textos dentro del racimo estable
ALIAS_COV = 0.8        # solape de calles con el candidato de iguales cifras


def recopilar(partes):
    """{codigo: {ids, textos, munis}} acumulando TODOS los items del caché."""
    ev = defaultdict(lambda: {"ids": set(), "textos": [], "munis": []})
    for mid, v in (partes or {}).items():
        for item in (v.get("circuitos") or []):
            for cod in item.get("codigos") or []:
                e = ev[cod]
                e["ids"].add(str(mid))
                if item.get("calles"):
                    e["textos"].append(item["calles"].strip())
                if item.get("municipio"):
                    e["munis"].append(item["municipio"])
    return ev


def _tokens(t):
    return circuitos_id._tokens(t or "")


def _jac(a, b):
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def estable(textos):
    """(texto representante, tamaño del racimo, n de textos). El representante
    es el texto exacto más frecuente DENTRO del racimo (empate: el más largo,
    como 'longest wins' en build_circuitos)."""
    txs = [t for t in textos if t]
    mejor, mejor_rep = 0, None
    for cand in dict.fromkeys(txs):
        racimo = [t for t in txs if _jac(cand, t) >= JACCARD_ESTABLE]
        if len(racimo) > mejor:
            rep, _ = max(Counter(racimo).items(), key=lambda kv: (kv[1], len(kv[0])))
            mejor, mejor_rep = len(racimo), rep
    return mejor_rep, mejor, len(txs)


def _cifras(cod):
    return re.sub(r"\D", "", cod)


def _prefijo(cod):
    return set(re.sub(r"\d", "", cod))


def autoridades_oficiales():
    """{codigo: [municipios]} de la tabla UNE + corrección manual (para el
    chequeo de municipio del alias débil de números sueltos)."""
    out = {}
    try:
        for cod, info in json.load(open(circuitos_id.OFICIAL_FILE)).items():
            if info.get("municipios"):
                out[cod] = list(info["municipios"])
    except Exception:
        pass
    for cod, m in correcciones.circuitos_municipio().items():
        out.setdefault(cod, []).append(m)
    return out


def buscar_alias(cod, textos, munis, catalogo, autoridad):
    """(canonico|None, confianza). Candidatos: códigos conocidos con las
    MISMAS cifras y prefijo compatible (el parte omite o añade letras de la
    subestación). Con solape >= ALIAS_COV contra el calle de cualquiera de
    sus posts, alias confirmado (el caso P325->OP325, idéntico). Si no: para
    un número puro de 3 dígitos —que nunca puede promoverse— se acepta el
    candidato ÚNICO compatible cuyo municipio de autoridad coincida con el
    de los partes y comparta al menos un topónimo (el caso 581->SF581: las
    tablas oficiales describen SF581 con repartos y el parte lo escribe con
    calles numeradas; el solape puro no llega a 0.8)."""
    cifras = _cifras(cod)
    letras = _prefijo(cod)
    cands = [k for k in catalogo
             if k != cod and _cifras(k) == cifras and
             (letras <= _prefijo(k) or _prefijo(k) <= letras)]
    mejor_cov, mejor_k, mejor_txt = 0.0, None, None
    for k in cands:
        ck = catalogo[k]
        if not ck:
            continue
        for t in textos:
            tk = _tokens(t)
            if not tk:
                continue
            cov = len(tk & ck) / min(len(tk), len(ck))
            if cov > mejor_cov:
                mejor_cov, mejor_k, mejor_txt = cov, k, t
    if mejor_k and mejor_cov >= ALIAS_COV:
        return mejor_k, mejor_cov, mejor_txt
    if cifras.isdigit() and len(cod) == 3 and len(cands) == 1:
        k = cands[0]
        muni = municipio_consistente(munis)
        inter = max((len(_tokens(t) & catalogo[k]) for t in textos), default=0) \
            if catalogo.get(k) else 0
        if muni and muni in autoridad.get(k, []) and inter >= 1:
            return k, mejor_cov, (mejor_txt or (textos[0] if textos else ""))
    return None, mejor_cov, None


def municipio_consistente(munis):
    """Municipio canónico si la mayoría de los posts que lo nombran coinciden
    (extract.municipios_en normaliza '10 de Octubre'/'Diez de Octubre' y
    descarta los ambigüos 'Plaza-Cerro'). None si rota o no dice."""
    resueltos = [ms[0] for m in munis for ms in [municipios_en(m or "")] if len(ms) == 1]
    if not resueltos:
        return None
    nom, n = Counter(resueltos).most_common(1)[0]
    return nom if n * 2 >= len(resueltos) else None


def aprender(partes, catalogo=None, autoridad=None, falsos=None, ya=None):
    """dict de registros NUEVOS {CODIGO: {calles, municipio, alias_de, posts,
    ejemplo_mensaje}}. No toca disco: main() decide si escribe."""
    catalogo = catalogo if catalogo is not None else circuitos_id._catalogo_tokens()
    autoridad = autoridad if autoridad is not None else autoridades_oficiales()
    falsos = falsos if falsos is not None else set(correcciones.circuitos_falsos())
    ya = ya if ya is not None else circuitos_id._aprendidos()
    nuevos = {}
    for cod, e in sorted(recopilar(partes).items()):
        if cod in ya or cod in falsos or circuitos_id.es_conocido(cod):
            continue  # aprendido, vetado a mano o ya en el catálogo
        if len(e["ids"]) < MIN_POSTS:
            continue  # aparición aislada: se queda en el embudo
        # alias ANTES de promover: enruta la evidencia al registro canónico.
        canon, conf, txt = buscar_alias(cod, e["textos"], e["munis"], catalogo, autoridad)
        if canon:
            nuevos[cod] = {"calles": txt or (e["textos"][0] if e["textos"] else ""),
                           "municipio": municipio_consistente(e["munis"]),
                           "alias_de": canon, "posts": len(e["ids"]),
                           "ejemplo_mensaje": max(e["ids"], key=lambda x: int(x) if x.isdigit() else 0)}
            continue
        if not (circuitos_id.RE_COD_LETRAS.match(cod) or
                (circuitos_id.RE_COD_NUM.match(cod) and len(cod) == 4)):
            continue  # número suelto de 3 (o raro): alias o embudo, no registro
        rep, tam, n = estable(e["textos"])
        if n == 0 or tam * 2 <= n:
            continue  # calles que rotan: ruido del LLM, no se aprende
        nuevos[cod] = {"calles": rep, "municipio": municipio_consistente(e["munis"]),
                       "alias_de": None, "posts": len(e["ids"]),
                       "ejemplo_mensaje": max(e["ids"], key=lambda x: int(x) if x.isdigit() else 0)}
    return nuevos


def main():
    try:
        partes = json.load(open(PARTES_FILE))
    except Exception:
        print("aprende_circuitos: sin caché de partes; nada que aprender")
        return
    actuales = dict(circuitos_id._aprendidos())
    nuevos = aprender(partes)
    # editable a mano: una clave ya aprendida NUNCA se pisa (borrarla del JSON
    # es la forma de forzar su re-aprendizaje).
    pendientes = {c: r for c, r in nuevos.items() if c not in actuales}
    if pendientes:
        destino = {**actuales, **pendientes}
        tmp = APRENDIDOS_FILE + ".tmp"
        json.dump({k: destino[k] for k in sorted(destino)},
                  open(tmp, "w"), ensure_ascii=False, indent=1)
        os.replace(tmp, APRENDIDOS_FILE)
        circuitos_id.recargar()  # el build de ESTA corrida los ve conocidos
    promos = {c: r for c, r in pendientes.items() if not r["alias_de"]}
    alias = {c: r for c, r in pendientes.items() if r["alias_de"]}
    print(f"aprende_circuitos: {len(pendientes)} códigos nuevos "
          f"({len(promos)} promovidos, {len(alias)} alias), "
          f"{len(actuales) + len(pendientes)} en el archivo aprendido")
    for c, r in sorted(promos.items(), key=lambda x: -x[1]["posts"]):
        print(f"  + {c} ({r['posts']} posts, {r['municipio'] or 'municipio rota'}): "
              f"{(r['calles'] or '')[:60]}")
    for c, r in sorted(alias.items(), key=lambda x: -x[1]["posts"]):
        print(f"  ~ {c} -> {r['alias_de']} ({r['posts']} posts): "
              f"{(r['calles'] or '')[:60]}")


if __name__ == "__main__":
    main()
