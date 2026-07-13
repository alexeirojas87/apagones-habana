// El API vive en el worker de Cloudflare Pages (mismo origen en pages.dev).
const API_BASE = location.hostname.endsWith("pages.dev") ? "" : "https://apagones-habana.pages.dev";

const form = document.getElementById("sug-form");
const btn = document.getElementById("sug-enviar");
const estado = document.getElementById("sug-estado");

form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const tipo = document.getElementById("sug-tipo").value;
  const titulo = document.getElementById("sug-titulo").value.trim();
  const detalle = document.getElementById("sug-detalle").value.trim();
  if (titulo.length < 5) {
    estado.className = "sug-estado err";
    estado.textContent = "Escribe un título un poco más descriptivo (mínimo 5 caracteres).";
    return;
  }
  btn.disabled = true;
  const antes = btn.textContent;
  btn.textContent = "Enviando…";
  estado.className = "sug-estado";
  estado.textContent = "";
  try {
    const r = await fetch(`${API_BASE}/api/sugerencia`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ tipo, titulo, detalle }),
    });
    const d = await r.json();
    if (r.ok) {
      form.reset();
      estado.className = "sug-estado ok";
      estado.textContent = "✔ ¡Gracias! Tu sugerencia quedó registrada para revisión.";
    } else {
      estado.className = "sug-estado err";
      estado.textContent = `✖ ${d.error || "No se pudo enviar."}`;
    }
  } catch {
    estado.className = "sug-estado err";
    estado.textContent = "✖ Sin conexión con el servidor. Inténtalo más tarde.";
  } finally {
    btn.disabled = false;
    btn.textContent = antes;
  }
});
