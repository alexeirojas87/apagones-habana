// Cloudflare Pages worker: API de reportes vecinales + servido de estáticos.
// POST /api/reporte  {lat, lon, direccion}  -> guarda un reporte "sin corriente"
// GET  /api/reportes                        -> puntos agregados de las últimas 6h
//
// Reglas: IP hasheada (nunca en claro), máx. 3 reportes por IP cada 2 horas,
// un punto pasa a "confirmado" con >= 10 IPs distintas en la misma celda.

const BBOX = { latMin: 22.9, latMax: 23.35, lonMin: -82.7, lonMax: -81.9 };
const CONFIRMADOS_MIN = 10;
const VENTANA_H = 6;
const REPO = "alexeirojas87/apagones-habana";  // buzón de sugerencias/bugs -> issues
const SUGERENCIAS_DIA = 5;                       // tope por IP cada 24 h

async function sha256(texto) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(texto));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json", "access-control-allow-origin": "*" },
  });
}

function supa(env, ruta, opciones = {}) {
  return fetch(`${env.SUPABASE_URL}/rest/v1/${ruta}`, {
    ...opciones,
    headers: {
      apikey: env.SUPABASE_SERVICE_KEY,
      authorization: `Bearer ${env.SUPABASE_SERVICE_KEY}`,
      "content-type": "application/json",
      ...(opciones.headers || {}),
    },
  });
}

async function crearReporte(request, env) {
  let cuerpo;
  try {
    cuerpo = await request.json();
  } catch {
    return json({ error: "JSON inválido" }, 400);
  }
  const lat = Number(cuerpo.lat), lon = Number(cuerpo.lon);
  const direccion = String(cuerpo.direccion || "").slice(0, 140);
  const tipo = cuerpo.tipo === "con" ? "con" : "sin";
  if (!(lat >= BBOX.latMin && lat <= BBOX.latMax && lon >= BBOX.lonMin && lon <= BBOX.lonMax)) {
    return json({ error: "ubicación fuera de La Habana" }, 400);
  }

  const ip = request.headers.get("CF-Connecting-IP") || "0.0.0.0";
  const ipHash = await sha256(ip + (env.REPORTE_SALT || "sal"));

  const desde = new Date(Date.now() - 2 * 3600e3).toISOString();
  const previos = await (
    await supa(env, `reportes?ip_hash=eq.${ipHash}&fecha=gte.${desde}&select=id`)
  ).json();
  if (Array.isArray(previos) && previos.length >= 3) {
    return json({ error: "ya enviaste varios reportes hace poco; inténtalo más tarde" }, 429);
  }

  const res = await supa(env, "reportes", {
    method: "POST",
    body: JSON.stringify({ lat, lon, direccion, ip_hash: ipHash, tipo }),
    headers: { prefer: "return=minimal" },
  });
  if (!res.ok) return json({ error: "no se pudo guardar" }, 500);
  return json({ ok: true });
}

async function listarReportes(env) {
  const desde = new Date(Date.now() - VENTANA_H * 3600e3).toISOString();
  const filas = await (
    await supa(env, `reportes?fecha=gte.${desde}&select=lat,lon,direccion,ip_hash,tipo,fecha&limit=5000`)
  ).json();
  if (!Array.isArray(filas)) return json({ puntos: [] });

  // celdas de ~110m (3 decimales); IPs distintas por celda y por tipo
  const celdas = new Map();
  for (const f of filas) {
    const k = `${f.lat.toFixed(3)},${f.lon.toFixed(3)}`;
    const c = celdas.get(k) || { lats: 0, lons: 0, n: 0, sin: new Set(), con: new Set(), direccion: f.direccion, fecha: f.fecha };
    c.lats += f.lat; c.lons += f.lon; c.n += 1;
    (f.tipo === "con" ? c.con : c.sin).add(f.ip_hash);
    if (f.direccion) c.direccion = f.direccion;
    if (f.fecha > c.fecha) c.fecha = f.fecha;  // reporte más reciente de la celda
    celdas.set(k, c);
  }
  const puntos = [...celdas.values()].map((c) => {
    const tipo = c.con.size > c.sin.size ? "con" : "sin";
    const n = tipo === "con" ? c.con.size : c.sin.size;
    return {
      lat: +(c.lats / c.n).toFixed(5),
      lon: +(c.lons / c.n).toFixed(5),
      tipo,
      reportes: n,
      sin: c.sin.size,
      con: c.con.size,
      confirmado: n >= CONFIRMADOS_MIN,
      fecha: c.fecha,
      direccion: c.direccion || "",
    };
  });
  return new Response(JSON.stringify({ puntos, ventana_h: VENTANA_H, umbral: CONFIRMADOS_MIN }), {
    headers: {
      "content-type": "application/json",
      "access-control-allow-origin": "*",
      "cache-control": "public, max-age=60",
    },
  });
}

