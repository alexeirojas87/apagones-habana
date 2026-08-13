"""Corrige el catálogo publicado para que no contradiga lo que el LLM extrajo.

Verifica el RESULTADO, no la extracción. comparar_extraccion.py ya medía que el
LLM veía circuitos que el regex no, y lo registró a diario durante un mes (116
de 117 discrepancias a favor del LLM) sin que nadie pudiera actuar: la
comparación ocurría ANTES del punto donde el pipeline decidía si aplicar el dato.
Ahí estuvo el fallo que publicó AL53 como "con servicio" estando afectado.

Aquí se compara al final de la cadena: para cada circuito, el último parte que
declara su estado CON evidencia del código escrito en el texto manda sobre lo
publicado. Si el catálogo dice otra cosa y el veredicto del LLM es más reciente,
se corrige.

Es idempotente: con el pipeline sano no corrige nada (medido: 0 correcciones).
Si empieza a corregir, algo aguas arriba se rompió — el contador es la señal.

Entrada/salida: web/data/circuitos.json (se reescribe si hay correcciones)
Lectura:        data/partes_llm.json
"""

import json
import os
import sys

RAIZ = os.path.join(os.path.dirname(__file__), "..")
CATALOGO = os.path.join(RAIZ, "web", "data", "circuitos.json")
LLM_FILE = os.path.join(RAIZ, "data", "partes_llm.json")


def veredictos(llm_cache):
    """codigo -> (fecha, estado, message_id) del último parte que lo declara.

    Solo cuentan los códigos en `codigos_estado`: los que están literalmente
    escritos en el parte. Un circuito nombrado solo por sus calles no puede
    cambiar de estado, porque la coincidencia por calle no es evidencia.
    """
    out = {}
    for mid, v in (llm_cache or {}).items():
        if v.get("via") != "llm":
            continue
        fecha = v.get("fecha") or ""
        for item in v.get("circuitos") or []:
            estado = item.get("estado")
            if not estado:
                continue
            for cod in item.get("codigos_estado") or []:
                if cod not in out or fecha > out[cod][0]:
                    out[cod] = (fecha, estado, mid)
    return out


def corregir(circuitos, llm_cache):
    """Devuelve (circuitos, correcciones). No muta la entrada en sitio."""
    verdad = veredictos(llm_cache)
    correcciones = []
    for c in circuitos:
        v = verdad.get(c.get("codigo"))
        if not v:
            continue
        fecha, estado, mid = v
        if c.get("estado") == estado:
            continue
        # El catálogo puede tener algo MÁS NUEVO (otro parte posterior, o el
        # regex sobre un post que el LLM aún no procesó): eso no es un fallo.
        if (str(c.get("estado_fecha") or "")[:16]) >= fecha[:16]:
            continue
        correcciones.append({
            "codigo": c["codigo"], "antes": c.get("estado"), "ahora": estado,
            "fecha_antes": str(c.get("estado_fecha") or "")[:16],
            "fecha_ahora": fecha[:16], "post": mid,
        })
        c["estado"] = estado
        c["estado_fecha"] = fecha
        c["ultima_message_id"] = int(mid) if str(mid).isdigit() else mid
    return circuitos, correcciones


def main():
    try:
        catalogo = json.load(open(CATALOGO))
        llm_cache = json.load(open(LLM_FILE))
    except Exception as e:
        print(f"coherencia: no se pudo leer ({e}); se omite")
        return 0

    _, correcciones = corregir(catalogo.get("circuitos") or [], llm_cache)
    if not correcciones:
        print("coherencia: catálogo consistente con el LLM, 0 correcciones")
        return 0

    json.dump(catalogo, open(CATALOGO, "w"), ensure_ascii=False)
    print(f"coherencia: {len(correcciones)} circuito(s) CORREGIDOS "
          f"(el catálogo contradecía al LLM):")
    for c in correcciones[:20]:
        print(f"  {c['codigo']}: '{c['antes']}' ({c['fecha_antes']}) -> "
              f"'{c['ahora']}' ({c['fecha_ahora']}, post {c['post']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
