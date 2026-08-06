# Plan: NaN Builders — LLM sin restricciones para Apagones Habana

Estado: **propuesto**. Fecha: 2026-08-06.

## Motivación

Hoy operamos con NVIDIA NIM (preferido) + Cloudflare Workers AI (respaldo). Ambas
capas imponen restricciones que nos obligan a un diseño conservador:

| Limitación | Impacto |
|---|---|
| Truncar partes a 2500 chars | Se pierden circuitos del final del parte |
| Truncar comentarios a 800 chars | Contexto insuficiente para entender jerga cubana |
| Tope de 600 partes/día + 250 comentarios/día | Procesamiento parcial, fallback a reglas |
| Sleep de 1.5s entre calls | Pipeline más lento |
| Rate limits (429) y cuota agotada | Posts sin procesar hasta la próxima corrida |

NaN builders elimina **todas** estas restricciones. Este plan describe cómo migrar
y qué construir ahora que es posible.

## Modelos disponibles en NaN

| Modelo | Uso previsto |
|---|---|
| `deepseek-v4-flash` / `deepseek-v4-flash-0731` | Extracción de partes, análisis de comentarios, generación de resúmenes, chat RAG |
| `qwen3-embedding` | Embeddings semánticos para búsqueda en histórico de partes |
| `rerank` | Re-ranking de resultados de búsqueda semántica |
| `whisper` | Transcripción de mensajes de voz de Telegram |
| `gemma4`, `glm5.2`, `mimo-v2.5`, `qwen3.6` | Modelos alternativos para experimentación |

## Fases

### Fase 1: NaN como proveedor principal (`llm_provider.py`)

Añadir NaN builders como el proveedor **preferido** (por encima de NVIDIA), con
el mismo patrón de failover: NaN → NVIDIA → Cloudflare.

**Cambios concretos:**
- Nuevo método `_nan()` en `llm_provider.py` (API compatible OpenAI)
- `proveedor_preferido()` devuelve `"nan"` si hay `NAN_API_KEY`
- `_orden_proveedores()` → `["nan", "nvidia", "cloudflare"]`
- El método `_disponible()` chequea `NAN_API_KEY`
- NO hay cuota diaria para NaN (se omite el chequeo de `llm_cuota`)
- NO hay sleep entre calls cuando el proveedor es NaN

**Modelos por defecto:**
```python
MODELOS = {
    "partes":    {"nan": "deepseek-v4-flash", "nvidia": "openai/gpt-oss-120b", "cloudflare": "@cf/meta/llama-3.3-70b-instruct-fp8-fast"},
    "comentarios": {"nan": "deepseek-v4-flash", "nvidia": "openai/gpt-oss-20b", "cloudflare": "@cf/meta/llama-3.3-70b-instruct-fp8-fast"},
    "geocode":    {"nan": "deepseek-v4-flash", "cloudflare": "@cf/meta/llama-3.3-70b-instruct-fp8-fast"},
}
```

### Fase 2: Extracción de partes sin truncar (`partes_llm.py`)

deepseek-v4-flash tiene contexto grande — podemos pasar el post **completo**.

**Cambios concretos:**
- Eliminar el truncado a 2500 chars: pasar `texto[:8000]` o incluso el post entero
- Subir `MAX_LLM_PARTES` de 15 a ~200 (sin cuota que lo limite)
- Eliminar el `time.sleep(1.5)` cuando el proveedor es NaN
- Prompt mejorado para que deepseek extraiga:
  - **Subestaciones** (prefijos A/D/PG/PZ/AL/GC...)
  - **Relaciones causales** entre circuitos dentro del mismo post
  - **Horas estimadas de restablecimiento** cuando las menciona
  - **Causa** más granular (descarga automática, rotura de línea, sobrecarga...)

**Beneficio:** cobertura del 100% de los posts (vs ~94% actual) y datos más ricos
por post.

### Fase 3: Procesamiento masivo de comentarios (`comentarios_llm.py`)

