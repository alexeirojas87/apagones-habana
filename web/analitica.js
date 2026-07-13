const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let DATOS = null;
let RECORD = null;  // {max_apagados, fecha} — pico histórico de circuitos sin servicio a la vez

function fechaISO(d) { return d.toISOString().slice(0, 10); }

// Barras horizontales: items = [{label, valor, sub?}]
function barras(cont, items, opts = {}) {
  const el = document.getElementById(cont);
  if (!items.length) { el.innerHTML = "<p class='vacio'>Sin datos en el rango.</p>"; return; }
  const max = Math.max(...items.map((i) => i.valor)) || 1;
  const color = opts.color || "#e5484d";
  el.innerHTML = items.map((i) => `
    <div class="fila">
      <span class="etq" title="${esc(i.label)}">${opts.link
        ? `<a href="${opts.link(i.label)}">${esc(i.label)}</a>` : esc(i.label)}</span>
      <span class="barra"><span style="width:${Math.round((i.valor / max) * 100)}%;background:${color}"></span></span>
      <span class="val">${opts.fmt ? opts.fmt(i.valor) : i.valor}${i.sub ? ` <em>${esc(i.sub)}</em>` : ""}</span>
    </div>`).join("");
}

// Tira vertical (días/horas): items = [{label, valor}]
function tira(cont, items, color) {
  const el = document.getElementById(cont);
  const max = Math.max(...items.map((i) => i.valor)) || 1;
  el.innerHTML = items.map((i) => `
    <div class="col" title="${esc(i.label)}: ${i.valor}">
      <span class="cbarra" style="height:${Math.round((i.valor / max) * 100)}%;background:${color || "#e5484d"}"></span>
      <span class="clabel">${esc(i.label)}</span>
    </div>`).join("");
}

function conteo(arr, clave) {
  const m = new Map();
  for (const x of arr) { const k = clave(x); if (k == null) continue; m.set(k, (m.get(k) || 0) + 1); }
  return [...m.entries()].map(([label, valor]) => ({ label, valor })).sort((a, b) => b.valor - a.valor);
}