// Buzón de sugerencias/bugs: crea un issue en el repo de GitHub (etiqueta
// sugerencia|bug + "desde-web"). Rate-limit por IP hasheada; se registra en
// Supabase (para el límite y como respaldo). La IP nunca va al issue público.
async function crearSugerencia(request, env) {
  let cuerpo;
  try {
    cuerpo = await request.json();
  } catch {
    return json({ error: "JSON inválido" }, 400);
  }
  const tipo = cuerpo.tipo === "bug" ? "bug" : "sugerencia";
  const titulo = String(cuerpo.titulo || "").trim().slice(0, 120);
  const detalle = String(cuerpo.detalle || "").trim().slice(0, 2000);
  if (titulo.length < 5) {
    return json({ error: "Escribe un título un poco más descriptivo (mínimo 5 caracteres)." }, 400);
  }
  if (!env.GITHUB_TOKEN) return json({ error: "el buzón no está configurado" }, 500);

  const ip = request.headers.get("CF-Connecting-IP") || "0.0.0.0";
  const ipHash = await sha256(ip + (env.REPORTE_SALT || "sal"));
  const desde = new Date(Date.now() - 24 * 3600e3).toISOString();
  const previos = await (
    await supa(env, `sugerencias?ip_hash=eq.${ipHash}&fecha=gte.${desde}&select=id`)
  ).json();
  // Falla en seguro: sin la tabla (rate-limit) no abrimos el endpoint a spam.
  if (!Array.isArray(previos)) {
    return json({ error: "el buzón no está disponible ahora mismo" }, 503);
  }
  if (previos.length >= SUGERENCIAS_DIA) {
    return json({ error: "Ya enviaste varias sugerencias hoy. ¡Gracias! Prueba de nuevo mañana." }, 429);
  }

  const emoji = tipo === "bug" ? "🐛" : "💡";
  const cuerpoIssue = `${detalle || "(sin detalle)"}\n\n---\n_Enviado desde la web de Apagones La Habana._`;
  const r = await fetch(`https://api.github.com/repos/${REPO}/issues`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "apagones-web",
      "content-type": "application/json",
    },
    body: JSON.stringify({ title: `${emoji} ${titulo}`, body: cuerpoIssue, labels: [tipo, "desde-web"] }),
  });
  if (!r.ok) return json({ error: "no se pudo registrar la sugerencia, inténtalo más tarde" }, 502);
  const issue = await r.json();

  // registro para el rate-limit y respaldo (si falla, no bloquea la respuesta)
  await supa(env, "sugerencias", {
    method: "POST",
    body: JSON.stringify({ ip_hash: ipHash, tipo, titulo, detalle, issue_number: issue.number }),
    headers: { prefer: "return=minimal" },
  });
  // No devolvemos la URL/número del issue: el repo de GitHub no se expone al cliente.
  return json({ ok: true });
}

const NAN_BASE = (env) => env.NAN_BASE_URL || "https://api.nan.builders/v1";

// ---------------------------------------------------------------------------
// Chat del mapa. El modelo NO recibe el catálogo entero volcado en el prompt
// (eran ~7.600 tokens por mensaje y aun así contaba a mano, que es donde estos
// modelos fallan callados). Recibe un resumen corto y un juego de herramientas
// que se resuelven aquí en código: los filtros y las sumas son exactos.
//
// La búsqueda semántica es UNA herramienta más, para el texto libre histórico
// —partes y reportes vecinales, donde el usuario describe su zona con sus
// palabras—. Lo estructurado nunca pasa por el índice vectorial: la similitud
// devuelve los k más parecidos y no garantiza completitud, así que contar con
// ella daría respuestas confiadas y falsas.