Hoy procesamos ~8 comentarios con LLM por corrida (máximo 250/día para no
robarle cuota a partes_llm). Sin cuota, podemos procesar **todos**.

**Cambios concretos:**
- Eliminar el tope `MAX_LLM = 8` — procesar todos los comentarios pendientes
- Eliminar el truncado a 800 chars — deepseek entiende el texto completo
- Eliminar el fallback a `comentarios_reglas` (deepseek es mejor y siempre está disponible)
- Prompt mejorado para que deepseek entienda jerga:
  - "esto está durísimo" → `sin_corriente`
  - "se pasaron otra vez" → `queja`
  - "gracias, ya llegó el circo" → `con_corriente`
  - "¿y pa' bloque 3?" → `pregunta`
- Extraer además: `telefono_operador`, `referencia_local` (cerca del colmado...)

**Beneficio:** el mapa de reportes vecinales pasa de ~10 reportes/día a
potencialmente 100+.

### Fase 4: Bot Telegram + Web Widget (RAG)

El bot vive en dos lugares: web y Telegram. Ambos comparten el mismo backend RAG.

**Web widget** (implementado):
- Widget flotante en todas las páginas (index.html, analitica.html, etc.)
- Bubble flotante abajo a la derecha → panel de chat
- Consultas vía `POST /api/chat` en el Cloudflare Workers de Pages
- Sugerencias rápidas: "¿qué pasa en Marianao?", "estado del bloque 3"

**Bot de Telegram** (workers/bot-worker.js):
- Webhook serverless desplegable como Cloudflare Worker aparte

**Componentes:**

```
Usuario → "¿qué pasa en Marianao?"
                ↓
        qwen3-embedding → search en histórico de partes
                ↓
        rerank → top 5 resultados más relevantes
                ↓
        deepseek-v4-flash → genera respuesta con fuentes
                ↓
        "Afectación en bloque 2 por déficit (14:30h). 
         Circuitos: P318 (Playa, calles 1-5)..."
```

**Comandos del bot:**
- `/estado [lugar]` — "¿qué pasa en Marianao / bloque 3 / Alamar?"
- `/historico [circuito|bloque]` — "últimos 10 partes del circuito P318"
- `/cuando [bloque]` — "¿cuándo fue el último corte en el bloque 5?"
- `/resumen` — resumen de las últimas 24h generado por deepseek
- `/audio` — responde a un mensaje de voz (Whisper + análisis)

**Arquitectura:**
- Servicio serverless (Cloudflare Workers) o script invocado por el webhook
- Embeddings precomputados de todo el histórico (`qwen3-embedding`)
- Caché de respuestas frecuentes para minimizar latencia
- Actualización de embeddings con cada corrida de ingesta

### Fase 5: Análisis y resúmenes automáticos (`build_analitica.py`)

Hoy `build_analitica.py` hace agregaciones simples (count de eventos por
día/bloque). Con deepseek podemos generar análisis cualitativo.

**Nuevos datos generados:**

- **`web/data/resumen_diario.json`** — resumen ejecutivo del día generado por
  deepseek: "Hoy 4 bloques afectados, 15 circuitos distintos, tiempo promedio
  de restablecimiento 4.2h. El bloque 3 tuvo 3 cortes — posible problema
  recurrente en la subestación P."
- **`web/data/patrones.json`** — patrones detectados por deepseek en los
  últimos 30 días: frecuencias por bloque, horas pico de corte, duración
  promedio por bloque
- **`web/data/alertas.json`** — alertas automáticas: "3 restablecimientos
  fallidos en bloque 2 en 6h → posible avería no reportada"

**Visualización:** sección "Análisis" en la web con estos datos.

### Fase 6: Geocodificación mejorada (`geocode_llm.py`)

deepseek es significativamente mejor que llama-3.3-70b entendiendo direcciones
ambiguas. Esto desatasco las zonas que hoy Nominatim no puede ubicar.

