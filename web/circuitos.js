const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function fechaHabana(iso) {
  return new Date(iso).toLocaleDateString("es-CU", {
    day: "numeric", month: "short", timeZone: "America/Havana",
  });
}

let DATOS = null;
let ESTADO = null;  // estado.json: para aplicar la realidad actual al catálogo histórico

// Estado VIGENTE del circuito: MISMA regla que en la portada y el mapa (app.js,
// circuitoVigente) para que los números coincidan en todas las páginas:
//  - SEN caído: todo sin servicio, salvo lo restablecido DESPUÉS del colapso.
//  - Un "con servicio" anterior al apagón vigente de su bloque ya no vale.
//  - Sin dato: circuitos del catálogo oficial nunca vistos en un parte.
function estadoVigente(c) {
  const en = ESTADO && ESTADO.evento_nacional;
  const t = c.estado_fecha ? new Date(c.estado_fecha) : null;
  if (en) {
    if (c.estado === "con servicio" && t && t > new Date(en.desde))
      return { clase: "con", txt: "con servicio", obsoleto: false };
    return { clase: "sin", txt: "sin servicio (SEN caído)", obsoleto: c.estado !== "sin servicio" };
  }
  if (c.estado === "con servicio") {
    const b = c.bloque && ESTADO && ESTADO.bloques ? ESTADO.bloques[c.bloque] : null;
    if (t && b && b.estado === "afectado" && b.desde && t < new Date(b.desde))
      return { clase: "sin", txt: "sin servicio", obsoleto: true };
    return { clase: "con", txt: "con servicio", obsoleto: false };
  }
  if (c.estado === "sin servicio") return { clase: "sin", txt: "sin servicio", obsoleto: false };
  // Nunca reportado afectado -> por descarte se asume con corriente (azul).
  return { clase: "asum", txt: "sin apagones reportados", obsoleto: false };
}

function render(filtro = "") {
  const cont = document.getElementById("lista");
  const q = filtro.trim().toLowerCase();
  const cs = (DATOS.circuitos || []).filter((c) => {
    if (!q) return true;
    return [c.codigo, c.calles, c.municipio, c.bloque && "bloque " + c.bloque]
      .some((x) => x && String(x).toLowerCase().includes(q));
  });
  if (!cs.length) {
    cont.innerHTML = `<p class="vacio">${q ? "Ningún circuito coincide." : "Sin circuitos aún."}</p>`;
    return;
  }
  cont.innerHTML = cs.map((c) => {
    const e = estadoVigente(c);
    const of = c.oficial ? `<span class="circ-of" title="Verificado con la tabla oficial de la Empresa Eléctrica">✓ oficial</span>` : "";
    const daf = c.daf ? `<span class="circ-daf" title="Circuito con microcortes por Disparo Automático de Frecuencia">🟡 DAF</span>` : "";
    // municipio(s): usa la lista oficial si existe (puede ser más de uno)
    const munis = (c.municipios && c.municipios.length) ? c.municipios.join(" · ") : c.municipio;
    const muni = munis ? ` · ${esc(munis)}` : "";
    const calles = c.calles
      ? esc(c.calles) + muni
      : `<span class="circ-sininfo">Sin información de calles por parte de la UNE</span>${muni}`;
    const meta = c.ultima ? `${c.veces}× · visto ${fechaHabana(c.ultima)}` : "catálogo oficial";
    // enlace al mapa solo si el circuito está ubicado (punto o líneas de calles)
    const enMapa = (c.lat != null || (c.lineas && c.lineas.length))
      ? `<a class="circ-mapa" href="index.html?c=${encodeURIComponent(c.codigo)}" title="Centrar el mapa en este circuito">🗺️ ver en el mapa</a>`
      : "";
    return `<article class="circ">
      <div class="circ-cab">
        <span class="circ-cod">${esc(c.codigo)}</span>
        ${of}${daf}
        <span class="circ-est ${e.clase}">${esc(e.txt)}</span>
        <span class="circ-meta">${meta}</span>
        ${enMapa}
      </div>
      <div class="circ-calles">${calles}</div>
    </article>`;
  }).join("");
}

const filtro = document.getElementById("filtro");
filtro.addEventListener("input", () => DATOS && render(filtro.value));

// Enlace directo a un circuito: circuitos.html?c=CÓDIGO -> prefiltra por ese código.
const _cParam = new URLSearchParams(location.search).get("c");
if (_cParam) filtro.value = _cParam;

function cargar() {
  return Promise.all([
    fetch(`data/circuitos.json?t=${Date.now()}`).then((r) => r.json()),
    fetch(`data/estado.json?t=${Date.now()}`).then((r) => r.json()).catch(() => null),
  ])
    .then(([d, est]) => {
      DATOS = d; ESTADO = est;
      let ncon = 0, nsin = 0;
      for (const c of d.circuitos) {
        const cl = estadoVigente(c).clase;
        if (cl === "con") ncon++; else if (cl === "sin") nsin++;
      }
      const nasum = d.circuitos.length - ncon - nsin;
      const sen = est && est.evento_nacional
        ? " · ⚠️ SEN caído: los restablecidos antes del apagón cuentan como sin servicio" : "";
      document.getElementById("circ-info").textContent =
        `${d.circuitos.length} circuitos · 🟢 ${ncon} con servicio · 🔴 ${nsin} sin servicio` +
        `${nasum > 0 ? ` · 🔵 ${nasum} sin apagones reportados` : ""}${sen}`;
      render(filtro.value);  // conserva el filtro escrito
    })
    .catch(() => { if (!DATOS) document.getElementById("circ-info").textContent = "error cargando el catálogo"; });
}
cargar();
// Auto-refresco sin recargar la página (mantiene el filtro).
setInterval(cargar, 90000);
