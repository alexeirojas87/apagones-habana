# Apagones Habana

Mapa en tiempo real del estado eléctrico de La Habana, alimentado por el canal de Telegram
de la Empresa Eléctrica (t.me/EmpresaElectricaDeLaHabana), sus comentarios y reportes de usuarios.

## Dato clave

La geografía oficial se organiza en **6 bloques de apagón** (`data/bloques.json`, transcrito del
PDF oficial). El canal anuncia afectaciones/restablecimientos por bloque y a veces por circuito.
La unidad del mapa es el **bloque** (y dentro de él, zonas por municipio).

## Arquitectura (100% gratis)

```
Canal EELH + comentarios
        │
        ▼
[Ingestor] Telethon (Python) ejecutado por GitHub Actions cron (~cada 5-10 min)
        │  lee mensajes nuevos del canal y del grupo de discusión
        ▼
[Extractor] reglas/regex + LLM con free tier para texto libre de comentarios
        │  → eventos {tipo, bloque, circuitos, municipios, zonas, hora}
        ▼
[BD] Supabase free tier (Postgres + PostGIS incluido)
        │  estado por bloque/zona + histórico crudo
        ▼
[API/Frontend] Cloudflare Pages (estático + Functions)  ← accesible desde Cuba
        │
        ├── Mapa web: MapLibre GL + tiles PMTiles de La Habana servidos como archivo estático
        └── Bot Telegram público (webhook en Cloudflare Workers): /estado, /suscribir, reportes
```

### Por qué estas elecciones

- **Cloudflare Pages/Workers**: gratis y, a diferencia de Vercel/Heroku/AWS, normalmente
  accesible desde IPs cubanas (crítico: los usuarios están en Cuba).
- **Supabase free**: Postgres con PostGIS sin pagar. Solo lo consulta el backend, así que
  su accesibilidad desde Cuba no importa.
- **GitHub Actions cron como "ingestor"**: evita pagar un servidor siempre encendido. Telethon
  corre en modo pull cada N minutos (fetch de mensajes desde el último `message_id` guardado)
  en vez de conexión persistente. Sesión de Telegram guardada como secret (StringSession).
- **Mapa sin dependencias externas**: se genera un extracto de OpenStreetMap de La Habana en
  formato **PMTiles** (con tilemaker o planetiler, una sola vez) y se sirve como archivo
  estático (~15-40 MB) desde el mismo hosting. MapLibre GL lo lee por HTTP range requests.
  Ventajas: cero servicios de tiles externos, cacheable por el navegador, y permite hacer la
  web una PWA que funcione con conectividad pobre.
- **LLM para parsear comentarios**: empezar con regex/reglas (los posts oficiales son muy
  regulares). Para comentarios en lenguaje libre, usar un free tier (p. ej. Gemini) o
  Claude Haiku (centavos/mes a este volumen).

## Notas importantes

- Un bot de Telegram **no puede leer** el canal de la EELH: el ingestor usa una **cuenta de
  usuario dedicada** vía MTProto (Telethon). El bot propio es solo la interfaz pública.
- Los comentarios del canal viven en su grupo de discusión vinculado; el ingestor debe
  suscribirse a ambos.
- El estado debe "envejecer": sin noticias de un bloque en X horas → estado desconocido.

## Datos

- `data/bloques.json`: transcripción del PDF "Bloques de apagón ACTUALES" (6 bloques,
  421 zonas agrupadas por municipio). La asignación bullet→municipio se infirió de la
  maquetación del PDF; conviene revisar los casos ambiguos (marcados con `?`).

## Fases

1. **Datos base** ✅ `data/bloques.json` transcrito del PDF oficial.
2. **Ingestor** ✅ Telethon + GitHub Actions cron (cada ~10 min) → Supabase.
3. **Extractor** ✅ por reglas (`extractor/extract.py`) → tabla `eventos`. ~94% de
   cobertura en posts oficiales; comentarios procesados de forma conservadora.
4. **Mapa** ✅ https://alexeirojas87.github.io/apagones-habana/ — `scripts/estado.py`
   publica `estado.json` estático junto a la web (Leaflet + GeoJSON de municipios,
   sin tiles externos). Mejora futura: basemap de calles con PMTiles.
5. **Bot público** (pendiente): webhook en Cloudflare Workers — consultas, suscripciones, reportes.
