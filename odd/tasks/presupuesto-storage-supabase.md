# presupuesto-storage-supabase — bajar y sostener la base por debajo de 500 MB

## Objetivo

Que la base de Supabase (tier gratis, 500 MB) deje de estar al borde de la cuota
—~432 MB, 86%— y quede con margen estructural, **sin perder la capacidad de
consultar los últimos 30 días** ni la del RAG del chatbot, y sin indexar
comentarios que no aportan valor de reporte.

## Problema

Medido en el proyecto `apagoneshabana` (`bmtvaebcnwzjjlempzgb`) el 2026-09-26:

| Objeto | Total | Detalle |
|---|---|---|
| `chatbot_fragmentos` | 225 MB | de eso, el índice ivfflat pesa **132 MB** |
| `mensajes` | 140 MB | pkey 20 MB + fecha 13 MB |
| `eventos` | 21,5 MB | 81.886 filas |
| `mensajes_pkey` | 19,6 MB | |

El porcentaje que muestra el panel suma 123% porque cada tabla ya incluye sus
propios índices: el total real de la base es ~432 MB.

Dos causas, ninguna de las cuales es "basura muerta en las tablas":

1. **El índice ivfflat está ~2× inflado.** `chatbot_fragmentos` tiene 16.742
   filas y 65 MB de vectores (16.742 × 4.104 B = 68,7 MB, así que el dato
   cuadra). El heap está impecable: `n_dead_tup = 3`. Pero `pg_relation_size`
   del índice da 132 MB cuando un ivfflat sobre esos 16.742 vectores debería
   pesar ~70 MB. Es decir, **~62 MB son entradas muertas**: `purga.py` borra
   fragmentos de más de 30 días en cada corrida (cada 30 min) y **ivfflat no
   compacta sus páginas**. VACUUM limpia el heap; el índice se queda con los
   cadáveres. Es una fuga que se repite en cada ciclo: por eso la base no puede
   "quedarse" debajo de la cuota.
2. **El índice vectorial es caro por diseño a esta escala.** Cada fragmento
   cuesta 4,1 KB de vector + 4,1 KB de índice, contra 150 bytes de texto:
   se está pagando un índice *aproximado* de 132 MB para buscar entre 16.742
   vectores que un seq scan exacto resuelve en decenas de milisegundos — y con
   mejor recall que `probes=1`. El bot es low-QPS.

Y una causa de segunda: el auto-apagado silencioso de la purga (ver T2).

Composición del índice por tipo (medida):

| tipo | reporta | filas |
|---|---|---|
| reporte | sin_corriente | 7.661 |
| afectacion | — | 2.146 |
| restablecimiento | — | 2.084 |
| reporte | con_corriente | 1.527 |
| reporte | **pregunta** | 1.226 |
| reporte | **queja** | 1.107 |
| averia | — | 392 |
| reporte | **irrelevante** | 250 |
| deficit | — | 222 |
| daf | — | 142 |
| otro | — | 20 |

Los fragmentos de comentario son 11.771 (70%); los partes oficiales, 5.006. El
ruido (pregunta/queja/irrelevante) son **2.583 filas, el 15% del índice**: poco
en MB (~22), pero cada una compite por un hueco del top-6 en cada consulta del
RAG. Ese es el "estamos ingiriendo partes que no son partes" reportado.

## Por qué (medición que habilita el plan)

- 16.742 × 4.104 B = 68,7 MB ≈ los 65 MB de vectores reportados por
  `sum(pg_column_size(embedding))`. El modelo de costo por fila cierra:
  heap ~0,3 KB + vector ~4,2 KB + índice ~4,15 KB ≈ 8,6 KB/fila (13 KB con el
  índice inflado), y 16.742 × 13 KB ≈ 218 MB, que es el total observado.
- `n_dead_tup = 3` en `chatbot_fragmentos` y `last_autovacuum = 2026-09-22`
  descartan bloat de tabla. Todo el desperdicio está en el índice.
