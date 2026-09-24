# reloj-silencio-contrario — el reloj del estado solo lo resetea un parte contrario

## Objetivo

Que el reloj de silencio que decide `desconocido` (>48 h) y `azul/asum` (>216 h)
lo resetee **solo un parte CONTRARIO al estado actual** (de la UNE o vecinal). Un
parte que **coincide** con el estado vigente (otro "sin" estando ya en apagón) NO
lo resetea: el apagón sigue contando desde el último cambio de estado.

## Problema

Hoy `ultimaNoticia(c)` (duplicada en `web/app.js`, `web/circuitos.js` y
`web/_worker.js`, y en línea en `scripts/build_seo.py`) toma el máximo de **todas**
las noticias: la última mención del catálogo (`ultima`) y `desde`/`ultima_sin`/
`ultimo_con`/`ultimo_reset` del conteo vecinal. Cualquier re-mención "sin" —aunque
el circuito ya estuviera declarado "sin servicio"— reinicia el reloj, y entonces:

- **1861**: último parte que lo CAMBIÓ = 11/9 ("sin"); después solo partes que
  coinciden (vecinales del 19/9 y 24/9). El reloj se mantiene en 5,9 h → sigue
  rojo, y la tarjeta lo publica con **315 h** (cronómetro desde `estado_fecha`).
  Con la regla del mantenedor debería llevar 13 días de silencio → **azul**.
- Lo mismo con **A1538** (10 días), **SF584** (9) y **A495** (6).
- La serie acumulada (`circuitos_horas.json`) hereda el mismo vicio: el tope de
  216 h se ancla en `re_menciones` y en las señales vecinales "sin", así que 1861
  publica 315 h en vez de cortar a las 216 h.

## Regla (la del mantenedor, textual)

> "el vecinal resetea el silencio, o cualquier parte en sí, también el de la UNE,
> resetea el silencio, solo si es un parte contrario al estado actual. Si el
> circuito está en apagón y hay un parte de la UNE/vecino que dice que no tiene
> corriente, eso sigue contando; solo se resetea si es un parte de estado
> contrario."

Contrario = el que cambiaría el estado declarado:

- estado `sin servicio` → contrario = restablecimiento (UNE `con servicio`) o
  señal vecinal `ultimo_con` (el builder la marca `reportado_con`);
- estado `con servicio` → contrario = afectación (UNE `sin servicio`) o reporte
  vecinal "sin" (el builder lo marca `discrepado` con `desde`).

## Alcance

- `scripts/build_circuitos.py`
  - nuevo campo `estado_desde` = fecha del parte que **cambió** el estado (regex y
    LLM). `estado_fecha` NO cambia de significado (lo usan build_seo,
    build_serie24h, coherencia_catalogo, bot y worker como "última mención").
  - `horas_historicas`: el tope de 216 h del tramo abierto se ancla en la apertura
    (el cambio de estado), no en las re-menciones ni en las señales vecinales "sin".
- `web/app.js`, `web/circuitos.js`, `web/_worker.js`: el reloj pasa a ser el del
  cambio de estado (+ la señal vecinal contraria que marcan `discrepado` /
  `reportado_con`).
- `web/app.js` (tarjeta "con más horas sin corriente"): usa ESE reloj en vez del
  cronómetro desde `estado_fecha`.
- `scripts/build_seo.py`: el máximo en línea de "última noticia" usa el mismo reloj
  (si no, las páginas SEO contradicen al mapa).

## Fuera de alcance

- El significado de `estado_fecha` y de `ultima` (los consumen otros scripts).
- La rama `discrepado` / `reportado_con` (prioridad de estado) y el evento nacional.
- `.github/workflows/*`.

## Checklist

- [x] **T1** `estado_desde` en el replay (regex y LLM) + propagado en los
      `setdefault` del catálogo.
- [x] **T2** Reloj nuevo en los tres clientes (`ultimoCambio`), con guardas
      `discrepado`/`reportado_con`, y `silencioHoras` usándolo.
- [x] **T3** Tarjeta de horas usando el reloj del estado (el fallback a
      `estado_fecha` vive dentro de `ultimoCambio`, así los catálogos viejos
      siguen teniendo reloj; ya no se usa el cronómetro `Date.now()`).
- [x] **T4** `horas_historicas`: tope anclado en la apertura del tramo.
- [x] **T5** `build_seo.py` con el mismo reloj.
- [x] **T6** Tests nuevos del caso 1861 (partes que coinciden no resetean ni
      extienden) + ajuste de los que fijan la regla vieja.
- [ ] **T7** Suite completa en verde + commit de unidad de trabajo.
      (Suite en verde: 576 tests OK. El commit lo hace el mantenedor.)

## Criterios de aceptación

1. Un circuito cuyo último cambio de estado fue hace >216 h sale de rojo (azul/
   desconocido) **aunque** tenga partes coincidentes o señales vecinales "sin"
   recientes.
2. Un circuito con un parte contrario (restablecimiento o reporte vecinal de
   retorno) resetea el reloj y vuelve a su estado con el contador en cero.
