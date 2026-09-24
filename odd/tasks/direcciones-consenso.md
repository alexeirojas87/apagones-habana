# direcciones-consenso — dirección de circuito por consenso del canal

## Objetivo

Que la dirección (`calles`) de cada circuito en `web/data/circuitos.json` deje de
depender de la ÚLTIMA mención de la UNE y pase a decidirse por **consenso
corroborado** sobre el historial del canal, de modo que un error de la fuente
(una mención suelta con el código o la zona cruzada) no pueda volver permanente
la dirección equivocada — y que un cambio real y sostenido sí pueda ganar.

## Problema

`_adoptar_calles()` (build_circuitos.py) es "gana la última": si el texto nuevo
solapa < 0.25 con el conocido, lo reemplaza. Con eso:

- **AL56** quedó con la dirección oficial de AL53 (`Zonas: 1, 2, 3, 5, 24, 8, 7`)
  después de que la UNE le publicara esas zonas en un parte DAF. Al geocodificar
  la misma zona, AL56 y AL53 caen en el mismo punto y en el mapa se apilan (uno
  tapa al otro) — síntoma visible reportado por el mantenedor.
- El cruce de direcciones es **sistemático**, no un caso aislado: con la tabla
  oficial como árbitro se detectaron **41 pares circuito←otro / 110 menciones**
  en el caché local (p. ej. `D1125`←`PG930` 35×, `A495`←`S514` 26×, `A1695`←`PG920`).
- El parseo también aporta: cuando un parte lista varios códigos con un mismo
  bloque de texto (`👉AL53, AL56: Zonas: ...`), ese texto se reparte idéntico a
  todos los códigos del grupo.

## Por qué (medición que habilita la fórmula)

Sobre el caché local (2.5 meses, 27.572 menciones, 206 circuitos con ≥30):

- **154 circuitos con racimo dominante ≥90%** → la dirección es clara.
- **Solo 3 cambios reales y sostenidos** (cada mitad del período con su propio
  racimo dominante): `GC15`, `OP329`, `A500`.
- **16 circuitos con racimo dominante <70%**, y al mirarlos casi todos se
  explican por causas ya conocidas: textos que son listas de otros códigos,
  variantes de escritura del mismo lugar (`Chibás`/`Chivás`), cruces reales de la
  UNE, y los 3 cambios reales.

Conclusión: el consenso decide en la mayoría abrumadora de los casos; los pocos
irreducibles van a revisión manual.

Nota: el PDF de la UNE **no es verdad absoluta** (los circuitos cambiaron desde
su publicación). La tabla oficial queda como arranque en frío y como árbitro del
detector de cruce, nunca como pisa-decisiones.

## Alcance

- `scripts/build_circuitos.py`
  - `replay_canal(...)`: recolectar votos (fecha, texto, ¿mención compartida?) sin
    cambiar la firma posicional ni el retorno de 2 elementos.
  - Nuevas funciones puras: peso por recencia, agrupado de variantes, resolución
    de la dirección y detector de cruce.
  - `main()`: invocar el resolver después de la fusión oficial y la semilla
    aprendida, y ANTES de la detección de cambios y de la geocodificación.
- `tests/test_direcciones_consenso.py` (nuevo).

## Fuera de alcance

- `.github/workflows/*` — el mantenedor pidió explícitamente no tocar la
  ingesta/CI en este cambio.
- `web/*.html` y la UI de "en revisión" (el flag queda en los datos; mostrarlo en
  el sitio es seguimiento, no este cambio).
- Reintentar el arreglo de `CG12→GC12` (alias) o de los geocodes apilados: es
  otro cambio; este solo elimina la causa de la dirección cruzada.

## Restricciones

- No romper `_adoptar_calles` ni sus tests: sigue siendo el respaldo para códigos
  sin votos (y la defensa contra texto degenerado del LLM).
- `replay_canal` mantiene su retorno `(cat, eventos_horas)`; el nuevo parámetro de
  votos es opcional con default `None` (lo llama `tests/test_horas_circuitos.py:282`
  con 4 argumentos posicionales).
- Sin red: correr y verificar solo con el caché commiteado.

## Parámetros de la fórmula (v1)