- Con el índice afuera y el ruido borrado, la tabla queda en ~14.200 filas y
  ~70 MB (de 225), y el crecimiento vuelve a estar acotado por retención.

## Alcance

- `scripts/chatbot/embeddings.py` — no indexar `reporta` sin valor de reporte;
  selección determinística.
- `scripts/purga.py` — el auto-apagado deja de ser silencioso.
- `.github/workflows/ingest.yml` — dejar de tragarse el fallo de la purga.
- `tests/test_embeddings_filtro.py` (nuevo) y `tests/test_purga.py`.
- `docs/presupuesto-storage-supabase.md` (nuevo) — el DDL del lado base.

## Fuera de alcance (deliberado)

- **Retención de `mensajes`** (60 d comentarios / 365 d canal): el requisito es
  "30 días consultables" y ya se cumple. Bajarla a 30 destruiría un mes de
  mensajes que nadie pidió destruir, para ahorrar ~35 MB cuando el margen ya
  alcanza. Queda como lever explícito si el crecimiento lo exige.
- **`halfvec(1024)`**: ahorra ~29 MB y a cambio exige un rewrite de tabla con
  lock exclusivo. No vale el riesgo por 29 MB sobre un presupuesto de 500.
- **Bajar `HORAS_COMENTARIOS`**: con el `.order` (T1) y el tope de 500 la
  cobertura se vuelve determinística; el efecto en tamaño se mide después, no se
  supone.
- **`scripts/comentarios_llm.py` (regex `RUIDO`)**: sus filas alimentan
  `estado.py` (reloj de silencio, líneas 785/790/1058) y `build_analitica.py`.
  Filtrar ahí cambia **producto**, no solo el índice, y no es lo que se pidió.
  El filtro va en el índice (T1), que es lo único que lee el RAG.
- **Monitor de tamaño por RPC + paso de CI**: el auto-apagado ruidoso (T2/T3)
  ataca la *causa* del crecimiento descontrolado. Agregar un objeto de base y un
  paso de CI para vigilar el *síntoma* sería agrandar el sistema sin necesidad.
- `web/*.html`: modificados en el working tree por el CI; no se tocan.

## Restricciones

- **`PURGA_FRAGMENTOS_DIAS >= DIAS_HISTORICO_BOT`, siempre.** Hoy los dos valen
  30 y por eso están alineados. Si la purga fuera más corta que la ventana de
  embeddings, `embeddings.py` re-insertaría en cada corrida lo que la purga
  acaba de borrar: churn infinito, costo de API y la base que no baja nunca. Lo
  fija un test (T4).
- No cambiar la dimensión 1024 ni la firma de `buscar_fragmentos`/`vector(1024)`:
  los vectores almacenados dejarían de ser comparables y habría que re-embeber
  16.742 fragmentos.
- Los tests corren sin red (convención del repo).
- `embeddings.py` sigue siendo incremental por sha1.

## Checklist

- [x] **T1** `embeddings.py`: `fragmentos_comentarios()` deja de indexar
      `reporta` fuera de `{sin_corriente, con_corriente}` y ordena por
      `fecha desc` antes del `.limit(500)`. Docstring con el porqué y las cifras.
- [x] **T2** `purga.py`: `cache_frescos()` en falso termina con código ≠ 0 (hoy
      imprime y sigue, dejando la retención apagada sin que nadie se entere).
- [x] **T3** `ingest.yml`: la línea de purga deja de llevar `|| echo`.
- [x] **T4** Tests: filtro, determinismo del orden y el invariante
      `PURGA_FRAGMENTOS_DIAS >= DIAS_HISTORICO_BOT`; auto-apagado con exit ≠ 0.
- [ ] **T5** DDL del lado base (bloqueado: requiere credencial): drop del ivfflat
      + delete del ruido + `vacuum full`. El SQL está en
      `docs/presupuesto-storage-supabase.md`.

## Criterios de aceptación

1. Ningún fragmento con `id` `com_*` y `reporta` fuera de
   `{sin_corriente, con_corriente}` se indexa ni queda indexado.
