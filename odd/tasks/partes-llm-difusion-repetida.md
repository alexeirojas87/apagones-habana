# partes-llm-difusion-repetida — no pagarle el LLM dos veces al mismo texto

## Objetivo

Que una difusión del canal que se repite **no vuelva a costar una llamada al LLM
en cada emisión**. Hoy el pipeline le paga una extracción por hora al mismo aviso
idéntico, para siempre.

## Problema

Medido sobre `data/partes_llm.json` el 2026-09-26:

| | valor |
|---|---|
| llamadas al LLM (`via: llm`) | 13.307 |
| de esas, sin nada extraído — histórico | 372 (2,8%) |
| de esas, sin nada extraído — últimas 24 h | **24 de 281 (8,5%)** |
| de esas 24, en el minuto `:26/:27/:28` | **24 (todas)** |

Las 24 vacías de las últimas 24 horas caen **una por hora, todas las horas del
día**:

```
25 11:26 | 25 12:26 | 25 13:26 | 25 14:26 | 25 15:26 | 25 16:26 | 25 17:26 | 25 18:26
25 19:26 | 25 20:26 | 25 21:26 | 25 22:27 | 25 23:27 | 26 00:27 | 26 01:27 | 26 02:27
26 03:27 | 26 04:27 | 26 05:27 | 26 06:27 | 26 07:27 | 26 08:27 | 26 09:28 | 26 10:28
```

No son mensajes sueltos: es **una difusión programada, 24 veces por día**. La
última (`26 10:28` en hora local, UTC−4) es la que reportó el mantenedor, y
aparece en la caché como la entrada `87195` (`2026-09-26T14:28`, `tipo: otro`,
**cero datos**). El histórico era 2,8% porque la difusión arrancó hace poco — el
propio aviso dice "en fase de prueba", así que la tasa va a seguir subiendo.

**Causa.** El pre-filtro de `partes_llm.py:81-83` es:

```python
RELEVANTE = re.compile(r"circuito|bloque|afectaci|restablec|aver[ií]a|d[eé]ficit|desconexi|MW|disparo")
```

y el aviso dice *"Mantente informado sobre el estado de los **circuitos**"*. La
palabra "circuito" lo deja pasar al LLM. Una búsqueda de palabras sueltas no
distingue un parte de una difusión que **habla** de circuitos.

**Qué NO cuesta** (para no dramatizar el alcance): no ensucia el chatbot ni el
mapa. `embeddings.py` exige código, calle, municipio o déficit para indexar y
esto no trae nada de eso, así que no llega a `chatbot_fragmentos`. Lo que
consume es **presupuesto de extracción**: `MAX_LLM_PARTES=50` y
`MAX_SEGUNDOS_PARTES=420` por corrida, en un presupuesto que a veces se corta
solo por reloj y empuja partes legibles a la corrida siguiente.

## Por qué B (y qué se descartó)

La caché está indexada por **`message_id`**, y cada emisión de la difusión trae
un `message_id` nuevo: por eso el `validador_version` no la reconoce y se vuelve
a pagar. La caché no puede reconocer "el mismo texto" porque no guarda el texto.

- **Descartado: exigir código de circuito.** De los partes que sí traen datos,
  **692 no tienen código** (solo calles, municipio o bloques). Un filtro así
  cargaría partes legítimos — peor que desperdiciar una llamada.
- **Descartado: archivo de rechazos aparte.** Obligaría a tocar el `git add` de
  `ingest.yml:186`, que lista los cachés commiteados uno por uno. Se reusa
  `data/partes_llm.json`.
- **Elegido: hash del texto en la propia caché.** A un parte que no dejó nada se
  le guarda el sha1 de su texto. Antes de pagar el LLM, si el hash del texto ya
  está en esa lista, no se paga. Sólo se guarda en las entradas vacías (372 hoy),
  así que el crecimiento del archivo es de kilobytes, no de megabytes.
- **Y una red: marcas de difusión.** Un regex con `@…bot`, "suscríb", "/reporte",
  "nuestro bot", "fase de prueba". Ataja la **primera** emisión de un aviso
  nuevo, cuando el hash todavía no existe. El hash y las marcas son
  independientes a propósito: si mañana cambian la redacción, las marcas fallan
  pero el hash sigue funcionando a partir de la segunda emisión.

## Alcance

- `scripts/partes_llm.py` — cuatro funciones puras (`es_difusion`,
  `hash_texto`, `sin_datos`, `hashes_sin_datos`), el regex de difusión, y los dos
  cortes nuevos en el loop más el guardado del hash.