3. La tarjeta nunca publica más de lo que su propio escalón sostiene.
4. La serie acumulada de un tramo abierto no pasa de la apertura + 216 h.
5. Los tests existentes siguen pasando (salvo los que fijan la regla vieja, que se
   actualizan a propósito).

## Impacto medido (caché local al 18/9, sobre 313 circuitos)

- Antes: `sin` 123 · `desconocido` 41 · `asum` 67 · `con` 82.
- Después: `sin` 83 · `desconocido` 61 · `asum` 96 · `con` 73.
- Transiciones: `sin→desconocido` 32, `desconocido→asum` 20, `sin→asum` 8,
  `con→desconocido` 8, `con→asum` 1.

## Verificación

- `python3 -m unittest discover -s tests`
- Reproducción offline: 1861 con el parte del 11/9 + señales vecinales coincidentes
  → azul, y su serie abierta cortada a 216 h.
- TDD: no habilitado explícitamente en el proyecto (misma convención que el resto:
  tests de regresión con el código, que es lo que corre el CI antes de ingerir).

## Progreso / evidencia

Implementado el 2026-09-24 sobre la rama `fix/reloj-silencio-contrario`.

### Funciones nuevas / cambiadas

- `scripts/build_circuitos.py`
  - `replay_canal(filas, oficial, falsos, llm_cache, votos=None)`: agrega
    `estado_desde` a los registros (setdefault regex/déficit/LLM) y lo setea
    solo cuando `r["estado"] != <nuevo estado>` (regex y LLM; el guard temporal
    del LLM `(r["estado_fecha"] or "") <= fecha` se respeta).
  - `horas_historicas(eventos, generado, senales=None, senales_con=None)`: el
    tope del tramo abierto pasa a `azul_cap = abierto + 216 h`. `senales` queda
    en la firma sin uso para el tope.
- `scripts/build_seo.py`
  - `_ultimo_cambio(c)` (reemplaza a `_ultima_noticia`): `estado_desde` con
    respaldo `estado_fecha`, más las señales vecinales CONTRARIAS
    (`ultimo_con` con `reportado_con` / `desde` con `discrepado`).
  - `_silencio_horas(c, gen)`: usa `_ultimo_cambio` (misma firma).
- `web/app.js`, `web/circuitos.js`, `web/_worker.js`
  - `ultimoCambio(c)` (reemplaza a `ultimaNoticia`): idéntico en los tres.
  - `silencioHoras(c, generado)`: usa `ultimoCambio` (misma firma).
  - `web/app.js`: la tarjeta "Con más horas sin corriente" usa
    `silencioHoras(c, estado.generado)` como vía no oficial (las horas del parte
    de déficit siguen ganando); `title`/rótulo actualizados a "horas de silencio".

### Comandos y resultado textual

```
$ python3 -m unittest tests.test_estado_desconocido tests.test_horas_circuitos -v
...
Ran 108 tests in 0.232s

OK
```

```
$ python3 -m unittest discover -s tests
...
Ran 576 tests in 2.282s

OK
```

Reproducción offline del 1861 con `horas_historicas` (sin escribir archivos),
apertura real del caché `2026-09-11T15:44:57+00:00` + re-menciones/señales
vecinales "sin" coincidentes del 19/9 y 24/9, horizonte 315 h:

```
nuevo total 1861 = 216.0 h
viejo total 1861 = 315.0 h
nuevo por_dia: {"2026-09-11": 12.25, ... "2026-09-20": 11.75}
```

Circuitos del caché con `estado_desde` a más de 216 h del horizonte (18/9):
**85** (de 314 con estado; 297 traen `estado_desde`). Ejemplos: `T43` 1775 h,
`OP32` 1722 h, `3540` 1721 h, `A1538` 619.9 h (los casos del mantenedor:
1861 queda a 167 h del horizonte de este caché, por eso cae a desconocido y no
a azul todavía; con el horizonte del mantenedor —24/9— sería azul).

### Desviaciones / decisiones

- El `setdefault` del camino de **déficit** también lleva `estado_desde: None`,
  pero ese camino NO setea el valor (la instrucción acota el seteo a "los dos
  caminos: regex y LLM"). Consecuencia: 15 circuitos que solo aparecen en
  "Actualización de afectaciones" tienen `estado_desde` nulo y su reloj cae al
  respaldo `estado_fecha` (la última mención de déficit). Queda anotado por si
  el mantenedor quiere que el parte de déficit también mueva el reloj.
- No se modificó `tests/fixtures/mini_circuitos.json`: agregar `estado_desde`
  rompería `test_mini_circuitos_usa_campos_reales_del_catalogo` (las claves del
  fixture deben existir en `web/data/circuitos.json`, que no se toca). El
  respaldo `estado_fecha` cubre el fixture.
- La tarjeta de horas ya no conserva el cronómetro viejo `Date.now()`: el
  respaldo "catálogo viejo" es `estado_fecha` dentro de `ultimoCambio`.

