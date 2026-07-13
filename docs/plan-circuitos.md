# Plan: catálogo de circuitos

Estado: **fase 1 hecha (acumulando datos)**. Retomar tras unos días de recolección.
Fecha de arranque: 2026-07-06.

## Idea

La Empresa pone en sus partes un **código de circuito** delante de las calles:

```
👉 A1443- Rio Verde, Calixto Sánchez
👉 PG940- Comodoro, El Volpe, Rosario, Güinera…
👉 D1125- Reparto Guiteras-Párraga
```

El código = **prefijo de subestación** (A, D, PG, PZ, S, T, R, AL, GC, C, H, MB, SG,
SF, CPP, OP, L, P…) + **número de circuito**. Es un **identificador estable** de un
tramo físico de red que alimenta un conjunto fijo de calles. Sirve como *llave
canónica* para dejar de casar direcciones en texto libre (que es donde perdemos
precisión). Encaja con la filosofía del proyecto: construir nuestra propia fuente
de verdad, sembrada de los partes.

## Qué ya está hecho (fase 1)

- **`scripts/build_circuitos.py`**: reconstruye el catálogo desde TODO el histórico
  del canal en Supabase (no acumula fichero propio; la BD ya es la fuente). Por cada
  código guarda: `codigo`, `prefijo`, `calles` (la lista más completa vista),
  `municipio`, `bloque` (inferido si el post es de un solo bloque), `causa`, `veces`,
  `primera`, `ultima`, `estado` (con/sin servicio) y `estado_fecha`.
- Salida: **`web/data/circuitos.json`**. Enganchado al cron en `ingest.yml`.
- **Pestaña `web/circuitos.html` + `circuitos.js`**: lista filtrable por código, calle
  o municipio. Enlazada desde todas las páginas.
- Estado inicial: ~64 circuitos, ~43 con bloque identificado.

## Decisión actual

**Acumular unos días** antes de invertir más. Hoy casi todos los códigos aparecen 1
sola vez (43 menciones en 2363 posts); hay que confirmar que reaparecen de forma
consistente y ver cuántos códigos distintos hay en total.

## Próximos pasos (fase 2, al retomar)

1. **Geocodificar las calles** de cada circuito (reusar `geocode_zonas.py` /
   Nominatim + caché) → ubicación aproximada por circuito, más fina que "bloque
   entero". Pintarlos en el mapa (capa nueva, opcional togglear).
2. **Usar el código como llave** para deduplicar y seguir averías, parciales y
   restablecimientos (hoy se casan por dirección/tipo). "restablecido A1443" =
   flip exacto de ese circuito.
3. **Historial por circuito**: enlazar cada código con los partes donde aparece
   (ver su línea de tiempo de cortes/restablecimientos).
4. **Inferir circuito→bloque por co-ocurrencia** cuando el post lista varios bloques
   (hoy solo atribuimos si el post es de UN bloque): parsear por secciones de bloque.

## Preguntas abiertas / cosas a vigilar

- **Códigos de número puro** (ej. "2073 - Bello 26"): hoy se OMITEN para no
  confundir con números de dirección. Si la Empresa los usa consistentemente,
  relajar `RE_UN_CODIGO` para admitirlos con contexto.
- **Decodificar el prefijo → subestación**: ¿A/D/PG… corresponden a subestaciones
  concretas? Útil para agrupar y para el mapa. Verificar con los datos que se
  acumulen.
- **Recurrencia**: confirmar que un mismo código reaparece con descripción estable
  (solo `PG940` se vio 2× hasta ahora, con misma descripción — buena señal).
- **Multi-bloque en un post**: mejorar la atribución de bloque parseando secciones.

## Archivos relevantes

- `scripts/build_circuitos.py` — generador del catálogo.
- `web/circuitos.html`, `web/circuitos.js`, estilos en `web/style.css` (`.circ*`).
- `web/data/circuitos.json` — salida (semilla en git; el cron la regenera).
- `.github/workflows/ingest.yml` — paso `build_circuitos.py`.