2. Un mismo snapshot de `comentarios_llm` produce siempre el mismo conjunto de
   candidatos (hoy `.limit(500)` sin `.order` devuelve 500 arbitrarios).
3. Un caché ausente o viejo ya no deja la purga apagada en silencio: el paso del
   workflow falla de forma visible.
4. Los 576 tests existentes + los nuevos, en verde.
5. Con T5 aplicado: la base queda por debajo de 300 MB, con 30 días de RAG y 60
   días de mensajes intactos.

## Verificación

- Runner: `python3 -m unittest discover -s tests` — baseline **576 tests, OK**.
- Dirigido: `python3 -m unittest tests.test_embeddings_filtro tests.test_purga`
- Modo TDD: **no habilitado en el proyecto.** No hay runner ni configuración de
  TDD; la convención real del repo es tests de regresión junto al código. (Una
  memoria de `sdd-init` dice `strict_tdd: true`, pero no se corresponde con
  ningún artefacto del repo: manda la convención real.)

## Progreso / evidencia

T1–T4 implementados y verificados. T5 bloqueado por credencial (ver "Próximo
paso").

Comandos y resultados textuales:

```
$ python3 -m unittest tests.test_embeddings_filtro tests.test_purga
Ran 9 tests in 0.256s
OK
```

```
$ python3 -m unittest discover -s tests
Ran 582 tests in 2.273s
OK
```

Baseline de la rama antes de tocar nada: `Ran 576 tests in 2.404s` / `OK`.
Los 6 nuevos: 5 en `tests/test_embeddings_filtro.py` + 1 en `tests/test_purga.py`.
Fallo ambiental conocido (no introducido acá): matplotlib imprime deprecaciones
de `parseString`/`resetCache` en stderr.

`python3 -m py_compile scripts/chatbot/embeddings.py scripts/purga.py`: sin
salida. `.github/workflows/ingest.yml` parsea con `yaml.safe_load` → 24 steps.

### Desviación documentada: la purga va después del deploy

El plan original era solo sacarle el `|| echo` a la línea de la purga (T3). Al
revisar el workflow apareció que **`build_seo.py` y `wrangler pages deploy` no
tienen `if: always()`**. Con la purga fallando donde estaba (`:181`), un caché
viejo habría fallado el job y **saltado el SEO y el deploy**: el sitio se
congela en el acto, mientras que la base tarda semanas en llegar a la cuota. Es
el dominio de fallo cruzado y es peor que el problema que arregla.

En vez de agregarle `if: always()` al deploy (que lo haría desplegar también
cuando falla la ingesta, cambio de semántica mucho más amplio), **se movió el
step de la purga al final del job, después del deploy**. Resultado: un fallo de
la purga sigue dejando la corrida en rojo (visible, que es el punto) y ya no
bloquea nada, porque el deploy ocurrió antes. La purga no alimenta ningún step
posterior, así que el orden es libre.

Verificado que la purga quedó última:

```
$ python3 -c "import yaml; ... steps[-1] ..."
steps: 24
 - git config user.name "github-actions" ... | always= always()
 - python3 scripts/build_seo.py || echo "seo falló, se continúa" | always= None
 - npx -y wrangler@latest pages deploy web --project-name apagones-habana | always= None
 - python scripts/purga.py | always= None
```

### Commits de unidad de trabajo

(pendientes de registrar en este documento)

## Próximo paso

T5 requiere una credencial que el repo no tiene: el CLI de Supabase está logueado
(proyecto `apagoneshabana`, ref `bmtvaebcnwzjjlempzgb`) pero **el proyecto no
está linkeado, no hay `psql` instalado y `.env` solo tiene `SUPABASE_URL` +
`SUPABASE_SERVICE_KEY`** (REST, no sirve para DDL). Sin la cadena de conexión de
Postgres (o sin correr el SQL a mano en el editor de Supabase) no se puede hacer
el drop del índice, que es el 80% del ahorro.

