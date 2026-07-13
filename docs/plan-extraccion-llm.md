# Plan: extracción flexible de partes y circuitos (LLM + validación)

**Problema**: la UNE no es consistente. Cada cambio de redacción (emojis 🚨→🛑,
déficit por bloques→por circuitos, "AL56 - Zonas:", celdas partidas en PDF)
rompe un regex y hay que parchear. Necesitamos entender el *contenido* de los
partes, no su forma.

**Principio**: el LLM interpreta, el catálogo valida, los regex quedan de red
de seguridad. Nunca peor que hoy: si Cloudflare AI falla, todo sigue como ahora.

## Tareas

### 1. Resolutor de identidad de circuitos (`scripts/circuitos_id.py`) — sin LLM
Módulo único que hoy está regado por 4 scripts:
- `normalizar_codigo("l317 / 1244")` → `["L317", "1244"]` (mayúsculas, espacios,
  dobles con `/`).
- `identificar(texto)` → código(s) al inicio del texto con las reglas seguras
  (letras+dígitos siempre; números puros solo 3-4 dígitos con separador).
- `casar_por_calles(texto)` → si NO hay código, matching difuso contra el
  catálogo oficial (solapamiento de tokens normalizados de calles/zonas);
  devuelve `(codigo, confianza)`; solo se acepta con confianza alta (≥0.6 y
  claramente mejor que el segundo candidato).
- `es_conocido(codigo)` → True si está en el catálogo (oficial o aprendido);
  los desconocidos se marcan `"por_confirmar"` en vez de entrar directo.

### 2. Extractor LLM de partes (`scripts/partes_llm.py`)
- Cloudflare Workers AI (mismo patrón/credenciales que `comentarios_llm.py`).
- Entrada: posts del canal aún no procesados. Salida por post (JSON estricto):
  `{tipo, circuitos: [{codigo, calles, municipio, estado, horas, causa}],
    bloques, mw_deficit, pct_restablecido}`.
- Caché **en archivo** `data/partes_llm.json` por `message_id` (mismo patrón
  que las geocachés, commiteado por el cron): cada post se procesa UNA vez.
- Todo código devuelto por el LLM pasa por el resolutor (tarea 1): si no es
  conocido ni casa por calles → `por_confirmar` (mitiga alucinaciones).

### 3. Comparador regex vs LLM
- En el pipeline, tras `estado.py`: comparar circuitos/tipos extraídos por
  ambos caminos para los posts de las últimas 24 h.
- Coinciden → confianza alta. Difieren → gana el LLM, pero la discrepancia se
  guarda en `data/discrepancias_extraccion.json`.
- `verificar_datos.py` (verificación diaria) reporta las discrepancias en el
  issue: así vemos QUÉ formatos nuevos aparecen y si el LLM o el regex fallan.

### 4. Integración gradual
- `estado.py` y `build_circuitos.py` consumen la salida LLM **primero** con
  fallback al regex por post (si el post no está en la caché LLM, regex).
- Empezar por lo que más se rompe: identificación de circuitos en partes de
  restablecimiento y déficit. Los bloques/averías siguen con regex hasta ver
  discrepancias en cero por una semana.

### 5. Verificación ampliada
- Añadir a `verificar_datos.py`: chequeo de `por_confirmar` acumulados (si un
  código lleva 3+ apariciones, proponer promoverlo al catálogo en el issue) y
  resumen de discrepancias regex/LLM.

## Costos y límites
- Workers AI free tier (~10k neurons/día); ~20-40 partes/día → sobra.
- La caché por message_id garantiza que el cron de 10 min no repite trabajo.

## Estado
- [x] Tarea 1: resolutor de identidad
- [x] Tarea 2: extractor LLM con caché
- [x] Tarea 3: comparador y registro de discrepancias
- [x] Tarea 4: integración con fallback
- [x] Tarea 5: verificación ampliada