function render() {
  const [desdeD, hastaD] = rangoActual();
  document.getElementById("rango-info").textContent =
    `${desdeD} a ${hastaD} · generado ${new Date(DATOS.generado).toLocaleDateString("es-CU")}`;
  const enD = (f) => f.slice(0, 10) >= desdeD && f.slice(0, 10) <= hastaD;

  const ev = DATOS.eventos.filter((e) => enD(e[0]));
  const com = DATOS.comentarios.filter((c) => enD(c[0]));
  const afect = ev.filter((e) => e[1] === "afectacion");
  const averias = (DATOS.averias || []).filter((a) => enD(a[0]));
  const mw = (DATOS.mw || []).filter((x) => enD(x[0]));

  // --- CIRCUITOS: [fecha, codigo, horas] de los partes de déficit ---
  const circR = (DATOS.circuitos_partes || []).filter((x) => enD(x[0]));
  const maxH = new Map(), freq = new Map();       // por circuito: horas máximas y nº de partes
  const porDiaC = new Map(), porSemC = new Map(); // circuitos distintos por día / semana
  const porParte = new Map();                     // circuitos en un mismo parte (simultáneos)
  for (const [f, cod, h] of circR) {
    maxH.set(cod, Math.max(maxH.get(cod) || 0, h));
    freq.set(cod, (freq.get(cod) || 0) + 1);
    const d = f.slice(0, 10), k = semanaDe(f);
    (porDiaC.get(d) || porDiaC.set(d, new Set()).get(d)).add(cod);
    (porSemC.get(k) || porSemC.set(k, new Set()).get(k)).add(cod);
    (porParte.get(f) || porParte.set(f, new Set()).get(f)).add(cod);
  }
  // récord de circuitos apagados: máximo de circuitos distintos afectados en un
  // mismo día (peor día) y a la vez en un mismo parte.
  let recDia = { n: 0, f: "" }, recSimul = { n: 0, f: "" };
  for (const [d, s] of porDiaC) if (s.size > recDia.n) recDia = { n: s.size, f: d };
  for (const [f, s] of porParte) if (s.size > recSimul.n) recSimul = { n: s.size, f };

  // KPIs
  const kpi = [
    ["Circuitos afectados", maxH.size],
    ["Averías (roturas)", averias.length],
    ["Reportes de vecinos", ev.filter((e) => e[1] === "reporte_sin_servicio").length + com.length],
    ["Avisos DAF", afect.filter((e) => e[3] === "DAF").length],
  ];
  document.getElementById("kpis").innerHTML = kpi.map(([t, n]) =>
    `<div class="kpi"><span class="num">${n}</span><span class="et">${t}</span></div>`).join("");

  // Circuitos con más horas sin corriente (máx en el rango) — etiquetas enlazadas
  const linkCirc = (cod) => `circuitos.html?c=${encodeURIComponent(cod)}`;
  barras("c-circ-horas", [...maxH.entries()].map(([c, h]) => ({ label: c, valor: h }))
    .sort((a, b) => b.valor - a.valor).slice(0, 15), { color: "#e5484d", fmt: (v) => v + " h", link: linkCirc });

  // Circuitos más veces afectados (nº de partes en el rango)
  barras("c-circ-freq", [...freq.entries()].map(([c, n]) => ({ label: c, valor: n }))
    .sort((a, b) => b.valor - a.valor).slice(0, 15), { color: "#e07b00", link: linkCirc });

  // Causas de los avisos
  barras("c-causas", conteo(afect.filter((e) => e[3]), (e) => e[3]), { color: "#7b2d8e" });

  // Municipios (avisos que nombran pocos municipios, los específicos)
  const muni = [];
  for (const e of afect) if ((e[4] || []).length && e[4].length <= 4) for (const m of e[4]) muni.push(m);
  barras("c-municipios", conteo(muni, (m) => m).slice(0, 15), { color: "#e07b00" });

  // Averías por municipio
  barras("c-averias", conteo(averias.filter((a) => a[2]), (a) => a[2]).slice(0, 15), { color: "#b455c8" });

  // Circuitos afectados por día
  const dias = [...porDiaC.entries()].sort().map(([d, s]) => ({ label: d.slice(5), valor: s.size }));
  tira("c-dias", dias);

  // Hora del día en que salen los partes de déficit
  const horas = Array.from({ length: 24 }, (_, h) => ({ label: String(h), valor: 0 }));
  for (const [f] of circR) horas[+f.slice(11, 13)].valor++;
  tira("c-horas", horas, "#1560d0");

  // Lugares reportados por vecinos
  barras("c-lugares", conteo(com, (c) => c[2]).slice(0, 15), { color: "#c25a00" });

  // Récords (dentro del rango)
  const cPorDia = [...porDiaC.entries()].map(([d, s]) => ({ label: d, valor: s.size })).sort((a, b) => b.valor - a.valor);
  const avPorDia = conteo(averias, (a) => a[0].slice(0, 10));
  const maxMw = mw.reduce((a, x) => (x[1] > a[1] ? x : a), ["", 0]);
  let maxCirc = { h: 0, c: "" };
  for (const [c, h] of maxH) if (h > maxCirc.h) maxCirc = { h, c };
  const recAla = RECORD && RECORD.max_apagados;  // pico histórico real (conteo de circuitos)
  const recs = [
    ["🔴 Récord de apagados a la vez", recAla || recDia.n || "—",
      recAla ? `histórico · ${(RECORD.fecha || "").slice(0, 10)}` : (recDia.f ? `en el día ${recDia.f}` : "")],
    ["🔴 Peor día del rango", recDia.n || "—", recDia.f ? `${recDia.f} · circuitos afectados` : ""],
    ["⚡ Mayor déficit", maxMw[1] ? `${maxMw[1]} MW` : "—", maxMw[0] ? maxMw[0].slice(0, 10) : ""],
    ["🕐 Más horas sin corriente", maxCirc.h ? `${maxCirc.h} h` : "—",
      maxCirc.c ? `Circuito <a href="circuitos.html?c=${encodeURIComponent(maxCirc.c)}">${esc(maxCirc.c)}</a>` : ""],
    ["🔧 Día de más averías", avPorDia[0] ? `${avPorDia[0].valor}` : "—", avPorDia[0] ? avPorDia[0].label : ""],
  ];
  // `s` puede traer un enlace ya escapado (código de circuito); el resto son cadenas propias.
  document.getElementById("c-records").innerHTML = recs.map(([t, v, s]) =>
    `<div class="rec"><span class="rt">${t}</span><span class="rv">${esc(v)}</span><span class="rs">${s}</span></div>`).join("");

  // MW del déficit en el tiempo (promedio diario)
  const mwDia = new Map();
  for (const [f, v] of mw) { const d = f.slice(0, 10); const a = mwDia.get(d) || { s: 0, n: 0 }; a.s += v; a.n++; mwDia.set(d, a); }
  tira("c-mw", [...mwDia.entries()].sort().map(([d, a]) => ({ label: d.slice(5), valor: Math.round(a.s / a.n) })), "#e5484d");

  // Tipos de avería
  barras("c-tipos", conteo(averias, (a) => a[1]), { color: "#b455c8" });

  // Tendencia semanal: circuitos distintos afectados y MW medio por semana ISO
  const sem = new Map();
  for (const [k, s] of porSemC) { const a = sem.get(k) || { af: 0, mwS: 0, mwN: 0 }; a.af = s.size; sem.set(k, a); }
  for (const [f, v] of mw) { const k = semanaDe(f); const a = sem.get(k) || { af: 0, mwS: 0, mwN: 0 }; a.mwS += v; a.mwN++; sem.set(k, a); }
  const semanas = [...sem.entries()].sort().map(([k, a]) => ({
    label: k, valor: a.af, sub: a.mwN ? `${Math.round(a.mwS / a.mwN)} MW medio` : "",
  }));
  barras("c-semanas", semanas, { color: "#e07b00" });
}

