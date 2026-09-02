"""Resolutor de IDENTIDAD de circuitos: un solo lugar para reconocer y validar
códigos de circuito, en vez de regex repetidos por 4 scripts. La UNE no es
consistente ("👉AL56 - Zonas:", "💥Circuito AL56", "L317/1244", "1243-5 horas"),
así que aquí viven las reglas seguras y el matching por calles.

API:
  normalizar_codigo("l317 / 1244") -> ["L317", "1244"]
  identificar("AL56 - Zonas: 13...") -> (["AL56"], "Zonas: 13...")  # o ([], texto)
  casar_por_calles("Zonas: 13; 15 y Micro X") -> ("AL56", 0.83)     # o (None, 0)
  es_conocido("AL56") -> True (catálogo oficial o aprendido de Telegram)
  canonico("581") -> "SF581" (alias aprendido resuelve al código canónico)
"""

import json
import os
import re
import unicodedata

RAIZ = os.path.join(os.path.dirname(__file__), "..")
OFICIAL_FILE = os.path.join(RAIZ, "data", "circuitos_oficial.json")
CATALOGO_FILE = os.path.join(RAIZ, "web", "data", "circuitos.json")
# Aprendizaje de partes recurrentes (aprende_circuitos.py): precedent
# bloques_aprendidos.json — commiteado y editable a mano.
APRENDIDOS_FILE = os.path.join(RAIZ, "data", "circuitos_aprendidos.json")

# Código: letras+dígitos siempre (P318, AL56, CPP20); números puros son ambiguos
# (calles "23", zonas "13") -> solo 3-4 dígitos y NUNCA sueltos dentro del texto.
RE_COD_LETRAS = re.compile(r"^[A-Za-z]{1,3}\d{1,4}$")
RE_COD_NUM = re.compile(r"^\d{3,4}$")

# Palabras de calles que no identifican nada por sí solas (ruido del matching).
_VACIAS = {"calle", "calles", "desde", "hasta", "entre", "y", "e", "la", "el",
           "los", "las", "de", "del", "reparto", "repartos", "zona", "zonas",
           "avenida", "ave", "final", "parte", "esquina"}


def _sin_tildes(s):
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def normalizar_codigo(texto):
    """'l317 / 1244' o 'T44 y T41' -> lista de códigos; inválidos se descartan."""
    out = []
    for pieza in re.split(r"\s*[,/]\s*|\s+[yeYE]\s+", str(texto or "")):
        p = pieza.strip().replace(" ", "").upper()
        if RE_COD_LETRAS.match(p) or RE_COD_NUM.match(p):
            out.append(p)
    return out


def identificar(texto):
    """Código(s) al INICIO de un texto de parte y el resto (la dirección).
    Reglas seguras: con letras siempre; números puros exigen 3-4 dígitos y
    separador explícito (no confundir '13; 15; 16' ni '23-B y 41').
    Devuelve (codigos, resto)."""
    t = (texto or "").strip()
    # códigos separados por barra o por " y " ("T44 y T41 Toyo...")
    sep = r"(?:\s*/\s*|\s+[yeYE]\s+)"
    m = (re.match(rf"((?:[A-Za-z]{{1,3}}\d{{1,4}})(?:{sep}(?:[A-Za-z]{{1,3}}\d{{1,4}}|\d{{3,4}}))*)"
                  r"\b\s*[-–:]?\s*(.*)", t)
         or re.match(rf"((?:\d{{3,4}})(?:{sep}(?:[A-Za-z]{{1,3}}\d{{1,4}}|\d{{3,4}}))*)"
                     r"\s*[-–:]\s*(.*)", t))
    if not m:
        return [], t
    return normalizar_codigo(m.group(1)), (m.group(2) or "").strip()


def _tokens(texto):
    plano = _sin_tildes((texto or "").lower())
    toks = set()
    for w in re.findall(r"[a-z0-9]+", plano):
        if w not in _VACIAS and (len(w) > 2 or w.isdigit()):
            toks.add(w)
    return toks


_CATALOGO = None  # {codigo: tokens de calles} — se carga una vez
_APRENDIDOS = None  # {codigo: registro} de data/circuitos_aprendidos.json (una vez)


def _aprendidos():
    """Registros aprendidos de partes recurrentes (aprende_circuitos.py).
    Best effort: sin archivo o roto = nada aprendido (el pipeline regex sigue)."""
    global _APRENDIDOS
    if _APRENDIDOS is None:
        try:
            _APRENDIDOS = json.load(open(APRENDIDOS_FILE))
        except Exception:
            _APRENDIDOS = {}
    return _APRENDIDOS


def recargar():
    """Olvida las cachés de catálogo y aprendidos. build_circuitos la llama
    después de que el aprendiz escribe el archivo, para que los códigos
    promovidos en ESTA corrida cuenten ya como conocidos."""
    global _CATALOGO, _APRENDIDOS
    _CATALOGO = None
    _APRENDIDOS = None


