"""Purga datos viejos de Supabase para mantener el proyecto dentro de la cuota.

El histórico LARGO ya no vive en la DB: los lectores incrementales lo guardan
en los cachés commiteados del repo (data/analitica_raw.json, data/canal_cache.json,
data/partes_llm.json), y la analítica y el catálogo se construyen desde allí.
Borrar aquí no afecta a las estadísticas de varios días.

Retenciones (días, configurables por env):
  PURGA_COMENTARIOS_DIAS=60       mensajes del grupo de comentarios (216k filas,
                                  la mayor parte de la DB; nada lee los viejos)
  PURGA_COMENTARIOS_LLM_DIAS=120  enriquecidos por LLM (analitica los tiene en caché)
  PURGA_FRAGMENTOS_DIAS=90        índice semántico (coincide con DIAS_HISTORICO_BOT)
  PURGA_EVENTOS_DIAS=365          eventos extraídos (analitica los tiene en caché)
  PURGA_CANAL_DIAS=365            mensajes del canal (canal_cache los conserva)

Seguridad (por eso puede correr solo en cada ingesta):
  1. Los cachés incrementales deben existir y ser frescos (< 48 h): son la copia
     del histórico que la DB va a borrar. Sin ellos, la purga se omite.
  2. El borrado va por franjas de fechas y se detiene al llegar al fondo de los
     datos, así la primera purga (masiva) y las de mantenimiento cuestan lo mismo.
  3. PURGA_DRY_RUN=1 solo cuenta lo que borraría (útil la primera vez).
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

RAIZ = os.path.join(os.path.dirname(__file__), "..")
BASE = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1"
KEY = os.environ["SUPABASE_SERVICE_KEY"]
MAX_HORAS_CACHE = 48   # si un caché es más viejo, la purga se auto-deshabilita
PASO_SIN_DATOS = 3     # franjas consecutivas vacías antes de dar por alcanzado el fondo

# (tabla, filtros extra, columna de fecha, retención días, días por franja)
TABLAS = [
    ("mensajes", {"chat": "eq.comentarios"}, "fecha",
     int(os.environ.get("PURGA_COMENTARIOS_DIAS", "60")), 7),
    ("mensajes", {"chat": "eq.canal"}, "fecha",
     int(os.environ.get("PURGA_CANAL_DIAS", "365")), 30),
    ("comentarios_llm", {}, "fecha",
     int(os.environ.get("PURGA_COMENTARIOS_LLM_DIAS", "120")), 30),
    ("eventos", {}, "fecha",
     int(os.environ.get("PURGA_EVENTOS_DIAS", "365")), 30),
    ("chatbot_fragmentos", {}, "fecha",
     int(os.environ.get("PURGA_FRAGMENTOS_DIAS", "90")), 30),
]


def _iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def _request(tabla, params, method="GET", timeout=300, headers=None):
    url = f"{BASE}/{tabla}?{urllib.parse.urlencode(params)}"
    base = {"apikey": KEY, "authorization": f"Bearer {KEY}"}
    if headers:
        base.update(headers)
    req = urllib.request.Request(url, method=method, headers=base)
    return urllib.request.urlopen(req, timeout=timeout)


def _franja(tabla, filtros, col_fecha, gte, lt):
    """Query params de la franja [gte, lt) de fechas. Los filtros van como lista
    de pares porque fecha aparece dos veces (gte y lt) y un dict las pisa."""
    return list(filtros.items()) + [(col_fecha, f"gte.{_iso(gte)}"),
                                    (col_fecha, f"lt.{_iso(lt)}")]


def _contar(tabla, filtros, col_fecha, gte, lt):
    """Filas en la franja [gte, lt). Devuelve 0 si la franja está vacía.

    El conteo exacto va en el header content-range (Prefer: count=exact +
    Range de una fila: no baja los datos)."""
    params = _franja(tabla, filtros, col_fecha, gte, lt) + [("select", col_fecha)]
    try:
        with _request(tabla, params, headers={"prefer": "count=exact",
                                              "range": "0-0"}) as r:
            cr = r.headers.get("content-range", "*/0")
            total = cr.split("/")[1]
            return 0 if total == "*" else int(total)
    except urllib.error.HTTPError as e:
        # rango vacío (416): con count=exact el total viene en content-range
        if e.code == 416:
            cr = e.headers.get("content-range", "*/0")
            total = cr.split("/")[1]
            return 0 if total == "*" else int(total)
        raise


def _borrar(tabla, filtros, col_fecha, gte, lt):
    with _request(tabla, _franja(tabla, filtros, col_fecha, gte, lt),
                  method="DELETE") as r:
        r.read()


def cache_frescos():
    """Los cachés que preservan el histórico deben existir y ser recientes."""
    rutas = [os.path.join(RAIZ, "data", "analitica_raw.json"),
             os.path.join(RAIZ, "data", "canal_cache.json")]
    for r in rutas:
        try:
            viejo = time.time() - os.path.getmtime(r) > MAX_HORAS_CACHE * 3600
        except OSError:
            print(f"purga: no existe {os.path.basename(r)}; se omite la purga "
                  "(corre primero build_analitica y build_circuitos)")
            return False
        if viejo:
            print(f"purga: {os.path.basename(r)} tiene más de {MAX_HORAS_CACHE} h "
                  "(¿falló la ingesta?); se omite la purga por seguridad")
            return False
    return True


def purgar_tabla(tabla, filtros, col_fecha, dias, paso_dias, ahora, dry_run):
    corte = ahora - timedelta(days=dias)
    fondo = corte - timedelta(days=730)
    borrado, sin_datos, franjas = 0, 0, 0
    fin = corte
    while fin > fondo:
        franjas += 1
        ini = fin - timedelta(days=paso_dias)
        n = _contar(tabla, filtros, col_fecha, ini, fin)
        if n:
            sin_datos = 0
            if dry_run:
                print(f"  {tabla} {list(filtros.values()) or ''}: "
                      f"{n} filas entre {ini:%Y-%m-%d} y {fin:%Y-%m-%d}")
            else:
                _borrar(tabla, filtros, col_fecha, ini, fin)
                borrado += n
        else:
            sin_datos += 1
            if sin_datos >= PASO_SIN_DATOS:
                break
        fin = ini
    if dry_run:
        print(f"  {tabla} {list(filtros.values()) or ''}: dry-run terminado "
              f"({franjas} franjas revisadas)")
    else:
        print(f"  {tabla} {list(filtros.values()) or ''}: {borrado} filas borradas "
              f"(retención {dias} días, {franjas} franjas)")


def main():
    dry_run = os.environ.get("PURGA_DRY_RUN") == "1"
    if not cache_frescos():
        return
    ahora = datetime.now(timezone.utc)
    modo = "dry-run" if dry_run else "REAL"
    print(f"purga: {modo} (retenciones: " + ", ".join(
        f"{t[0]}{(' ' + ','.join(t[1].values())) if t[1] else ''}={t[3]}d"
        for t in TABLAS) + ")")
    # una tabla que falle (red, gateway) no tumba la ingesta: la próxima corrida
    # retoma la franja donde quedó
    for tabla, filtros, col_fecha, dias, paso_dias in TABLAS:
        try:
            purgar_tabla(tabla, filtros, col_fecha, dias, paso_dias, ahora, dry_run)
        except Exception as e:
            print(f"  AVISO: purga de {tabla} falló ({e}); continúa la siguiente")


if __name__ == "__main__":
    main()