function semanaDe(fechaMin) {
  const d = new Date(fechaMin.slice(0, 10) + "T00:00:00Z");
  const jueves = new Date(d); jueves.setUTCDate(d.getUTCDate() + 3 - ((d.getUTCDay() + 6) % 7));
  const ene1 = new Date(Date.UTC(jueves.getUTCFullYear(), 0, 1));
  const sem = Math.ceil(((jueves - ene1) / 86400000 + 1) / 7);
  return `${jueves.getUTCFullYear()}-S${String(sem).padStart(2, "0")}`;
}

function hoyISO() {  // "YYYY-MM-DD" en hora local del navegador
  const d = new Date(), p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

let modo = { dias: 0 };
function rangoActual() {
  const fin = hoyISO();
  if (modo.desde && modo.hasta) return [modo.desde, modo.hasta];  // rango personalizado (fechas)
  if (modo.dias === 0.5) return [fin, fin];  // "Hoy"
  if (modo.dias > 0) {
    const d = new Date(); d.setDate(d.getDate() - modo.dias);
    const p = (n) => String(n).padStart(2, "0");
    return [`${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`, fin];
  }
  const min = DATOS.eventos.length ? DATOS.eventos[0][0].slice(0, 10) : fin;  // "Todo"
  return [min, fin];
}

document.querySelectorAll("#controles button").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll("#controles button").forEach((x) => x.classList.remove("sel"));
    b.classList.add("sel");
    modo = { dias: +b.dataset.dias };
    document.getElementById("desde").value = "";
    document.getElementById("hasta").value = "";
    render();
  };
});
function custom() {
  const d = document.getElementById("desde").value, h = document.getElementById("hasta").value;
  if (d && h) {
    document.querySelectorAll("#controles button").forEach((x) => x.classList.remove("sel"));
    modo = { dias: 0, desde: d, hasta: h };
    render();
  }
}
document.getElementById("desde").onchange = custom;
document.getElementById("hasta").onchange = custom;

function cargar() {
  return Promise.all([
    fetch(`data/analitica.json?t=${Date.now()}`).then((r) => r.json()),
    fetch(`data/circuitos.json?t=${Date.now()}`).then((r) => r.json()).catch(() => null),
  ])
    .then(([d, circ]) => { DATOS = d; RECORD = (circ && circ.record_apagados) || RECORD; render(); })
    .catch(() => { if (!DATOS) document.getElementById("rango-info").textContent = "error cargando datos"; });
}
cargar();
// Auto-refresco de datos sin recargar la página (conserva el rango seleccionado).
setInterval(cargar, 120000);
