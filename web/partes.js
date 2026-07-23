const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function horaHabana(iso) {
  return new Date(iso).toLocaleTimeString("es-CU", {
    hour: "2-digit", minute: "2-digit", timeZone: "America/Havana",
  });
}
function diaHabana(iso) {
  return new Date(iso).toLocaleDateString("es-CU", {
    weekday: "long", day: "numeric", month: "long", timeZone: "America/Havana",
  });
}

let DATOS = null;
const PARTE_ID = Number(new URLSearchParams(location.search).get("id")) || null;
let PARTE_ENFOCADO = false;

function render(filtro = "") {
  const feed = document.getElementById("feed");
  const q = filtro.trim().toLowerCase();
  const partes = (DATOS.partes || []).filter((p) => !q || p.texto.toLowerCase().includes(q));
  if (!partes.length) {
    feed.innerHTML = `<p class="vacio">${q ? "Sin partes que coincidan con el filtro." : "Sin partes recientes."}</p>`;
    return;
  }
  let html = "", diaPrev = null;
  for (const p of partes) {
    const dia = diaHabana(p.fecha);
    if (dia !== diaPrev) { html += `<h2 class="dia">${esc(dia)}</h2>`; diaPrev = dia; }
    const link = `https://t.me/${DATOS.canal}/${p.id}`;
    const cuerpo = esc(p.texto).replace(/\n/g, "<br>");
    html += `<article class="parte${p.id === PARTE_ID ? " destacada" : ""}" id="parte-${p.id}">
      <div class="parte-cab">
        <span class="parte-tag">${esc(p.tag)}</span>
        <span class="parte-hora">${horaHabana(p.fecha)}</span>
        <a class="parte-tg" href="${link}" target="_blank" rel="noopener">ver en Telegram ↗</a>
      </div>
      <div class="parte-txt">${cuerpo}</div>
    </article>`;
  }
  feed.innerHTML = html;
  if (PARTE_ID && !PARTE_ENFOCADO && !q) {
    const objetivo = document.getElementById(`parte-${PARTE_ID}`);
    if (objetivo) {
      PARTE_ENFOCADO = true;
      requestAnimationFrame(() =>
        objetivo.scrollIntoView({ behavior: "smooth", block: "center" }));
    }
  }
}

const filtro = document.getElementById("filtro");
filtro.addEventListener("input", () => DATOS && render(filtro.value));

function cargar() {
  return fetch(`data/partes.json?t=${Date.now()}`)
    .then((r) => r.json())
    .then((d) => {
      DATOS = d;
      document.getElementById("parte-info").textContent =
        `${d.partes.length} partes · actualizado ${horaHabana(d.generado)} (hora de La Habana)`;
      render(filtro.value);  // conserva el filtro escrito
    })
    .catch(() => { if (!DATOS) document.getElementById("parte-info").textContent = "error cargando los partes"; });
}
cargar();
// Auto-refresco sin recargar la página (mantiene el filtro).
setInterval(cargar, 90000);
