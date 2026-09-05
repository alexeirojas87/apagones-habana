# Runbook: retest del sitemap en Search Console

Contexto: el sitemap `sitemap.xml` y las URLs canónicas del sitio cambiaron de
forma (las páginas fijas ahora se sirven extensionless, sin `.html`, y las
entradas del sitemap llevan `<lastmod>` derivado de los datos). Si Google
Search Console mostraba errores de tipo «Couldn't fetch» o «No se puede
obtener» sobre el sitemap o las URLs antiguas, este runbook describe los
pasos que corresponden al lado del usuario para volver a verificar. No hay
ninguna acción de código pendiente: el retest es íntegramente manual en GSC.

## Paso 1 — Reenviar el sitemap exacto

1. Abre [Google Search Console](https://search.google.com/search-console) y
   selecciona la propiedad de **prefijo de URL** del sitio.
2. Ojo con la variante de propiedad: GSC trata `http://` / `https://` y
   `www` / sin `www` como propiedades distintas. Usa la propiedad cuyo
   prefijo coincida exactamente con el sitemap (https, sin www:
   `https://apagones-habana.pages.dev`).
3. En el menú lateral: **Sitemaps** → en «Añadir un sitemap nuevo», introduce
   exactamente:
   `https://apagones-habana.pages.dev/sitemap.xml`
4. Pulsa **Enviar** y espera a que el estado pase de «Se envió» a procesado.

## Paso 2 — Inspección de URL con prueba en vivo

1. En la barra superior, pega una de las URLs nuevas del sitemap (por ejemplo
   `https://apagones-habana.pages.dev/circuitos`) y abre **URL Inspection**.
2. Pulsa **PROBAR URL EN VIVO** (Live test).
3. Resultado esperado:
   - **Page fetch**: `Successful`
   - **Crawl allowed**: `Yes`
   - La URL debe ser la forma extensionless servida con HTTP 200 (las formas
     antiguas con `.html` responden 308 y NO son el objetivo de rastreo).

## Paso 3 — Ventana de espera

Tras el envío, Google reintenta la obtención del sitemap durante unos días y
luego se detiene hasta que se reenvíe. Deja pasar una ventana de **unos
días** antes de juzgar el resultado; los informes de Cobertura / Páginas no
se actualizan al instante.

## Paso 4 — Una sola comprobación de Acciones Manuales

En **Acciones manuales** (Security & Manual Actions) haz una única
comprobación: debe constar que no hay ninguna acción manual ni problema de
seguridad en la propiedad. No hace falta repetirla después de cada envío.

## Nota sobre «Couldn't fetch» transitorio

Un estado **«Couldn't fetch»** (o «No se puede obtener») transitorio en GSC
es una **condición documentada y conocida** del propio Search Console: GSC
muestra el estado de la última obtención y no re-obtiene en tiempo real. Si
la Inspección de URL en vivo del paso 2 responde `Successful`, el estado
antiguo del sitemap es cosmético y desaparece solo con la ventana de espera
del paso 3. **No es un defecto del código ni del despliegue.**

## Resultado esperado

- El sitemap `https://apagones-habana.pages.dev/sitemap.xml` aparece
  **Correcto** (Success), con las 22 URLs listadas.
- Las URLs extensionless se indexan como objetivo de rastreo; las formas
  `.html` quedan como redirecciones 308 fuera del índice.
