# Circuit polygons layer

## Objective

Render each electrical circuit as a closed territory on the map, using the
Info UNE boundary layer as the seed, and keep the existing approximate
line/circle rendering only for circuits that still have no boundary.

## Problem

`web/app.js` draws circuits as street polylines or a 450 m circle. That makes
the map unreadable at city scale, prevents "which circuit am I in?" detection,
and cannot show the real service area. Info UNE already publishes a curated
GeoJSON of 197 circuits (219 polygons) whose borders follow OSM street
centerlines (median 4 m).

## Scope

In scope:

- Fetch and normalize the public Info UNE geometry layer into
  `web/data/circuitos_geometrias.geojson`.
- Keep the original attribution and mark provisional shapes.
- Fill codes that Info UNE does not cover with provisional polygons when we
  already have geocoded street lines.
- Render polygons, colored by circuit state, in `web/app.js`.
- Keep popups, tooltips, the layer toggle and `?c=CODE` focus working.
- Add a structural test for the new data file.

Out of scope:

- Building boundaries for the 76 codes with only a point or no geometry.
- Touching SEO files (`web/index.html`, `web/circuitos.html`, `scripts/build_seo.py`,
  tests modified by the parallel SEO session).
- Changing data ingestion, cron jobs or the circuit catalog.

## Constraints

- Do not modify files already dirty from the parallel SEO session.
- Keep the GeoJSON self-contained and static in `web/data/`.
- Attribute the source in the popup; do not strip the provenance.
- No duplicated rendering for features shared by several circuit codes.
- Data file must stay under ~250 KB.

## Tasks

- [x] T1 — Add `scripts/build_circuitos_geometrias.py` (route: delegated writer).
      Download `https://infoune.duckdns.org/api/v1/geometrias/circuitos-lh`,
      normalize properties (`codigos`, `codigo`, `municipio`, `fuente`,
      `provisional`), drop invalid polygons, round to 6 decimals.
- [x] T2 — Generate provisional polygons for our codes that have `lineas`
      in `web/data/circuitos.json` but no Info UNE polygon (buffer 120 m,
      dissolve, simplify). Mark them `provisional: true`.
- [x] T3 — Write `web/data/circuitos_geometrias.geojson` and verify counts
      (>=190 covered codes, valid polygons, no duplicate code+geometry).
- [x] T4 — Render polygons in `web/app.js` with state colors, popups,
      tooltips, shared-feature handling and fallback to line/circle.
- [x] T5 — Add `tests/test_geometrias_circuitos.py` structural checks.

## Acceptance criteria

- Map shows filled circuit territories colored by current state.
- Circuits without a polygon still render as today (line or 450 m circle).
- Shared polygons render once.
- Popup identifies the boundary source.
- `node --check web/app.js` passes.
- `python3 tests/test_bloques_removal.py` and the new test pass.

## Verification

```bash
node --check web/app.js
python3 tests/test_bloques_removal.py
python3 tests/test_geometrias_circuitos.py
python3 - <<'PY'
import json
d = json.load(open("web/data/circuitos_geometrias.geojson"))
codes = {c for f in d["features"] for c in f["properties"]["codigos"]}
print(len(d["features"]), "features,", len(codes), "codes")
PY
```

## Progress

- Created: planning only. No source writes yet.
- Route per task: T1-T3 delegated writer (data pipeline), T4 delegated writer
  (single non-trivial JS file plus generated data), T5 same writer.

- Implementado T1-T3: `scripts/build_circuitos_geometrias.py` descarga y
  normaliza la capa Info UNE (219 polígonos conservados) y añade 4 formas
  provisionales desde calles geocodificadas. Salida:
  `web/data/circuitos_geometrias.geojson` con 223 features, 214 códigos
  únicos, 4 provisionales y 158,299 bytes. Re-ejecutable e idempotente en
  sus features.
- Implementado T4: `web/app.js` indexa `porCodigo`/`compartidas`, pinta los
  polígonos con el color del estado (punteados y más transparentes si son
  provisionales), abre popup/tooltip con la atribución y conserva líneas y
  círculos como fallback. Las features compartidas se pintan una sola vez con
  el peor estado de sus códigos conocidos.