**Cambios concretos:**
- Usar deepseek en vez de llama para desenredar descripciones
- Prompt que incluya contexto del municipio y bloque para mejor precisión
- Reintentar las zonas en `data/geocode_fallos.txt` con deepseek

**Beneficio:** reducir las zonas no geocodificadas de ~30 a ~5.

### Fase 7: Whisper para mensajes de voz

El canal de Telegram y su grupo de discusión reciben mensajes de voz. Con
Whisper en NaN podemos transcribirlos y tratarlos como comentarios de texto.

**Implementación:**
- Script `scripts/audio_a_texto.py` que descarga audios de Telegram y los
  transcribe con Whisper
- Los textos transcritos entran al mismo pipeline de `comentarios_llm.py`
- Se guardan con flag `via: "whisper"` para identificar fuente

**Beneficio:** señal de cortes desde la calle sin que nadie escriba una palabra.
Crucial en Cuba donde el dato móvil es caro y mandar texto es más fácil con voz.

## Integración con el pipeline actual

```
Canal Telegram (cada 10 min)
    ↓
ingestor/ingest.py
    ↓
extractor/extract.py (regex — sin cambios)
    ↓
partes_llm.py ← NaN deepseek (sin truncar, sin cuota)
    ↓
comparar_extraccion.py  ← sigue siendo útil (validación)
    ↓
comentarios_llm.py ← NaN deepseek (masivo, sin fallback a reglas)
    ↓
audio_a_texto.py ← NaN whisper (nuevo, opcional)
    ↓
estado.py
    ↓
build_circuitos.py / build_partes.py
    ↓
build_analitica.py ← NaN deepseek (resúmenes y patrones)
    ↓
chatbot/ ← NaN deepseek + qwen3-embedding + rerank (nuevo)
```

## Variables de entorno nuevas

| Variable | Descripción |
|---|---|
| `NAN_API_KEY` | API key de NaN builders |
| `NAN_BASE_URL` | (opcional) URL base de la API NaN |
| `MODELO_NAN_PARTES` | Modelo para partes (defecto: `deepseek-v4-flash`) |
| `MODELO_NAN_COMENTARIOS` | Modelo para comentarios (defecto: `deepseek-v4-flash`) |

## Por qué deepseek-v4-flash en lugar de otros modelos

| Modelo | Por qué NO |
|---|---|
| `gemma4` | Bueno pero inferior a deepseek en extracción estructurada |
| `glm5.2` | No probado; podría experimentarse después |
| `mimo-v2.5` | No probado; reservado para experimentos |
| `qwen3.6` | Buen candidato para pruebas, pero deepseek es más fiable |
| `kokoro` | No identificado; investigar uso |

deepseek-v4-flash es el estándar para extracción y análisis por su
combinación de velocidad, contexto grande y fiabilidad en JSON estructurado.

## Archivos a modificar/crear

- `scripts/llm_provider.py` — añadir proveedor NaN
- `scripts/partes_llm.py` — eliminar truncado, aumentar MAX_LLM, eliminar sleep
- `scripts/comentarios_llm.py` — eliminar topes, truncados y fallback a reglas
- `scripts/geocode_llm.py` — cambiar modelo y prompt
- `scripts/build_analitica.py` — añadir generación de resúmenes con deepseek
- `scripts/audio_a_texto.py` — **nuevo**: Whisper para audios
- `scripts/chatbot/` — **nuevo**: módulo del bot con RAG
- `.github/workflows/ingest.yml` — añadir NAN_API_KEY, paso de audio
- `tests/` — actualizar tests para NaN

## Notas

- Los regex de `extractor/extract.py` y `scripts/estado.py` se **mantienen**
  como validación cruzada y para depuración (comparar_extraccion.py)
- El pipeline nunca debe depender exclusivamente del LLM — si NaN falla,
  NVIDIA/Cloudflare toman el relevo
- Los embeddings del histórico se regeneran una vez al día en `build_analitica.py`
- La caché de respuestas del chatbot se purga cada 24h