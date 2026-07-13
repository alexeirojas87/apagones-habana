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

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/api/sugerencia" && request.method === "POST") return crearSugerencia(request, env);
    if (url.pathname === "/api/reporte" && request.method === "POST") return crearReporte(request, env);
    if (url.pathname === "/api/reporte" && request.method === "OPTIONS") {
      return new Response(null, {
        headers: {
          "access-control-allow-origin": "*",
          "access-control-allow-methods": "POST",
          "access-control-allow-headers": "content-type",
        },
      });
    }
    if (url.pathname === "/api/reportes") return listarReportes(env);
    return env.ASSETS.fetch(request);
  },
};