def canonico(codigo):
    """Resuelve un alias aprendido hacia su código canónico. '581' (bare) ->
    'SF581' si el aprendiz aprendió que son el mismo circuito. Un código que no
    es alias se devuelve tal cual. La cadena de alias se sigue un máximo corto
    de saltos para no loops si alguien edita el JSON a mano."""
    ap = _aprendidos()
    c = str(codigo or "").strip().upper()
    for _ in range(4):
        reg = ap.get(c)
        destino = (reg or {}).get("alias_de")
        if not destino or destino == c:
            break
        c = destino
    return c


def _catalogo_tokens():
    global _CATALOGO
    if _CATALOGO is not None:
        return _CATALOGO
    _CATALOGO = {}
    try:
        for cod, info in json.load(open(OFICIAL_FILE)).items():
            _CATALOGO[cod] = _tokens(" ".join((info.get("calles") or {}).values()))
    except Exception:
        pass
    try:
        for c in json.load(open(CATALOGO_FILE)).get("circuitos", []):
            if c.get("calles") and c["codigo"] not in _CATALOGO:
                _CATALOGO[c["codigo"]] = _tokens(c["calles"])
    except Exception:
        pass
    # no alias aprendidos: ya son circuitos del catálogo (aún no en el JSON de
    # web hasta que build_circuitos los siembre). Los alias NO se añaden aquí:
    # resuelven a su canónico y no deben generar gemelos en el matching.
    for cod, reg in _aprendidos().items():
        if not reg.get("alias_de") and cod not in _CATALOGO:
            _CATALOGO[cod] = _tokens(reg.get("calles") or "")
    return _CATALOGO


def casar_por_calles(texto, umbral=0.6):
    """Sin código explícito: ¿a qué circuito del catálogo corresponden estas
    calles/zonas? Solapamiento de tokens (Jaccard sobre el más chico). Se acepta
    solo con confianza >= umbral Y claramente mejor que el segundo candidato
    (>= 1.5x): preferimos 'no sé' a equivocarnos de circuito.
    Devuelve (codigo|None, confianza)."""
    toks = _tokens(texto)
    if len(toks) < 2:
        return None, 0.0
    # puntuación primaria: cobertura (intersección / conjunto más chico);
    # desempate: Jaccard real (favorece la coincidencia EXACTA sobre el circuito
    # que solo contiene esas palabras entre muchas otras).
    cands = []
    for cod, ctoks in _catalogo_tokens().items():
        if not ctoks:
            continue
        inter = len(toks & ctoks)
        if not inter:
            continue
        cands.append((inter / min(len(toks), len(ctoks)),
                      inter / len(toks | ctoks), cod))
    cands.sort(reverse=True)
    if not cands:
        return None, 0.0
    mejor = cands[0]
    seg = cands[1] if len(cands) > 1 else (0.0, 0.0, None)
    domina = (mejor[0] / max(seg[0], 1e-9) >= 1.5) or \
             (mejor[0] == seg[0] and mejor[1] / max(seg[1], 1e-9) >= 1.5)
    if mejor[0] >= umbral and domina:
        return mejor[2], round(mejor[0], 2)
    return None, round(mejor[0], 2)


def es_conocido(codigo):
    """¿Está en el catálogo (oficial, de la web o aprendido de partes)? Los
    alias aprendidos cuentan como conocidos porque resuelven a su canónico:
    así el validador de partes_llm deja de marcarlos 'por_confirmar' y el
    embudo de códigos recurrentes se vacía solo ('581' -> SF581)."""
    return canonico(codigo) in _catalogo_tokens()


def resolver(texto):
    """Todo junto: código explícito si lo hay (validado), si no matching por
    calles. Devuelve {codigos, direccion, via, confianza, por_confirmar}.
    Los códigos conocidos salen en su forma CANÓNICA (un alias aprendido
    routing-a a su registro: '581' -> 'SF581'); los dudosos se devuelven tal
    cual para no inventar identidades."""
    codigos, resto = identificar(texto)
    if codigos:
        codigos = [canonico(c) if es_conocido(c) else c for c in codigos]
        return {"codigos": codigos, "direccion": resto, "via": "codigo",
                "confianza": 1.0,
                "por_confirmar": [c for c in codigos if not es_conocido(c)]}
    # número 3-4 dígitos SIN separador ("1170 Calles 42 desde...") solo se acepta
    # si ya es un circuito conocido del catálogo (si no, sería una calle).
    m = re.match(r"(\d{3,4})\s+(.+)", (texto or "").strip())
    if m and es_conocido(m.group(1)):
        return {"codigos": [canonico(m.group(1))], "direccion": m.group(2).strip(),
                "via": "codigo_catalogo", "confianza": 1.0, "por_confirmar": []}
    cod, conf = casar_por_calles(texto)
    if cod:
        return {"codigos": [cod], "direccion": texto.strip(), "via": "calles",
                "confianza": conf, "por_confirmar": []}
    return {"codigos": [], "direccion": texto.strip(), "via": None,
            "confianza": 0.0, "por_confirmar": []}
