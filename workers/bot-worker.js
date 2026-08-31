// Bot de Telegram para Apagones Habana — agente con tool-calling sobre datos
// precalculados (bot_datos.json, circuitos.json, estado.json publicados en
// Pages) + RAG sobre los partes crudos como herramienta de último recurso.
//
// El LLM NO hace aritmética ni cuenta listas: toda cifra (horas sin corriente,
// cortes, rankings, déficit MW) sale de una herramienta determinista. Así
// "¿cuántas horas lleva el AL53 sin corriente hoy?" se responde con el dato de
// bot_datos.horas_dia, no con excusas sobre rankings.
//
// Endpoints:
//   POST /webhook/{secret}   — webhook de Telegram
//   GET  /setup/{token}      — registrar webhook (configuración inicial)
//
// Env: TELEGRAM_BOT_TOKEN, BOT_WEBHOOK_SECRET, SUPABASE_URL, SUPABASE_SERVICE_KEY,
//      NAN_API_KEY, NAN_BASE_URL (opcional), PAGES_URL (opcional), MODELO_BOT (opcional)

const TELEGRAM_API = "https://api.telegram.org/bot";
const NAN_BASE = (env) => env.NAN_BASE_URL || "https://api.nanbuilders.ai/v1";
const WEB = (env) => env.PAGES_URL || "https://apagones-habana.pages.dev";
const MODELO = (env) => env.MODELO_BOT || "deepseek-v4-flash";
const TTL_DATOS_MS = 10 * 60 * 1000;   // los JSON se publican por cron horario
const MAX_RONDAS_TOOLS = 4;

// ---------------------------------------------------------------------------
// infraestructura

async function tg(method, env, body) {
  const r = await fetch(`${TELEGRAM_API}${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}

function supa(env, ruta, opts = {}) {
  return fetch(`${env.SUPABASE_URL}/rest/v1/${ruta}`, {
    ...opts,
    headers: {
      apikey: env.SUPABASE_SERVICE_KEY,
      authorization: `Bearer ${env.SUPABASE_SERVICE_KEY}`,
      "content-type": "application/json",
      ...(opts.headers || {}),
    },
  });
}

async function callNan(path, body, env) {
  const r = await fetch(`${NAN_BASE(env)}/${path}`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${env.NAN_API_KEY}`,
      "content-type": "application/json",
    },
    body: JSON.stringify(body),
  });
  return r.json();
}

// --- datos publicados (cacheados en el isolate entre mensajes) ---

let CACHE_DATOS = { t: 0 };

async function cargarDatos(env) {
  if (CACHE_DATOS.bot_datos && Date.now() - CACHE_DATOS.t < TTL_DATOS_MS) {
    return CACHE_DATOS;
  }
  const j = (ruta) => fetch(`${WEB(env)}/data/${ruta}`).then((r) => (r.ok ? r.json() : null));
  const [bot_datos, catalogo, estado] = await Promise.all([
    j("bot_datos.json"), j("circuitos.json"), j("estado.json"),
  ]);
  if (!bot_datos || !catalogo) return CACHE_DATOS.bot_datos ? CACHE_DATOS : null;
  const porCodigo = {};
  for (const c of catalogo.circuitos || []) porCodigo[c.codigo] = c;
  CACHE_DATOS = { t: Date.now(), bot_datos, catalogo, porCodigo, estado };
  return CACHE_DATOS;
}

// ---------------------------------------------------------------------------
// herramientas deterministas (el LLM las llama; aquí no hay magia)

function sinAcentos(s) {
  return String(s || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
}

function buscarCircuito(d, codigo) {
  const clave = String(codigo || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  return d.porCodigo[clave] || null;
}

function hoyHabana(offsetDias = 0) {
  // día local habanero (la serie horas_dia usa días locales, no UTC)
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Havana", year: "numeric", month: "2-digit", day: "2-digit",
  });
  const d = new Date(Date.now() + offsetDias * 86400000);
  return fmt.format(d);
}

function horasEnRango(d, codigo, dias) {
  // suma la serie diaria en los últimos *dias* días habaneros
  const serie = (d.bot_datos.horas_dia || {})[codigo] || {};
  let total = 0;
  const detalle = [];
  for (let i = 0; i < dias; i++) {
    const dia = hoyHabana(-i);
    if (serie[dia] != null) {
      total += serie[dia];
      detalle.push({ dia, horas: serie[dia] });
    }
  }
  return { horas: Math.round(total * 10) / 10, detalle };
}

