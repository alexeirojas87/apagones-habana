// Dispara la ingesta de datos en GitHub Actions vía workflow_dispatch.
// Existe porque los `schedule` de GitHub Actions son best-effort (se saltan/retrasan
// corridas); los disparos por API sí se ejecutan al instante. El token de GitHub
// vive como secret del Worker (GH_TOKEN), nunca en el código.
const REPO = "alexeirojas87/apagones-habana";
const WORKFLOW = "ingest.yml";

async function dispatch(env) {
  return fetch(
    `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "apagones-cron-worker",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: "main" }),
    }
  );
}

export default {
  // Lo llama el Cron Trigger cada 10 min.
  async scheduled(event, env, ctx) {
    ctx.waitUntil(dispatch(env));
  },
  // Permite probarlo/forzarlo a mano abriendo la URL del Worker.
  async fetch(request, env) {
    const r = await dispatch(env);
    const cuerpo = r.ok ? "" : ` — ${await r.text()}`;
    return new Response(`dispatch ingest.yml -> HTTP ${r.status}${cuerpo}`, {
      status: r.ok ? 200 : 502,
    });
  },
};