- Implementado T5: `tests/test_geometrias_circuitos.py` (7 pruebas) en verde.
- Verificación ejecutada: `node --check web/app.js`, `test_bloques_removal`,
  `test_estado_desconocido`, `test_reporte_bidireccional`, `test_poblacion` y
  `test_geometrias_circuitos`: todos OK. Cobertura del catálogo: 202 de 270
  códigos con polígono; 45 siguen con círculo y 23 no tienen ubicación.

## Delivery forecast

~350 authored lines (script ~120, app.js ~90, test ~60, data file generated
and excluded). Under the 400-line heuristic; single PR.

## Visual pass (post-preview)

Tras comparar con Info UNE en el navegador:

- Basemap fijado a flavor `light`: el tema oscuro dejaba el terreno casi negro
  y apagaba los colores de estado. Chrome oscuro + mapa claro es la referencia.
- `Circuitos DAF` y `Posibles zonas sin apagón` quedan en el control pero
  arrancan apagados: el tinte azul del segundo se confundía con el estado
  "asumido" y ensuciaba todos los polígonos.
- Polígonos: borde del color brillante (`col.l`) en vez del oscuro (`col.b`) y
  relleno 0.42 (provisional 0.22).
- Verificado con datos de producción descargados en `/tmp` (el snapshot local
  `web/data/estado.json` es de julio y no muestra el estado real).

## Second visual pass (feedback del owner)

- Fuera el relleno por estado: teñía barrios enteros de rojo/rosa y ocultaba
  los límites internos. Los circuitos ahora se dibujan **solo con contorno**
  (weight 2, color de estado; provisional punteado).
- El basemap vuelve a seguir el tema del sitio (`temaActual()`); no se fuerza
  el flavor claro de Info UNE.
- Círculos reducidos:
  - comentario "vecino sin luz": de área de 350 m a punto de 5 px;
  - emergencia sin barrio OSM: de 400 m a 220 m con relleno 0.12;
  - circuito sin polígono: de 450 m a 200 m, punteado y relleno 0.10.
- Capas DAF y "posibles zonas sin apagón" siguen apagadas por defecto.

## Third visual pass (relleno + separación)

- Relleno tenue por estado (0.18 en tema claro, 0.24 en oscuro) y **contorno
  neutro** (oscuro sobre mapa claro, claro sobre mapa oscuro) para que dos
  circuitos vecinos del mismo estado no se fundan.
- El contorno de estado ya no se usa: el estado va en el relleno y el límite
  en el contorno neutro.
- Al cambiar de tema se repintan las capas dinámicas para recalcular el
  contorno (el basemap ya se recreaba).

## Auditoría de circuitos (P15, GC12 y typos sistemáticos)

- **P15**: es un typo de **PZ15**. La UNE publicó "P15" una vez con la
  descripción exacta de PZ15 (Nuevo Vedado: Tulipán, Lombillo, Marino). Se
  agregó el alias `P15 -> PZ15`; el círculo del Malecón desaparece.
- **"Dos GC12"**: el catálogo tenía **GC12** (Cojímar, sin servicio) y
  **CG12** (letras transpuestas, una sola mención, estado "con servicio" del
  1-sep). Alias `CG12 -> GC12`.
- Se agregaron 26 alias de códigos one-off/typo con descripción idéntica a un
  canónico: R543, 1305, A1318, MB2044, CCP8/9/10/17, D1065, D1272, D1465,
  A1290, A1143, 3550, 1690, 1300, 2026, P321, OPZ321, OP32, SR20, R820, R830,
  C3.
- `scripts/alinear_circuitos_geometria.py` mueve `lat/lon` al polígono y
  corrige municipios; `scripts/build_circuitos.py` lo llama en cada corrida.
- `scripts/auditar_circuitos.py` deja la auditoría reproducible.
- Resultado sobre el catálogo de producción: 303 circuitos (26 alias menos),
  0 centroides a >400 m de su polígono, 0 municipios incoherentes. Quedan 99
  sin polígono (45 sin ubicación).
