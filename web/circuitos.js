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
      return { clase: "con", txt: "con servicio", desde: c.estado_fecha, obsoleto: false };
    return { clase: "sin", txt: "sin servicio (SEN caído)", desde: en.desde,
             obsoleto: c.estado !== "sin servicio" };
  }
  if (c.estado === "con servicio") {
    const b = c.bloque && ESTADO && ESTADO.bloques ? ESTADO.bloques[c.bloque] : null;
    if (t && b && b.estado === "afectado" && b.desde && t < new Date(b.desde))
      return { clase: "sin", txt: "sin servicio", desde: b.desde, obsoleto: true };
    return { clase: "con", txt: "con servicio", desde: c.estado_fecha, obsoleto: false };
  }
  if (c.estado === "sin servicio") {
    // >24 h sin noticias: la UNE no siempre anuncia el restablecimiento ->
    // estado real desconocido (gris), misma regla que el mapa y la portada
    if (t && (Date.now() - t) > 24 * 3600000)
      return { clase: "nd", txt: "sin noticias +24h", desde: c.estado_fecha, obsoleto: false };
    return { clase: "sin", txt: "sin servicio", desde: c.estado_fecha, obsoleto: false };
  }
  // Nunca reportado afectado -> por descarte se asume con corriente (azul).
  return { clase: "asum", txt: "sin apagones reportados", obsoleto: false };
}

// "lleva 3h", "lleva 45 min", "lleva 2d 5h" desde una fecha ISO
function llevaDesde(iso) {
  if (!iso) return "";
  const min = Math.max(0, (Date.now() - new Date(iso)) / 60000);
  if (min < 1) return "";
  if (min < 60) return `lleva ${Math.round(min)} min`;
  const h = min / 60;
  if (h < 48) return `lleva ${Math.round(h * 10) / 10}h`;
  return `lleva ${Math.floor(h / 24)}d ${Math.round(h % 24)}h`;
}

function fechaCompleta(iso) {
  if (!iso) return "";
  const d = /^\d{4}-\d{2}-\d{2}$/.test(iso)
    ? new Date(`${iso}T12:00:00-04:00`) : new Date(iso);
  return d.toLocaleDateString("es-CU", {
    day: "numeric", month: "long", timeZone: "America/Havana",
  });
}

function renderDaf() {
  const cont = document.getElementById("daf-oficial");
  const d = DATOS && DATOS.daf_oficial;
  if (!d) {
    cont.innerHTML = `<div class="daf-card vencido">
      <div class="daf-titulo">🟡 Rotación oficial DAF</div>
      <p>No se ha identificado todavía un parte semanal de la Empresa Eléctrica.</p>
    </div>`;
    return;
  }
  const codigos = d.circuitos || [];
  const estado = d.vigente ? "Vigente" : "Última lista publicada · ya vencida";
  const chips = codigos.map((codigo) =>
    `<button type="button" class="daf-codigo" data-codigo="${esc(codigo)}">${esc(codigo)}</button>`
  ).join("");
  cont.innerHTML = `<div class="daf-card ${d.vigente ? "" : "vencido"}">
    <div class="daf-cab">
      <div>
        <div class="daf-titulo">🟡 Circuitos designados para DAF</div>
        <div class="daf-periodo">${estado} · ${fechaCompleta(d.desde)}–${fechaCompleta(d.hasta)}
          · ${codigos.length} circuito${codigos.length === 1 ? "" : "s"}</div>
      </div>
      <a class="daf-fuente" href="partes.html?id=${encodeURIComponent(d.message_id)}">Ver parte oficial →</a>
    </div>
    <p>Son los circuitos asignados esta semana para proteger el SEN. Estar en esta lista
       no significa que estén apagados ahora: solo se afectan cuando se activa el DAF.</p>
    <div class="daf-codigos">${chips}</div>
  </div>`;
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
  // horas oficiales declaradas en el parte de déficit vigente (ganan al cálculo)
  const horasDef = {};
  if (ESTADO && ESTADO.deficit && ESTADO.deficit.circuitos)
    for (const d of ESTADO.deficit.circuitos) horasDef[d.codigo] = d.horas;
  cont.innerHTML = cs.map((c) => {
    const e = estadoVigente(c);
    const lleva = e.clase === "sin" && horasDef[c.codigo] != null
      ? `lleva ${horasDef[c.codigo]}h (según la UNE)`
      : e.clase === "nd"
        ? `se afectó el ${fechaHabana(e.desde)}; sin noticias desde entonces`
        : llevaDesde(e.desde);
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
    const ultimoParte = c.ultima_message_id
      ? `<a class="circ-parte" href="partes.html?id=${encodeURIComponent(c.ultima_message_id)}" title="Abrir la mención oficial más reciente de este circuito">📢 ver último parte</a>`
      : "";
    const acciones = enMapa || ultimoParte
      ? `<span class="circ-acciones">${enMapa}${ultimoParte}</span>` : "";
    return `<article class="circ">
      <div class="circ-cab">
        <span class="circ-cod">${esc(c.codigo)}</span>
        ${of}${daf}
        <span class="circ-est ${e.clase}">${esc(e.txt)}</span>
        ${lleva ? `<span class="circ-lleva ${e.clase}">⏱ ${esc(lleva)}</span>` : ""}
        <span class="circ-meta">${meta}</span>
        ${acciones}
      </div>
      <div class="circ-calles">${calles}</div>
    </article>`;
  }).join("");
}

const filtro = document.getElementById("filtro");
filtro.addEventListener("input", () => DATOS && render(filtro.value));
document.getElementById("daf-oficial").addEventListener("click", (ev) => {
  const boton = ev.target.closest("[data-codigo]");
  if (!boton || !DATOS) return;
  filtro.value = boton.dataset.codigo;
  render(filtro.value);
  document.getElementById("lista").scrollIntoView({ behavior: "smooth" });
});

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
      let ncon = 0, nsin = 0, nnd = 0;
      for (const c of d.circuitos) {
        const cl = estadoVigente(c).clase;
        if (cl === "con") ncon++; else if (cl === "sin") nsin++; else if (cl === "nd") nnd++;
      }
      const nasum = d.circuitos.length - ncon - nsin - nnd;
      const sen = est && est.evento_nacional
        ? " · ⚠️ SEN caído: los restablecidos antes del apagón cuentan como sin servicio" : "";
      document.getElementById("circ-info").textContent =
        `${d.circuitos.length} circuitos · 🟢 ${ncon} con servicio · 🔴 ${nsin} sin servicio` +
        `${nnd > 0 ? ` · ⚪ ${nnd} sin noticias +24h` : ""}` +
        `${nasum > 0 ? ` · 🔵 ${nasum} sin apagones reportados` : ""}${sen}`;
      renderDaf();
      render(filtro.value);  // conserva el filtro escrito
    })
    .catch(() => { if (!DATOS) document.getElementById("circ-info").textContent = "error cargando el catálogo"; });
}
cargar();
// Auto-refresco sin recargar la página (mantiene el filtro).
setInterval(cargar, 90000);
