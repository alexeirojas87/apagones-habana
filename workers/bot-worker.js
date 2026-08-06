// Bot de Telegram para Apagones Habana — webhook serverless en Cloudflare Workers.
// NaN Builders backend via RAG (deepseek + qwen3-embedding + rerank).
// Comandos: /estado, /historico, /cuando, /resumen, /start, /suscribir
//
// Endpoints:
//   POST /webhook/{secret}   — webhook de Telegram
//   GET  /setup/{token}      — registrar webhook (configuración inicial)
//
// Env: TELEGRAM_BOT_TOKEN, BOT_WEBHOOK_SECRET, SUPABASE_URL, SUPABASE_SERVICE_KEY,
//      NAN_API_KEY, NAN_BASE_URL

const TELEGRAM_API = "https://api.telegram.org/bot";
const NAN_BASE = (env) => env.NAN_BASE_URL || "https://api.nanbuilders.ai/v1";
const VENTANA_H = 24;

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

async function embedConsulta(texto, env) {
  const r = await callNan("embeddings", {
    model: "qwen3-embedding", input: texto.slice(0, 512),
  }, env);
  return r.data?.[0]?.embedding;
}

function coseno(a, b) {
  let dot = 0, na = 0, nb = 0;
  for (let i = 0; i < a.length; i++) { dot += a[i] * b[i]; na += a[i] * a[i]; nb += b[i] * b[i]; }
  return dot / (Math.sqrt(na) * Math.sqrt(nb));
}

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

async function consultaRAG(consulta, env) {
  const vec = await embedConsulta(consulta, env);
  if (!vec) return null;
  const meta = await (await supa(env, `chatbot_metadata?select=*&limit=500`)).json();
  if (!Array.isArray(meta)) return null;
  const embeddings = await (await supa(env, `chatbot_embeddings?select=*&limit=500`)).json();
  if (!Array.isArray(embeddings)) return null;
  const idx = {};
  for (const e of embeddings) idx[e.id] = e.embedding;
  const scores = meta.map((m) => ({ id: m.id, sim: coseno(vec, idx[m.id] || []), meta: m }));
  scores.sort((a, b) => b.sim - a.sim);
  const top = scores.slice(0, 5);
  const docs = top.map((t) => `[${t.meta.fecha?.slice(0, 16) || ""}] ${t.meta.texto || ""}`).filter(Boolean);
  if (docs.length === 0) return null;
  const rerank = await callNan("rerank", {
    model: "rerank", query: consulta, documents: docs,
  }, env);
  const resultados = rerank.results || [];
  resultados.sort((a, b) => b.relevance_score - a.relevance_score);
  const topDocs = resultados.slice(0, 3).map((r) => docs[r.index]);
  const contexto = topDocs.join("\n");
  const r = await callNan("chat/completions", {
    model: "deepseek-v4-flash", messages: [
      { role: "system", content: "Eres el bot de Apagones Habana. Responde usando el contexto proporcionado. Sé conciso e informal. Si no hay datos relevantes, sugiere /estado." },
      { role: "user", content: `Contexto:\n${contexto}\n\nPregunta: ${consulta}` },
    ], temperature: 0.3, max_tokens: 1024,
  }, env);
  return r.choices?.[0]?.message?.content || null;
}

async function handleMessage(msg, env) {
  const chatId = msg.chat.id;
  const text = (msg.text || "").trim();
  const name = msg.from?.first_name || "vecino";

  if (text === "/start") {
    return tg("sendMessage", env, {
      chat_id: chatId,
      text: `Hola ${name}! Soy el bot de Apagones La Habana.\n\nComandos:\n/estado — estado actual por bloque\n/estado [lugar] — estado de un lugar específico\n/historico [circuito] — últimas noticias de un circuito\n/resumen — resumen de las últimas 24h\n/suscribir — recibe alertas\n\nTambién puedes preguntarme en lenguaje natural, ej: "¿qué pasa en Marianao?"`,
    });
  }

  if (text === "/suscribir") {
    return tg("sendMessage", env, {
      chat_id: chatId,
      text: "Función de suscripciones en desarrollo. Mientras, puedes consultar el mapa en https://apagones-habana.pages.dev",
    });
  }

  if (/^\/estado\b/i.test(text)) {
    const lugar = text.replace(/^\/estado\s*/i, "").trim();
    if (lugar) {
      const resp = await consultaRAG(`estado actual de ${lugar}`, env);
      if (resp) return tg("sendMessage", env, { chat_id: chatId, text: resp, parse_mode: "Markdown" });
    }
    const estado = await estadoBloque(env);
    return tg("sendMessage", env, { chat_id: chatId, text: estado, parse_mode: "Markdown" });
  }

  if (/^\/resumen\b/i.test(text)) {
    const resp = await consultaRAG("resumen de las últimas 24 horas en La Habana", env);
    return tg("sendMessage", env, {
      chat_id: chatId,
      text: resp || "No hay datos suficientes para un resumen.",
    });
  }

  if (/^\/historico\b/i.test(text)) {
    const circuito = text.replace(/^\/historico\s*/i, "").trim();
    if (!circuito) {
      return tg("sendMessage", env, {
        chat_id: chatId,
        text: "Ejemplo: /historico P318 — dime un código de circuito.",
      });
    }
    const resp = await consultaRAG(`histórico del circuito ${circuito}`, env);
    return tg("sendMessage", env, {
      chat_id: chatId,
      text: resp || `No encontré datos del circuito ${circuito}.`,
    });
  }

  // Lenguaje natural
  const resp = await consultaRAG(text, env);
  if (resp) {
    return tg("sendMessage", env, { chat_id: chatId, text: resp, parse_mode: "Markdown" });
  }
  return tg("sendMessage", env, {
    chat_id: chatId,
    text: "No entendí tu consulta. Prueba /estado, /historico [circuito], o pregúntame en lenguaje natural como '¿qué pasa en Marianao?'",
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