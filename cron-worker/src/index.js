// GUARDIÁN de la ingesta: dispara la corrida cada 10 min y, además, VIGILA que la
// web de verdad se esté actualizando. Vive en Cloudflare a propósito: cuando
// GitHub Actions se atasca (corridas zombis en cola, colas colgadas — pasó el
// 13/jul/2026: 18 h sin actualizar), todo lo que viva dentro de GitHub se atasca
// con él. Este Worker es el único componente independiente, así que él se encarga
// de detectar el atasco, matarlo y avisar.
//
// Cada tick:
//   1. mira la edad de estado.json publicado;
//   2. si está viejo (>45 min), CANCELA las corridas zombis (queued/pending
//      viejas, o in_progress pasadas del timeout) para destrabar la cola;
//   3. dispara la ingesta como siempre;
//   4. si lleva MUY viejo (>2 h), abre/comenta un issue de alerta (etiqueta
//      'guardian') para que quede constancia visible.
// El token de GitHub vive como secret del Worker (GH_TOKEN), nunca en el código.
const REPO = "alexeirojas87/apagones-habana";
const WORKFLOW = "ingest.yml";
const WEB = "https://apagones-habana.pages.dev";
const VIEJO_MIN = 45;      // edad que dispara la auto-reparación
const CRITICO_MIN = 120;   // edad que además abre el issue de alerta
const ZOMBI_COLA_MIN = 15; // queued/pending más viejo que esto = zombi
const ZOMBI_RUN_MIN = 25;  // in_progress más viejo que esto = colgado (timeout es 18)

function gh(env, ruta, opts = {}) {
  return fetch(`https://api.github.com/repos/${REPO}${ruta}`, {
    ...opts,
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "apagones-cron-worker",
      "Content-Type": "application/json",
      ...(opts.headers || {}),
    },
  });
}

const dispatch = (env) =>
  gh(env, `/actions/workflows/${WORKFLOW}/dispatches`, {
    method: "POST",
    body: JSON.stringify({ ref: "main" }),
  });

async function edadDatosMin() {
  try {
    const r = await fetch(`${WEB}/data/estado.json?t=${Date.now()}`,
      { headers: { "User-Agent": "apagones-cron-worker" } });
    if (!r.ok) return null;
    const d = await r.json();
    if (!d.generado) return null;
    return (Date.now() - Date.parse(d.generado)) / 60000;
  } catch {
    return null;
  }
}

// Cancela corridas atascadas que retienen el grupo de concurrencia. Devuelve
// cuántas canceló (para el log y el issue).
async function matarZombis(env) {
  let muertas = 0;
  for (const estado of ["queued", "pending", "in_progress"]) {
    const r = await gh(env, `/actions/runs?status=${estado}&per_page=50`);
    if (!r.ok) continue;
    const { workflow_runs = [] } = await r.json();
    const topeMin = estado === "in_progress" ? ZOMBI_RUN_MIN : ZOMBI_COLA_MIN;
    for (const run of workflow_runs) {
      const edad = (Date.now() - Date.parse(run.created_at)) / 60000;
      if (edad > topeMin) {
        const c = await gh(env, `/actions/runs/${run.id}/cancel`, { method: "POST" });
        if (c.ok || c.status === 202) muertas++;
      }
    }
  }
  return muertas;
}

async function alertar(env, edadMin, muertas) {
  // un solo issue de guardián abierto a la vez: si existe, comenta; si no, lo crea
  const q = await gh(env, `/issues?labels=guardian&state=open&per_page=1`);
  const abiertos = q.ok ? await q.json() : [];
  const cuerpo =
    `⚠️ La web lleva ~${Math.round(edadMin)} min sin actualizarse. ` +
    `El guardián canceló ${muertas} corrida(s) atascada(s) y redisparó la ingesta. ` +
    `Si esto se repite, revisar Actions y el estado de GitHub.`;
  if (abiertos.length) {
    await gh(env, `/issues/${abiertos[0].number}/comments`, {
      method: "POST", body: JSON.stringify({ body: cuerpo }),
    });
  } else {
    await gh(env, `/issues`, {
      method: "POST",
      body: JSON.stringify({
        title: "🛟 Guardián: la web dejó de actualizarse (auto-reparación en curso)",
        labels: ["guardian"], body: cuerpo,
      }),
    });
  }
}

async function tick(env) {
  const edad = await edadDatosMin();
  let muertas = 0;
  if (edad !== null && edad > VIEJO_MIN) {
    muertas = await matarZombis(env);
    if (edad > CRITICO_MIN) await alertar(env, edad, muertas);
  }
  const r = await dispatch(env);
  return { edad, muertas, dispatch: r.status };
}

export default {
  // Lo llama el Cron Trigger cada 10 min.
  async scheduled(event, env, ctx) {
    ctx.waitUntil(tick(env));
  },
  // Permite probarlo/forzarlo a mano abriendo la URL del Worker.
  async fetch(request, env) {
    const res = await tick(env);
    return new Response(
      `edad datos: ${res.edad === null ? "?" : Math.round(res.edad) + " min"} | ` +
      `zombis canceladas: ${res.muertas} | dispatch -> HTTP ${res.dispatch}`,
      { status: res.dispatch === 204 ? 200 : 502 }
    );
  },
};