const TOOLS = [
  {
    type: "function",
    function: {
      name: "estado_circuito",
      description: "Estado actual de un circuito: con/sin servicio, desde cuándo, municipio, calles y su acumulado de los últimos 30 días.",
      parameters: {
        type: "object",
        properties: { codigo: { type: "string", description: "Código del circuito, ej: AL53, GC11, P318, 1243" } },
        required: ["codigo"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "horas_circuito",
      description: "Horas sin corriente de un circuito en un período. Responde '¿cuántas horas lleva sin corriente hoy/esta semana?'.",
      parameters: {
        type: "object",
        properties: {
          codigo: { type: "string", description: "Código del circuito" },
          periodo: { type: "string", enum: ["hoy", "ayer", "7d", "30d"], description: "Período consultado (default hoy)" },
        },
        required: ["codigo"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "ranking_peores",
      description: "Circuitos con más horas sin corriente acumuladas (30 días), opcionalmente filtrado por municipio.",
      parameters: {
        type: "object",
        properties: {
          limite: { type: "integer", description: "Cuántos circuitos listar (default 10, máx 40)" },
          municipio: { type: "string", description: "Filtrar por municipio, ej: Guanabacoa" },
        },
      },
    },
  },
  {
    type: "function",
    function: {
      name: "estado_bloques",
      description: "Estado actual de los 6 bloques de La Habana (afectado/con servicio/desconocido) y la rotación DAF vigente.",
      parameters: { type: "object", properties: {} },
    },
  },
  {
    type: "function",
    function: {
      name: "resumen_hoy",
      description: "Resumen del día: déficit MW reciente, circuitos sin servicio ahora, actividad de ayer/hoy y circuitos con más horas hoy.",
      parameters: { type: "object", properties: {} },
    },
  },
  {
    type: "function",
    function: {
      name: "buscar_partes",
      description: "Busca en el texto de los partes oficiales recientes (útil para preguntas narrativas: qué dijo la UNE sobre un lugar, averías concretas, etc.).",
      parameters: {
        type: "object",
        properties: { consulta: { type: "string", description: "La búsqueda en lenguaje natural" } },
        required: ["consulta"],
      },
    },
  },
];

async function ejecutarTool(nombre, args, d, env) {
  try {
    switch (nombre) {
      case "estado_circuito": {
        const c = buscarCircuito(d, args.codigo);
        if (!c) return { error: `No conozco el circuito ${args.codigo}.` };
        return {
          codigo: c.codigo,
          estado: c.estado || "desconocido",
          desde: c.estado_fecha,
          ultima_mencion: c.ultima,
          veces_afectado_ventana: c.veces,
          municipio: c.municipios || c.municipio,
          bloque: c.bloque,
          calles: c.calles,
          ultimos_30_dias: d.bot_datos.circuitos[c.codigo] || null,
        };
      }
      case "horas_circuito": {
        const c = buscarCircuito(d, args.codigo);
        if (!c) return { error: `No conozco el circuito ${args.codigo}.` };
        const agg = d.bot_datos.circuitos[c.codigo] || null;
        if (args.periodo === "30d") {
          return { codigo: c.codigo, periodo: "30d", horas: agg ? agg.horas_total : 0,
                   cortes: agg ? agg.cortes : 0, fuente: "declaraciones de la UNE" };
        }
        if (args.periodo === "ayer") {
          return { codigo: c.codigo, periodo: "ayer", ...horasDiaUnico(d, c.codigo, 1) };
        }
        if (args.periodo === "7d") return { codigo: c.codigo, periodo: "7d", ...horasEnRango(d, c.codigo, 7) };
        // hoy: serie del día + si el corte sigue abierto el bot_datos ya la extendió
        const hoy = horasEnRango(d, c.codigo, 1);
        return {
          codigo: c.codigo, periodo: "hoy", horas: hoy.horas,
          detalle: hoy.detalle,
          acumulado_30d: agg ? agg.horas_total : 0,
          ultima_declaracion: agg ? agg.ultima : null,
          estado_ahora: c.estado || null,
          fuente: "declaraciones de la UNE repartidas por día habanero; si el corte sigue abierto suma hasta ahora",
        };
      }
      case "ranking_peores": {
        let lista = d.bot_datos.ranking_peores || [];
        if (args.municipio) {
          const muni = sinAcentos(args.municipio);
          lista = lista.filter((r) => {
            const c = d.porCodigo[r.codigo];
            const munis = (c && (c.municipios || [c.municipio])) || [];
            return munis.some((m) => sinAcentos(m).includes(muni));
          });
        }
        return {
          ventana_dias: d.bot_datos.ventana_dias,
          circuitos: lista.slice(0, Math.min(args.limite || 10, 40)),
        };
      }
      case "estado_bloques": {
        const bloques = {};
        for (const [b, v] of Object.entries((d.estado && d.estado.bloques) || {})) {
          bloques[b] = { estado: v.estado, desde: v.desde, causa: v.causa };
        }
        return { bloques, daf: (d.catalogo || {}).daf_oficial || null };
      }
      case "resumen_hoy": {
        const bd = d.bot_datos;
        const serie = bd.serie_diaria || [];
        const sinAhora = (d.catalogo.circuitos || [])
          .filter((c) => c.estado === "sin servicio" && c.estado_fecha &&
                         (Date.parse(bd.generado) - Date.parse(c.estado_fecha)) < 86400000 * 2)
          .map((c) => c.codigo);
        const topHoy = Object.entries(bd.horas_dia || {})
          .map(([codigo, dias]) => ({ codigo, horas: dias[hoyHabana()] }))
          .filter((x) => x.horas)
          .sort((a, b) => b.horas - a.horas)
          .slice(0, 10);
        const mw = (bd.deficit_mw.reciente || []).slice(-1)[0] || null;
        return {
          generado: bd.generado,
          deficit_mw_reciente: mw,
          circuitos_sin_servicio_recientes: sinAhora,
          top_horas_hoy: topHoy,
          actividad: serie.slice(-2),
        };
      }
      case "buscar_partes":
        return await buscarPartes(args.consulta || "", env);
      default:
        return { error: `herramienta desconocida: ${nombre}` };
    }
  } catch (e) {
    return { error: `fallo ejecutando ${nombre}: ${e.message}` };
  }
}

function horasDiaUnico(d, codigo, offset) {
  const serie = (d.bot_datos.horas_dia || {})[codigo] || {};
  const h = serie[hoyHabana(-offset)] || 0;
  return { horas: h };
}

// ---------------------------------------------------------------------------
// RAG sobre partes crudos (herramienta narrativa)

async function embedConsulta(texto, env) {
  const r = await callNan("embeddings", {
    model: "qwen3-embedding", input: texto.slice(0, 512),
    // dimensions: 1024 debe coincidir con lo indexado por embeddings.py; sin
    // este parámetro el modelo devuelve 4096 y el vector no es comparable
    dimensions: 1024,
  }, env);
  return r.data?.[0]?.embedding;
}

// Búsqueda semántica vía la función SQL buscar_fragmentos (pgvector, igual que
// la web). Antes consultaba las tablas chatbot_metadata/chatbot_embeddings, que
// no existen (el índice vive en chatbot_fragmentos), y la herramienta siempre
// respondía "buscador no disponible".
// La similitud absoluta de qwen3-embedding varía mucho según la pregunta, así
// que se usa un corte bajo + un corte RELATIVO al mejor resultado (calibrado en
// web/_worker.js) y se deja al LLM leer la relevancia.
const SIM_MINIMA = 0.25;
const SIM_RELATIVA = 0.75;

async function buscarPartes(consulta, env) {
  const vec = await embedConsulta(consulta, env);
  if (!vec) return { error: "buscador no disponible" };
  let filas;
  try {
    const r = await supa(env, "rpc/buscar_fragmentos", {
      method: "POST",
      body: JSON.stringify({ query_embedding: vec, match_count: 6, tipos: null }),
    });
    if (!r.ok) return { sin_indice: true, nota: "el histórico todavía no está indexado" };
    filas = await r.json();
  } catch (e) {
    return { sin_indice: true, nota: "el histórico todavía no está indexado" };
  }
  if (!Array.isArray(filas)) return { sin_indice: true, nota: "el histórico todavía no está indexado" };
  const validas = filas.filter((f) => (f.similitud || 0) >= SIM_MINIMA);
  if (!validas.length) return { resultados: [] };
  const mejor = validas[0].similitud || 0;
  const resultados = validas
    .filter((f) => (f.similitud || 0) >= mejor * SIM_RELATIVA)
    .map((f) => `[${(f.fecha || "").slice(0, 16).replace("T", " ")}] ${f.texto || ""}`)
    .filter((x) => x.length > 20);
  return { resultados };
}

// ---------------------------------------------------------------------------
// agente: LLM + tools en bucle

function systemPrompt(d) {
  return [
    "Eres el bot de Apagones La Habana. Respondes a vecinos sobre apagones.",
    `Hoy es ${hoyHabana()} en La Habana (los datos usan días habaneros).`,
    "Reglas:",
    "1) Para CUALQUIER cifra (horas, cortes, rankings, MW) llama a las herramientas; nunca inventes ni calcules números por tu cuenta.",
    "2) Usa horas_circuito para '¿cuántas horas lleva X (hoy/esta semana)?' y estado_circuito para '¿qué pasa con X?'.",
    "3) Si el circuito no aparece o sus horas son 0 hoy, dilo con naturalidad y da la última información conocida.",
    "4) busca_partes es para preguntas narrativas o de contexto, no para cifras.",
    "5) Responde en español, conciso, informal, sin markdown pesado (Telegram).",
  ].join("\n");
}

async function agente(consulta, env) {
  const d = await cargarDatos(env);
  if (!d) return "No pude cargar los datos ahora mismo. Prueba en unos minutos.";

  const messages = [
    { role: "system", content: systemPrompt(d) },
    { role: "user", content: consulta },
  ];
  for (let ronda = 0; ronda < MAX_RONDAS_TOOLS; ronda++) {
    const r = await callNan("chat/completions", {
      model: MODELO(env), messages, tools: TOOLS, temperature: 0.2,
    }, env);
    const m = r.choices?.[0]?.message;
    if (!m) return null;
    messages.push(m);
    const llamadas = m.tool_calls || [];
    if (!llamadas.length) return m.content;
    for (const tc of llamadas) {
      let args = {};
      try { args = JSON.parse(tc.function.arguments || "{}"); } catch { /* args vacíos */ }
      const out = await ejecutarTool(tc.function.name, args, d, env);
      messages.push({
        role: "tool",
        tool_call_id: tc.id,
        content: JSON.stringify(out).slice(0, 6000),
      });
    }
  }
  return "Me hice un lío con la consulta. Prueba con algo más directo, ej: 'horas del AL53 hoy'.";
}

// ---------------------------------------------------------------------------
// Telegram

async function estadoBloque(env) {
  const r = await supa(env, `eventos?select=bloque,tipo,fecha,municipios&order=fecha.desc&limit=20`);
  const datos = await r.json();
  if (!Array.isArray(datos)) return "No hay datos disponibles.";
  const porBloque = {};
  for (const e of datos) {
    if (!porBloque[e.bloque]) porBloque[e.bloque] = { tipo: e.tipo, fecha: e.fecha?.slice(0, 16), municipios: e.municipios || [] };
  }
  const lineas = Object.entries(porBloque).map(([b, v]) => {
    const emoji = v.tipo === "afectacion" ? "🔴" : v.tipo === "restablecimiento" ? "🟢" : "⚪";
    const muns = v.municipios.length > 3 ? `${v.municipios.slice(0, 3).join(", ")}...` : v.municipios.join(", ");
    return `${emoji} Bloque ${b}: ${v.tipo} (${v.fecha}) ${v.municipios.length > 0 ? "- " + muns : ""}`;
  });
  return lineas.join("\n") || "Sin eventos recientes.";
}

async function handleMessage(msg, env) {
  const chatId = msg.chat.id;
  const text = (msg.text || "").trim();
  const name = msg.from?.first_name || "vecino";

  if (text === "/start") {
    return tg("sendMessage", env, {
      chat_id: chatId,
      text: `Hola ${name}! Soy el bot de Apagones La Habana.\n\nPregúntame en lenguaje natural, ej:\n· "¿cuántas horas lleva el AL53 sin corriente hoy?"\n· "¿qué pasa en Marianao?"\n· "¿cuáles son los peores circuitos?"\n· "resumen de hoy"\n\nComandos: /estado, /suscribir`,
    });
  }

  if (text === "/suscribir") {
    return tg("sendMessage", env, {
      chat_id: chatId,
      text: "Función de suscripciones en desarrollo. Mientras, puedes consultar el mapa en https://apagones-habana.pages.dev",
    });
  }

  // atajo rápido sin LLM: estado por bloque
  if (/^\/estado\s*$/i.test(text)) {
    const estado = await estadoBloque(env);
    return tg("sendMessage", env, { chat_id: chatId, text: estado });
  }

  let resp = null;
  try {
    resp = await agente(text, env);
  } catch (e) {
    // sin esto, un fallo de red/JSON deja al usuario sin respuesta alguna
    console.error("agente error:", e);
  }
  return tg("sendMessage", env, {
    chat_id: chatId,
    text: resp || "No pude procesar la consulta ahora mismo (problemas con el proveedor de IA). Prueba de nuevo en un rato o usa /estado.",
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === `/webhook/${env.BOT_WEBHOOK_SECRET}` && request.method === "POST") {
      const update = await request.json();
      const msg = update.message;
      if (msg?.text) {
        await handleMessage(msg, env).catch((e) => console.error("handleMessage error:", e));
      }
      return new Response("ok", { status: 200 });
    }

    // GET /setup/{token} — registrar webhook (una vez)
    const setupMatch = url.pathname.match(/^\/setup\/(.+)$/);
    if (setupMatch && request.method === "GET") {
      const token = setupMatch[1];
      if (token !== env.BOT_WEBHOOK_SECRET) return new Response("no", { status: 403 });
      const webhookUrl = `${url.origin}/webhook/${token}`;
      const r = await tg("setWebhook", env, { url: webhookUrl });
      return new Response(JSON.stringify(r), {
        headers: { "content-type": "application/json" },
      });
    }

    return new Response("Bot de Apagones Habana — usa POST /webhook/{secret}", { status: 200 });
  },
};