const MODELO_CHAT = "deepseek-v4-flash";
const MAX_PASOS = 4;          // iteraciones de tool calling por pregunta
const TOPE_RESULTADO = 6000;  // chars por resultado devuelto al modelo

function sinAcentos(s) {
  return String(s || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
}

// Estado vigente de un circuito. "nd" = sin noticias hace más de 24 h, que no
// es lo mismo que "con servicio": no lo afirmamos si no lo sabemos.
function estadoVigente(c, est) {
  const t = c.estado_fecha ? new Date(c.estado_fecha) : null;
  const en = est && est.evento_nacional;
  if (en) return (c.estado === "con servicio" && t && t > new Date(en.desde)) ? "con" : "sin";
  if (c.estado === "con servicio") return "con";
  if (c.estado === "sin servicio") {
    if (t && (Date.now() - t) > 24 * 3600000) return "nd";
    return "sin";
  }
  return "asum";
}

function horasSin(c) {
  if (!c.estado_fecha) return null;
  return Math.round(((Date.now() - new Date(c.estado_fecha)) / 3600000) * 10) / 10;
}

function describirCircuito(c, est) {
  const v = estadoVigente(c, est);
  const etiqueta = { con: "con servicio", sin: "sin servicio", nd: "sin noticias hace +24h",
                     asum: "sin cortes reportados" }[v];
  const out = { codigo: c.codigo, estado: etiqueta, municipio: c.municipio || null };
  if (v === "sin") out.horas_sin_luz = horasSin(c);
  if (c.calles) out.zonas = String(c.calles).replace(/\s+/g, " ").slice(0, 300);
  if (c.estado_fecha) out.ultima_actualizacion = c.estado_fecha.slice(0, 16).replace("T", " ");
  if (c.causa) out.causa = c.causa;
  return out;
}

async function cargarContexto(baseUrl) {
  const t = Date.now();
  const [est, cat, bot] = await Promise.all([
    fetch(`${baseUrl}/data/estado.json?t=${t}`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
    fetch(`${baseUrl}/data/circuitos.json?t=${t}`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
    // bot_datos.json puede no existir todavía (lo genera build_bot_datos.py):
    // el histórico se degrada solo, el resto del bot sigue funcionando.
    fetch(`${baseUrl}/data/bot_datos.json?t=${t}`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
  ]);
  return { est, circuitos: (cat && cat.circuitos) || [], bot };
}

function resumenActual(ctx) {
  const { est, circuitos } = ctx;
  const conteo = { sin: 0, con: 0, nd: 0, asum: 0 };
  const porMunicipio = {};
  for (const c of circuitos) {
    const v = estadoVigente(c, est);
    conteo[v]++;
    const m = c.municipio || "sin municipio";
    porMunicipio[m] = porMunicipio[m] || { sin_servicio: 0, con_servicio: 0, total: 0 };
    porMunicipio[m].total++;
    if (v === "sin") porMunicipio[m].sin_servicio++;
    if (v === "con") porMunicipio[m].con_servicio++;
  }
  return {
    total_circuitos: circuitos.length,
    sin_servicio: conteo.sin,
    con_servicio: conteo.con,
    sin_noticias_24h: conteo.nd,
    sin_cortes_reportados: conteo.asum,
    apagon_nacional: !!(est && est.evento_nacional),
    deficit_mw: (est && est.deficit && (est.deficit.mw || est.deficit)) || null,
    por_municipio: porMunicipio,
    actualizado: (est && est.generado ? String(est.generado).slice(0, 16).replace("T", " ") : null),
  };
}

// --- Herramientas -----------------------------------------------------------

const HERRAMIENTAS = [
  {
    type: "function",
    function: {
      name: "buscar_zona",
      description: "Busca circuitos por barrio, reparto, calle o municipio. Devuelve TODOS los que coinciden con su estado actual. Úsala siempre que pregunten por un lugar.",
      parameters: {
        type: "object",
        properties: { lugar: { type: "string", description: "Nombre del barrio, calle, reparto o municipio" } },
        required: ["lugar"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "estado_circuito",
      description: "Estado actual de un circuito por su código (ej. PG940, A1219).",
      parameters: {
        type: "object",
        properties: { codigo: { type: "string" } },
        required: ["codigo"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "historico_municipio",
      description: "Afectaciones, restablecimientos y averías de un municipio en los últimos 30 días.",
      parameters: {
        type: "object",
        properties: { municipio: { type: "string" } },
        required: ["municipio"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "peores_circuitos",
      description: "Ranking de los circuitos con más horas sin servicio acumuladas en los últimos 30 días.",
      parameters: {
        type: "object",
        properties: { limite: { type: "integer", description: "Cuántos devolver (por defecto 10)" } },
      },
    },
  },
  {
    type: "function",
    function: {
      name: "tendencia",
      description: "Serie diaria de afectaciones, restablecimientos y averías, y déficit de generación. Para preguntas sobre si mejora o empeora.",
      parameters: {
        type: "object",
        properties: { dias: { type: "integer", description: "Cuántos días hacia atrás (por defecto 14)" } },
      },
    },
  },
  {
    type: "function",
    function: {
      name: "buscar_historico",
      description: "Búsqueda semántica en partes oficiales y reportes de vecinos pasados. Úsala para preguntas sobre qué pasó antes, averías concretas o lo que reporta la gente de una zona.",
      parameters: {
        type: "object",
        properties: {
          consulta: { type: "string", description: "La pregunta en lenguaje natural" },
          solo_reportes: { type: "boolean", description: "true para ver solo reportes de vecinos" },
        },
        required: ["consulta"],
      },
    },
  },
];

// La similitud absoluta de qwen3-embedding varía mucho según la pregunta
// (medido: 0.34 para una coincidencia buena por nombre de reparto, 0.68 para
// una por tipo de reporte), así que un corte fijo o descarta aciertos o cuela
// ruido. Se usa un mínimo bajo para no perder recall, un corte RELATIVO al
// mejor resultado, y se le pasa la similitud al modelo para que module cuánto
// se fía. Conviene recalibrar RELATIVO cuando el índice esté poblado.
const SIM_MINIMA = 0.25;
const SIM_RELATIVA = 0.75;

function filtrarPorSimilitud(filas) {
  const validas = filas.filter((f) => (f.similitud || 0) >= SIM_MINIMA);
  if (!validas.length) return [];
  const mejor = validas[0].similitud || 0;
  return validas
    .filter((f) => (f.similitud || 0) >= mejor * SIM_RELATIVA)
    .map((f) => ({
      fecha: (f.fecha || "").slice(0, 16).replace("T", " "),
      texto: f.texto,
      relevancia: Math.round((f.similitud || 0) * 100) / 100,
    }));
}

async function buscarHistorico(env, consulta, soloReportes) {
  if (!env.NAN_API_KEY) return { error: "búsqueda histórica no disponible" };
  let vector;
  try {
    const r = await fetch(`${NAN_BASE(env)}/embeddings`, {
      method: "POST",
      headers: { authorization: `Bearer ${env.NAN_API_KEY}`, "content-type": "application/json" },
      // dimensions: 1024 debe coincidir con lo que indexó embeddings.py.
      // Sin este parámetro el modelo devuelve 4096 y la consulta no sería
      // comparable con lo almacenado (ni cabría en el índice ivfflat).
      body: JSON.stringify({
        model: env.MODELO_EMBED || "qwen3-embedding",
        input: [String(consulta).slice(0, 512)],
        dimensions: 1024,
      }),
    });
    const d = await r.json();
    vector = d && d.data && d.data[0] && d.data[0].embedding;
  } catch (e) {
    return { error: "no se pudo procesar la consulta" };
  }
  if (!vector) return { error: "no se pudo procesar la consulta" };

  try {
    const r = await supa(env, "rpc/buscar_fragmentos", {
      method: "POST",
      body: JSON.stringify({
        query_embedding: vector,
        match_count: 6,
        tipos: soloReportes ? ["reporte"] : null,
      }),
    });
    if (!r.ok) return { sin_indice: true, nota: "el histórico todavía no está indexado" };
    const filas = await r.json();
    return filtrarPorSimilitud(Array.isArray(filas) ? filas : []);
  } catch (e) {
    return { sin_indice: true, nota: "el histórico todavía no está indexado" };
  }
}

async function ejecutarHerramienta(nombre, args, ctx, env) {
  const { est, circuitos, bot } = ctx;
  switch (nombre) {
    case "buscar_zona": {
      const q = sinAcentos(args.lugar);
      if (!q) return { error: "falta el lugar" };
      const hits = circuitos.filter((c) =>
        sinAcentos(c.calles).includes(q) || sinAcentos(c.municipio).includes(q) ||
        sinAcentos(c.codigo).includes(q));
      if (!hits.length) return { encontrados: 0, nota: "ninguna zona coincide con ese nombre" };
      const desc = hits.map((c) => describirCircuito(c, est));
      const cuenta = (e) => desc.filter((d) => d.estado === e).length;
      // El desglose va COMPLETO y suma exactamente "encontrados": si solo se
      // mandaran los dos primeros, el modelo tiene que explicar el resto y se
      // lo inventa.
      return {
        encontrados: hits.length,
        sin_servicio: cuenta("sin servicio"),
        con_servicio: cuenta("con servicio"),
        sin_noticias_24h: cuenta("sin noticias hace +24h"),
        sin_cortes_reportados: cuenta("sin cortes reportados"),
        circuitos: desc.slice(0, 25),
        ...(hits.length > 25 ? { nota: `se listan 25 de ${hits.length}` } : {}),
      };
    }
    case "estado_circuito": {
      const cod = sinAcentos(args.codigo).replace(/[^a-z0-9]/g, "");
      const c = circuitos.find((x) => sinAcentos(x.codigo).replace(/[^a-z0-9]/g, "") === cod);
      return c ? describirCircuito(c, est) : { error: `no existe el circuito ${args.codigo}` };
    }
    case "historico_municipio": {
      if (!bot) return { sin_datos: true, nota: "el histórico no está disponible" };
      const q = sinAcentos(args.municipio);
      const clave = Object.keys(bot.municipios || {}).find((m) => sinAcentos(m).includes(q) || q.includes(sinAcentos(m)));
      if (!clave) return { error: `no hay datos de "${args.municipio}"`, municipios: Object.keys(bot.municipios || {}) };
      return { municipio: clave, ventana_dias: bot.ventana_dias, ...bot.municipios[clave] };
    }
    case "peores_circuitos": {
      if (!bot) return { sin_datos: true, nota: "el histórico no está disponible" };
      const n = Math.min(Math.max(parseInt(args.limite, 10) || 10, 1), 40);
      return { ventana_dias: bot.ventana_dias, circuitos: (bot.ranking_peores || []).slice(0, n) };
    }
    case "tendencia": {
      if (!bot) return { sin_datos: true, nota: "el histórico no está disponible" };
      const n = Math.min(Math.max(parseInt(args.dias, 10) || 14, 1), 31);
      return {
        dias: (bot.serie_diaria || []).slice(-n),
        deficit_mw: bot.deficit_mw ? { max: bot.deficit_mw.max, min: bot.deficit_mw.min, media: bot.deficit_mw.media } : null,
        causas: bot.causas || null,
      };
    }
    case "buscar_historico":
      return await buscarHistorico(env, args.consulta, !!args.solo_reportes);
    default:
      return { error: `herramienta desconocida: ${nombre}` };
  }
}

async function llamarModelo(env, messages, conHerramientas) {
  const cuerpo = {
    model: MODELO_CHAT, messages,
    temperature: 0.3, max_tokens: 2048,
  };
  if (conHerramientas) { cuerpo.tools = HERRAMIENTAS; cuerpo.tool_choice = "auto"; }
  const r = await fetch(`${NAN_BASE(env)}/chat/completions`, {
    method: "POST",
    headers: { authorization: `Bearer ${env.NAN_API_KEY}`, "content-type": "application/json" },
    body: JSON.stringify(cuerpo),
  });
  if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 200)}`);
  return await r.json();
}

async function chatRAG(env, request) {
  if (!env.NAN_API_KEY) return { respuesta: "El chat no está configurado." };
  try {
    const body = await request.json();
    const consulta = String(body.consulta || "").slice(0, 500);
    if (!consulta) return { respuesta: "Escríbeme una pregunta." };
    const historial = Array.isArray(body.historial) ? body.historial.slice(-6) : [];
    const baseUrl = `https://${request.url.split("/")[2]}`;
    const ctx = await cargarContexto(baseUrl);

    if (!ctx.circuitos.length) {
      return { respuesta: "Ahora mismo no tengo datos del estado eléctrico. Prueba en unos minutos." };
    }

    const sistema = `Eres el asistente de Apagones Habana, un mapa del estado eléctrico de La Habana.

La Empresa Eléctrica reporta por CIRCUITO, no por bloques: los bloques ya no existen, nunca hables de ellos.

Tienes herramientas para consultar los datos. Úsalas siempre antes de responder algo concreto — no inventes ni estimes.
- Si preguntan por un lugar (barrio, reparto, calle, municipio), usa buscar_zona.
- Si preguntan qué pasó antes o qué reporta la gente, usa buscar_historico.
- Si preguntan por tendencias o los peores circuitos, usa tendencia o peores_circuitos.
Puedes usar varias herramientas antes de contestar.

"sin noticias hace +24h" significa que no hay parte reciente, NO que haya corriente: no afirmes que hay servicio si no consta.

buscar_historico devuelve un campo "relevancia" (0 a 1). Si es baja (<0.4), di que no encontraste nada claro en vez de forzar una respuesta con eso.

Responde en español de Cuba, informal y breve (2-4 frases salvo que pidan detalle). Da cifras concretas cuando las tengas.

ESTADO AHORA MISMO:
${JSON.stringify(resumenActual(ctx))}`;

    const messages = [{ role: "system", content: sistema }, ...historial,
                      { role: "user", content: consulta }];

    let conHerramientas = true;
    for (let paso = 0; paso < MAX_PASOS; paso++) {
      let data;
      try {
        data = await llamarModelo(env, messages, conHerramientas);
      } catch (e) {
        // Si el proveedor rechaza las herramientas, se reintenta sin ellas:
        // el resumen del system prompt basta para no dejar al usuario colgado.
        if (conHerramientas) { conHerramientas = false; continue; }
        throw e;
      }
      const m = data.choices && data.choices[0] && data.choices[0].message;
      if (!m) break;
      const llamadas = m.tool_calls || [];
      if (!llamadas.length) {
        if (m.content) return { respuesta: m.content };
        break;
      }
      messages.push(m);
      for (const lc of llamadas) {
        let args = {};
        try { args = JSON.parse((lc.function && lc.function.arguments) || "{}"); } catch (e) { args = {}; }
        let resultado;
        try {
          resultado = await ejecutarHerramienta(lc.function.name, args, ctx, env);
        } catch (e) {
          resultado = { error: String((e && e.message) || e) };
        }
        messages.push({
          role: "tool", tool_call_id: lc.id, name: lc.function.name,
          content: JSON.stringify(resultado).slice(0, TOPE_RESULTADO),
        });
      }
    }

    // Se agotaron los pasos con el modelo aún pidiendo herramientas: se le
    // fuerza a concluir con lo que ya recopiló.
    try {
      const data = await llamarModelo(env, messages, false);
      const texto = data.choices && data.choices[0] && data.choices[0].message.content;
      if (texto) return { respuesta: texto };
    } catch (e) { /* cae al mensaje de abajo */ }
    return { respuesta: "No pude armar una respuesta con los datos que tengo ahora." };
  } catch (e) {
    return { respuesta: "Error: " + ((e && e.message) || e) };
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/api/sugerencia" && request.method === "POST") return crearSugerencia(request, env);
    if (url.pathname === "/api/reporte" && request.method === "POST") return crearReporte(request, env);
    if (url.pathname === "/api/reporte" && request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: {
          "access-control-allow-origin": "*",
          "access-control-allow-methods": "POST, OPTIONS",
          "access-control-allow-headers": "content-type",
        },
      });
    }
    if (url.pathname === "/api/reportes") return listarReportes(env);
    if (url.pathname === "/api/chat" && request.method === "POST") {
      const resultado = await chatRAG(env, request);
      return new Response(JSON.stringify(resultado), {
        headers: { "content-type": "application/json", "access-control-allow-origin": "*" },
      });
    }
    if (url.pathname === "/api/chat" && request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: {
          "access-control-allow-origin": "*",
          "access-control-allow-methods": "POST, OPTIONS",
          "access-control-allow-headers": "content-type",
        },
      });
    }
    return env.ASSETS.fetch(request);
  },
};