- `tests/test_partes_llm_validacion.py` — tests de las cuatro funciones y de las
  dos garantías que no se pueden romper (abajo).

## Fuera de alcance

- La capa de `eventos` (`extractor/extract.py`) y el RAG: ya están cubiertos.
- Borrar las ~660 entradas vacías históricas de la caché: **no** conviene, son el
  registro de "ya visto" y pesan poco.
- Deducir la difusión por repetición de `fecha` (cada hora en punto): sería
  frágil y no generaliza a otras difusiones.
- El `|| echo "LLM partes falló, se continúa"` de `ingest.yml:92`: es la misma
  familia de problema que la purga, pero es otro cambio.

## Restricciones

- **No se puede exigir código de circuito**: 692 partes con datos no lo traen.
- **Ningún consumidor puede romperse con el `via` nuevo.** Verificado: todos
  filtran con `via != "llm"` (`embeddings.py:99`, `build_circuitos.py:512`,
  `coherencia_catalogo.py:39`, `estado.py:220/533/607`,
  `comparar_extraccion.py:58`), así que `via: "repetido"` se ignora igual que
  `prefiltro`. Los tests que leen el archivo real
  (`test_ningun_item_de_partes_llm_es_degenerado`, `test_aprende_circuitos`)
  iteran `circuitos`, no la forma de la entrada.
- El campo `hash_texto` sólo se agrega a las entradas **vacías**, nunca a las que
  traen datos.
- Sin red en los tests (convención del repo).

## Checklist

- [x] **T1** `es_difusion`, `hash_texto`, `sin_datos` y `hashes_sin_datos` en
      `scripts/partes_llm.py`, más el regex `RE_AVISO_DIFUSION` con las cifras
      medidas en el comentario.
- [x] **T2** Los dos cortes en el loop: difusión/prefiltro antes del LLM, y hash
      ya visto antes del LLM. El hash se guarda tras validar si `sin_datos`.
      Contadores en el print para que se vea en el log de la corrida.
- [x] **T3** Tests: las cuatro funciones, las marcas contra el texto real del
      aviso, y las dos garantías.
- [x] **T4** Suite completa en verde + commit de unidad de trabajo.

## Criterios de aceptación

1. Un texto cuyo hash ya se registró como vacío **no** llega al LLM.
2. El aviso reportado se rechaza por marcas **en su primera emisión**, sin gastar
   LLM (y el hash queda como red para cuando cambien la redacción).
3. **Un parte real sin código —solo calles, municipio o bloques— sigue yendo al
   LLM.** Es la garantía que protege a los 692 partes legítimos.
4. Los 582 tests existentes siguen pasando.

## Verificación

- Runner: `python3 -m unittest discover -s tests` — baseline **582 tests, OK**.
- Dirigido: `python3 -m unittest tests.test_partes_llm_validacion`
- Modo TDD: no habilitado en el proyecto (convención real: tests de regresión
  junto al código).

## Progreso / evidencia

T1–T4 implementados y verificados.

```
$ python3 -m unittest tests.test_partes_llm_validacion
Ran 15 tests in 0.388s
OK
```

```
$ python3 -m unittest discover -s tests
Ran 587 tests in 2.325s
OK
```

Baseline antes del cambio: `Ran 582 tests in 2.404s` / `OK`. Los 5 nuevos viven en
`tests/test_partes_llm_validacion.py` (10 → 15). Los dos tests que leen el
`data/partes_llm.json` real siguen pasando.

### La garantía que importaba, medida sobre el corpus real

Sin red, sobre las 12.935 entradas `via: llm` de `data/partes_llm.json` **con
datos**:

- **0 falsos positivos** de `es_difusion` → ningún parte legítimo se pierde por
  las marcas. Era el criterio de aceptación 3 y el riesgo real de este cambio.
- **335** entradas reales de difusión sin datos quedan atrapadas por las marcas.
- El aviso reportado da `es_difusion` `True`.

### Commits de unidad de trabajo

- `70f82181` — `perf(partes-llm): no volver a pagar el LLM por la misma difusion`
  (`scripts/partes_llm.py`, `tests/test_partes_llm_validacion.py`).

## Próximo paso

Verificar **en producción**, en el log de la próxima ingesta, que `partes_llm`
reporta difusiones evitadas — el contador nuevo es
`N sin llamar al LLM (X difusiones, Y repetidos)`.

Ojo al leerlo: los contadores son **por corrida** y `MAX_LLM_PARTES` corta el
recorrido antes de terminar el backlog, así que el número depende de dónde se
cortó. Lo que importa es que aparezca > 0 y que `via: llm` deje de crecer al
ritmo de una por hora.
