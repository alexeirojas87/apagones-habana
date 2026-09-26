# presupuesto-storage-supabase — DDL del lado base (T5)

> **APLICADO el 2026-09-26.** De **425 MB a 276 MB** (−149 MB; de 85% de la cuota
> a 55%). Las tres sentencias de abajo quedan como registro de lo que se ejecutó,
> no como algo pendiente. El detalle y la verificación funcional del RAG están en
> `odd/tasks/presupuesto-storage-supabase.md`.

Cómo se ejecutó, en vez del SQL Editor: con `psycopg2` y `autocommit` contra el
host directo `db.bmtvaebcnwzjjlempzgb.supabase.co:5432`. Ese host es el correcto
para esto —es sesión real—; el *transaction pooler* (`:6543`) no sirve, porque
`VACUUM` no puede correr dentro de una transacción. El host directo resuelve
**solo IPv6** (verificado: conecta desde esta máquina).

**Una sentencia por vez**, y el paso 3 toma un lock exclusivo que bloquea las
consultas del chatbot unos segundos: conviene correrlo de noche.

Estado de partida medido el 2026-09-26: 425 MB de 500, y el índice ivfflat en
132 MB (el doble de lo que le correspondía para 16.811 vectores).

---

## 1. El índice ivfflat se va

```sql
drop index if exists chatbot_fragmentos_embedding;
```

Pesa **132 MB** para buscar entre 16.742 vectores que ocupan 65 MB. Un ivfflat
sano sobre esas filas pesaría ~70 MB: el resto son entradas muertas, porque
`purga.py` borra fragmentos cada 30 minutos e **ivfflat no compacta sus páginas
en el VACUUM** (el heap sí: `n_dead_tup` era 3). Es una fuga que se repite en
cada ciclo, y por eso la base no logra quedarse debajo de la cuota.

A esta escala el índice es costo puro:

- Un seq scan exacto sobre 65 MB tarda decenas de milisegundos y el bot es
  low-QPS.
- La búsqueda queda **exacta** en vez de aproximada. Con `probes = 1` (el
  default) ivfflat solo mira una fracción de las listas: el índice no solo era
  caro, daba peor recall que no tenerlo.

`buscar_fragmentos` sigue funcionando sin cambios: Postgres hace el scan y
ordena. Perdés velocidad, no correctitud.

**Este paso libera ~132 MB al instante y no tiene pico de disco** (al contrario
que `REINDEX`, que necesita el índice nuevo completo antes de soltar el viejo:
con 68 MB de aire no entraba).

Si el índice vuelve a hacer falta —arriba de ~50.000 fragmentos— recrearlo
**después** de podar y con el `lists` correcto: la doc de pgvector usa
`rows/1000` hasta 1M de filas (`sqrt(rows)` es para más de 1M, y el
`schema_chatbot.sql` usaba `sqrt` con 45 lists para ~2.000 filas).

## 2. Fuera del índice los comentarios que no son reportes

```sql
delete from chatbot_fragmentos
where left(id, 4) = 'com_'
  and coalesce(metadatos ->> 'reporta', '') not in ('sin_corriente', 'con_corriente');
```

2.583 filas (`reporta` = `pregunta` / `queja` / `irrelevante`), el 15% del
índice. Poco en MB (~22) pero cada una competía por un hueco del top-6 en cada
consulta del RAG: son el "estamos ingiriendo partes que no son partes".

El filtro por `left(id, 4) = 'com_'` es lo que separa un fragmento de comentario
de uno de parte oficial. Filtrar por `tipo = 'reporte'` a secas **borraría partes
oficiales**, porque `fragmentos_partes()` toma el tipo del LLM y ese tipo puede
ser `reporte`.

Desde el cambio de código (`scripts/chatbot/embeddings.py`) no vuelven a entrar:
el índice solo acepta `sin_corriente` y `con_corriente`.

## 3. Compactar

```sql
vacuum (full, analyze) chatbot_fragmentos;
```

Va **después** del delete (el archivo nuevo solo contiene lo que sobrevive) y
**después** del drop (que es el que libera el aire que este paso necesita).

`VACUUM FULL` reescribe la tabla en un archivo nuevo: el pico de disco es
archivo viejo + archivo nuevo, y toma un lock exclusivo. Sin el paso 1, con 68 MB
de aire y una tabla de ~90 MB, era riesgoso. Con el paso 1 hecho, sobra lugar.

## 4. Comprobar

```sql
select count(*)                                                      as fragmentos,
       pg_size_pretty(sum(pg_column_size(embedding)))                as vectores,
       pg_size_pretty(pg_total_relation_size('chatbot_fragmentos'))  as tabla_total,
       pg_size_pretty(pg_database_size(current_database()))          as base;
```

Esperado: ~14.200 fragmentos, ~55 MB de vectores, la tabla alrededor de 70 MB
(venía de 225) y la base por debajo de **300 MB**, con los 30 días de RAG y los
60 días de mensajes intactos.

---

## Lo que este documento NO hace, y por qué

- **No toca la retención de `mensajes`** (60 d comentarios / 365 d canal). El
  requisito es "30 días consultables" y ya se cumple; bajarla destruiría un mes
  de mensajes que nadie pidió destruir para ahorrar ~35 MB cuando el margen ya
  alcanza. Es un lever disponible si el crecimiento lo exige.
- **No migra a `halfvec(1024)`.** Ahorraría ~29 MB a cambio de un rewrite de
  tabla con lock exclusivo. No vale el riesgo por 29 MB sobre un presupuesto de
  500. (Dato para el día que haga falta: `halfvec` guarda `2*dim+8` bytes en vez
  de `4*dim+8`, o sea exactamente la mitad, e ivfflat lo soporta con
  `halfvec_cosine_ops`.)
- **No agrega un monitor de tamaño por RPC.** El crecimiento descontrolado tenía
  una causa —el auto-apagado silencioso de la purga— y esa ya se arregló en el
  código (`scripts/purga.py` falla en vez de omitirse, y el workflow lo deja
  ver). Vigilar el síntoma con un objeto de base nuevo y un paso de CI sería
  agrandar el sistema sin necesidad.