- Vida media de recencia: **45 días** (peso = 0.5 ** (días / 45)).
- Variantes: mismo racimo si `_cobertura(a, b) >= 0.6`.
- Misma dirección que la vigente si `_cobertura(top, vigente) >= 0.35`.
- Cambio real: `share(top) >= 0.60` **y** `n(top) >= 3`.
- Cruce: el texto ganador solapa `>= 0.7` con la dirección de OTRO circuito
  (vigente publicada o tabla oficial) y `< 0.35` con la del propio.
- Mención compartida (≥2 códigos con un mismo bloque de texto): no vota, salvo que
  el circuito no tenga ningún otro voto.

## Checklist

- [x] **T1** `replay_canal` recolecta votos; marca `compartida` cuando el bloque de
      texto se reparte entre ≥2 códigos (vía regex y vía LLM).
- [x] **T2** Agrupado de variantes + peso por recencia (funciones puras).
- [x] **T3** `resolver_direcciones`: racimo dominante + histéresis (la vigente no
      cae por una mención suelta) + cambio sostenido permitido.
- [x] **T4** Detector de cruce: si el ganador es la dirección de otro circuito, no
      se adopta; se elige el mejor racimo propio no cruzado y se marca.
- [x] **T5** Hook en `main()` + flags en el catálogo
      (`direccion_en_revision`, `direccion_confianza`) + lista de revisión impresa
      en la salida del build.
- [x] **T6** `tests/test_direcciones_consenso.py`: mayoría le gana a la mención
      suelta; la recencia deja pasar un cambio sostenido; la mención compartida no
      vota; el cruce se detecta; empate → se conserva la vigente y se marca.
- [ ] **T7** Suite completa en verde + commit de unidad de trabajo.

## Criterios de aceptación

1. Un caso tipo AL56 (1–2 menciones cruzadas contra ~160 correctas) conserva la
   dirección correcta y queda marcado si corresponde.
2. Un caso tipo `OP329`/`A500` (cambio real, decenas de menciones, sostenido) sí
   actualiza la dirección.
3. Ningún circuito queda con la dirección de otro si el detector la identifica.
4. Los tests existentes siguen pasando (34 archivos de `tests/`).

## Verificación

- Runner: `python -m unittest discover -s tests`
- Dirigido: `python -m unittest tests.test_direcciones_consenso tests.test_cambios_direccion tests.test_extraccion_circuitos tests.test_horas_circuitos`
- Modo TDD: **no habilitado explícitamente en el proyecto** (no hay configuración de
  TDD ni `sdd-init` disponible). Modo estándar: tests de regresión junto al código,
  que es la convención del repo (el CI corre `unittest discover` antes de ingerir).

## Progreso / evidencia

Implementado T1–T6. Sin pipeline (sin red): solo `unittest` + una corrida offline
de lectura sobre `data/canal_cache.json` (no escribe archivos).

Comandos y resultados textuales:

```
$ python3 -m unittest tests.test_direcciones_consenso -v
...
Ran 18 tests in 0.011s

OK
```

```
$ python3 -m unittest discover -s tests
...
Ran 569 tests in 2.382s

OK
```

Sanity offline sobre el caché real (replay + resolver, sin escribir salidas):
`314` circuitos, `299` con votos, `37` en revisión (`33 cruzada`, `4 disputada`).
El caso **AL56** conserva la dirección correcta (`Zonas: 13; 15; 16; 17; 18; 21 y
Micro X`, no las zonas de AL53) y no queda marcado; **D1125** no adopta el texto
de PG930.

Desviación del diseño (documentada en el código): el paso 7 literal decía "si no
hay candidato, no toques `calles`". En datos reales eso dejaba publicado el texto
cruzado que el replay había adoptado como última mención (p. ej. OP318 quedaba
con la dirección de R466 aunque marcado `cruzada`), lo que viola el criterio de
aceptación 3 y el T4 ("no se adopta"). Se restauró la lectura de T3/T5: sin
candidato propio y con dirección vigente, `calles` vuelve a la vigente. Los casos
sin vigente (arranque en frío) no tienen nada que restaurar y quedan solo
marcados.

## Próximo paso

Revisar la desviación del detector de cruce y committear (T7): el commit lo hace
el mantenedor.
