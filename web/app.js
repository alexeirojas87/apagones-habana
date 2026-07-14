const ETIQUETA = { afectado: "SIN CORRIENTE", restablecido: "CON SERVICIO", desconocido: "SIN DATOS" };

function horaHabana(iso) {
  return new Date(iso).toLocaleTimeString("es-CU", {
    hour: "2-digit", minute: "2-digit", timeZone: "America/Havana",
  });
}

// Escapa texto para interpolarlo con seguridad en HTML de popups. Todo lo que
// venga de reportes de usuarios, posts de Telegram o nombres OSM debe pasar por
// aquí antes de ir a un innerHTML/bindPopup (previene XSS almacenado).
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function duracion(desdeIso) {
  const min = Math.max(0, Math.round((Date.now() - new Date(desdeIso)) / 60000));
  return min < 60 ? `${min}m` : `${Math.floor(min / 60)}h ${min % 60}m`;
}

// ¿El punto (lat, lon) cae dentro del anillo [[lon,lat],…]? Ray casting.
function dentroPoly(lat, lon, anillo) {
  let dentro = false;
  for (let i = 0, j = anillo.length - 1; i < anillo.length; j = i++) {
    const yi = anillo[i][1], xi = anillo[i][0], yj = anillo[j][1], xj = anillo[j][0];
    if ((yi > lat) !== (yj > lat) && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) dentro = !dentro;
  }
  return dentro;
}

// Aviso destacado de apagón nacional (desconexión total del SEN).
function avisoNacional(ev) {
  const el = document.getElementById("aviso-nacional");
  if (!ev) { el.hidden = true; el.innerHTML = ""; return; }
  el.hidden = false;
  const pct = ev.restablecido_pct != null
    ? ` Restablecido ~${ev.restablecido_pct}% de la ciudad (parte de las ${horaHabana(ev.pct_fecha)}).` : "";
  el.innerHTML = `🔴 <b>Desconexión total del SEN</b> — apagón nacional: todos los bloques sin corriente
    desde las ${horaHabana(ev.desde)} (lleva ${duracion(ev.desde)}).${pct} El servicio se restablece de forma
    gradual; el estado por bloque se irá actualizando con los avisos oficiales.`;
}

// Aclaración: la Empresa dejó de reportar por bloque y ahora informa por circuito.
function avisoDeficit(d) {
  const el = document.getElementById("aviso-deficit");
  if (!d || !d.por_circuito) { el.hidden = true; el.innerHTML = ""; return; }
  el.hidden = false;
  el.innerHTML = `<span class="def-ico">ℹ️</span> La Empresa Eléctrica ya no reporta por bloques: ahora
    informa la afectación <b>por circuito</b>. Los que podemos ubicar se muestran en el mapa.`;
}

function pintarBloques(bloques, daf) {
  const cont = document.getElementById("bloques");
  cont.innerHTML = "";
  const orden = ["1", "2", "3", "4", "5", "6", "0"];
  for (const n of orden) {
    const b = bloques[n];
    if (!b) continue;
    const div = document.createElement("div");
    // Afectado + restablecimiento reciente en curso = estado intermedio "restableciéndose"
    // (ámbar): la corriente está volviendo de forma gradual, no es un corte pleno.
    const restableciendose = b.estado === "afectado" && b.parcial;
    div.className = `bloque ${restableciendose ? "restableciendose" : b.estado}`;
    const detalle = [];
    if (b.desde && b.estado === "afectado") detalle.push(`lleva ${duracion(b.desde)}`);
    else if (b.desde) detalle.push(`desde ${horaHabana(b.desde)}`);
    if (b.horas_reportadas)
      detalle.push(`usuarios: ~${b.horas_reportadas}h sin luz (${b.n_reportes_horas})`);
    else if (b.reportes_sin) detalle.push(`${b.reportes_sin} reportes sin luz`);
    const etiqueta = restableciendose ? "RESTABLECIÉNDOSE" : ETIQUETA[b.estado];
    div.innerHTML = `<span class="n">${n === "0" ? "CE" : "B" + n}</span>
      <span class="estado">${etiqueta}</span>
      <span class="detalle">${detalle.join(" · ") || (b.causa || "")}</span>`;
    div.title = n === "0" ? "Circuitos de Emergencia" : b.causa || "";
    cont.appendChild(div);
  }
  // Tarjeta DAF (microcortes)
  const div = document.createElement("div");
  const claseDaf = { activo: "afectado", restablecido: "restablecido", sin_eventos: "desconocido" }[daf.estado];
  const txtDaf = { activo: "DISPARO ACTIVO", restablecido: "RESTABLECIDO", sin_eventos: "SIN EVENTOS" }[daf.estado];
  div.className = `bloque ${claseDaf}`;
  div.innerHTML = `<span class="n">DAF</span>
    <span class="estado">${txtDaf}</span>
    <span class="detalle">${daf.desde ? "desde " + horaHabana(daf.desde) : "microcortes"}</span>`;
  div.title = "Disparo Automático por Frecuencia: circuitos con microcortes";
  cont.appendChild(div);
}

const COLOR_ESTADO = { afectado: "#e5484d", restablecido: "#46a758", desconocido: "#6b7686" };

// Población aprox. por municipio de La Habana (claves = nombres canónicos que usa
// el extractor). Permite estimar los afectados ponderando por municipio.
const POB_MUNI = {
  "Playa": 142245, "Plaza": 104629, "Centro Habana": 105713, "Habana Vieja": 64104,
  "Regla": 36181, "Habana del Este": 141392, "Guanabacoa": 109066, "San Miguel del Padrón": 134978,
  "10 de Octubre": 158569, "Cerro": 101381, "Marianao": 111744, "La Lisa": 126593,
  "Boyeros": 170577, "Arroyo Naranjo": 174298, "Cotorro": 68494,
};
const POB_TOTAL = Object.values(POB_MUNI).reduce((a, b) => a + b, 0);

// La UNE reporta por CIRCUITO, no por bloque. Dejamos todo el pintado por bloque
// latente (sin borrar código) por si vuelve a bloques: con false no se pinta nada
// de bloques en el mapa y la portada muestra el resumen por circuito.
const MOSTRAR_BLOQUES = false;
// Las zonas azules "posibles zonas sin apagón" (espacio sin cobertura de la red que
// rota) SÍ se mantienen: no dependen del formato bloque/circuito y marcan dónde no
// se va la corriente. También los circuitos DAF (amarillo).
const MOSTRAR_PROTEGIDAS = true;

// La API de reportes vive en el worker de Cloudflare Pages; si la web se sirve
// desde otro host (github.io), se apunta al dominio principal.
const API_BASE = location.hostname.endsWith("pages.dev") ? "" : "https://apagones-habana.pages.dev";

function popupMunicipio(nombre, d, bloquesMun, estadoBloques, sinUbicar) {
  const faltantes = (sinUbicar || {})[nombre] || [];
  const filas = Object.entries(bloquesMun[nombre] || {}).map(([b, zonas]) => {
    const e = estadoBloques[b] || { estado: "desconocido" };
    const icono = { afectado: "🔴", restablecido: "🟢", desconocido: "⚪" }[e.estado];
    const txt = { afectado: "sin corriente", restablecido: "con servicio", desconocido: "sin datos" }[e.estado];
    const desde = e.desde ? ` desde ${horaHabana(e.desde)}` : "";
    const detalle = esc(zonas.slice(0, 2).join(" · "));
    return `<li>${icono} <b>B${b}</b> ${txt}${desde}<br>
      <span class="hora">${detalle}${zonas.length > 2 ? ` (+${zonas.length - 2} zonas)` : ""}</span></li>`;
  });
  const rep = d?.reportes_sin
    ? `<p class="rep">⚠ ${d.reportes_sin} usuarios reportan estar sin corriente</p>` : "";
  const pendientes = faltantes.length
    ? `<p class="rep">📍 ${faltantes.length} zona(s) de este municipio aún sin ubicar en el mapa,
       p. ej.: <span class="hora">${faltantes.slice(0, 2).map((z) => `B${z.bloque}: ${esc(z.zona.slice(0, 60))}…`).join(" · ")}</span></p>`
    : "";
  return `<div class="popup"><h3>${nombre}</h3>${rep}<ul>${filas.join("")}</ul>${pendientes}</div>`;
}

async function iniciar() {
  const [geo, estadoIni, bloquesMun, zonas, barrios, lineas, poligonos, cuadrantes, sinUbicar, barriosPoly, noRota, catInicial] = await Promise.all([
    fetch("data/municipios.geojson").then((r) => r.json()),
    fetch(`data/estado.json?t=${Date.now()}`).then((r) => r.json()),
    fetch("data/bloques_por_municipio.json").then((r) => r.json()),
    fetch("data/zonas.geojson").then((r) => r.json()),
    fetch("data/barrios.json").then((r) => r.json()),
    fetch("data/zonas_lineas.geojson").then((r) => r.json()).catch(() => ({ features: [] })),
    fetch("data/zonas_poligonos.geojson").then((r) => r.json()).catch(() => ({ features: [] })),
    fetch("data/zonas_cuadrantes.geojson").then((r) => r.json()).catch(() => ({ features: [] })),
    fetch("data/sin_ubicar.json").then((r) => r.json()).catch(() => ({})),
    fetch("data/barrios_poligonos.json").then((r) => r.json()).catch(() => ({})),
    fetch("data/no_rota.geojson").then((r) => r.json()).catch(() => ({ features: [] })),
    fetch(`data/circuitos.json?t=${Date.now()}`).then((r) => r.json()).catch(() => ({ circuitos: [] })),
  ]);

  // `estado` y `catCircuitos` son mutables: el auto-refresco los reasigna y todo
  // lo que los lee (tarjetas, banner, capas) ve el dato nuevo sin recargar.
  let estado = estadoIni;
  let catCircuitos = catInicial;

  // Estado VIGENTE de un circuito. MISMA regla en la portada, el mapa y la
  // pestaña Circuitos para que los números coincidan en todas partes:
  //  - SEN caído: todo cuenta como sin servicio, salvo lo restablecido DESPUÉS
  //    del colapso.
  //  - Un "con servicio" anterior al apagón vigente de su bloque ya no vale.
  //  - "asum": nunca ha aparecido afectado en un parte -> por descarte se asume
  //    con corriente (azul, no verde: es deducción, no dato oficial).
  //  - "nd": sin servicio con >24 h sin noticias -> estado real desconocido (la
  //    UNE no siempre anuncia el restablecimiento); gris, no rojo.
  function circuitoVigente(c) {
    const en = estado.evento_nacional;
    const t = c.estado_fecha ? new Date(c.estado_fecha) : null;
    if (en) return (c.estado === "con servicio" && t && t > new Date(en.desde)) ? "con" : "sin";
    if (c.estado === "con servicio") {
      const b = c.bloque && estado.bloques ? estado.bloques[c.bloque] : null;
      if (t && b && b.estado === "afectado" && b.desde && t < new Date(b.desde)) return "sin";
      return "con";
    }
    if (c.estado === "sin servicio") {
      if (t && (Date.now() - t) > 24 * 3600000) return "nd";
      return "sin";
    }
    return "asum";
  }

  // ¿Resumen expandido? Solo aplica en móvil (en escritorio siempre abierto por
  // CSS). Persiste entre auto-refrescos; por defecto colapsado: el mapa manda.
  let rcAbierto = false;

  // Portada por CIRCUITO (sustituye a las tarjetas de bloque): estimado de personas
  // con/sin corriente, contador de circuitos y Top 5 con más horas sin servicio.
  function resumenCircuitos() {
    const cont = document.getElementById("resumen-circuitos");
    const cat = catCircuitos.circuitos || [];
    let ncon = 0, nsin = 0, nasum = 0, nnd = 0;
    const perMuni = {};  // municipio -> {sin, tot}; "asum" cuenta como con corriente,
                         // "nd" (sin noticias) queda FUERA del estimado: estado desconocido
    for (const c of cat) {
      const v = circuitoVigente(c);
      if (v === "sin") nsin++; else if (v === "con") ncon++;
      else if (v === "nd") nnd++; else nasum++;
      if (c.municipio && POB_MUNI[c.municipio] && v !== "nd") {
        const o = perMuni[c.municipio] || (perMuni[c.municipio] = { sin: 0, tot: 0 });
        o.tot++; if (v === "sin") o.sin++;
      }
    }
    const nf = (n) => Number(Math.round(n)).toLocaleString("es-CU");

    // Población: cifra OFICIAL si la Empresa la publicó; si no, estimado EN TIEMPO
    // REAL ponderado por municipio: en cada municipio, la fracción de SUS circuitos
    // sin servicio × su población; donde no hay datos, se usa el promedio de ciudad.
    const of = estado.poblacion;
    let sinP, conP, sinPct, fuente;
    if (of && of.fuente === "oficial") {
      sinPct = of.sin_pct; sinP = POB_TOTAL * of.sin_pct / 100; conP = POB_TOTAL - sinP;
      fuente = "según cifras de la Empresa";
    } else if (cat.length > 0) {
      const sinCity = nsin / cat.length;  // los "asum" cuentan como con corriente
      sinP = 0;
      for (const [m, pob] of Object.entries(POB_MUNI)) {
        const o = perMuni[m];
        sinP += (o && o.tot >= 2 ? o.sin / o.tot : sinCity) * pob;
      }
      conP = POB_TOTAL - sinP;
      sinPct = Math.round((sinP / POB_TOTAL) * 100);
      fuente = "estimado por circuitos y población por municipio (aprox.)";
    }
    const explic = (of && of.fuente === "oficial")
      ? "Cómo se calcula: a partir del % de la ciudad con servicio que informa la Empresa Eléctrica, sobre la población de La Habana (~1.75 millones)."
      : "Cómo se calcula: en cada municipio, la proporción de sus circuitos sin servicio × la población del municipio; donde no hay datos de circuitos se usa el promedio de la ciudad. Aproximado.";
    // barra de proporción de circuitos (verde/rojo/azul) con leyenda
    const tot = cat.length || 1;
    const pw = (n) => (n / tot * 100).toFixed(1);
    const tipAsum = "Nunca han aparecido afectados en los partes: por descarte se asume que tienen corriente";
    const tipNd = "Reportados sin servicio hace más de 24 h y la UNE no los menciona desde entonces: estado real desconocido";
    const pob = sinP != null ? `
      <div class="rc-box">
        <div class="rc-box-t">Personas afectadas
          <span class="rc-info" tabindex="0" title="${esc(explic)}">ⓘ</span></div>
        <div class="rc-pnums">
          <div class="rc-pnum sin"><b>~${nf(sinP)}</b><small>sin corriente · ${sinPct}%</small></div>
          <div class="rc-pnum con"><b>~${nf(conP)}</b><small>con corriente · ${100 - sinPct}%</small></div>
        </div>
        <div class="rc-fuente">${fuente}</div>
      </div>` : "";

    // Tarjetas de los circuitos con más horas sin corriente: del CATÁLOGO
    // COMPLETO (no solo los ~5 que lista el parte de déficit: un circuito
    // apagado por avería/afectación puede llevar más horas que esos). Las horas
    // oficiales del parte ganan sobre nuestro conteo cuando existen.
    const horasDef = {};
    if (estado.deficit && estado.deficit.circuitos)
      for (const d of estado.deficit.circuitos) horasDef[d.codigo] = d.horas;
    const top = cat
      .filter((c) => circuitoVigente(c) === "sin")
      .map((c) => {
        let h = horasDef[c.codigo], oficialH = h != null;
        if (h == null && c.estado_fecha) {
          let desde = new Date(c.estado_fecha);
          const en = estado.evento_nacional;
          if (en && desde < new Date(en.desde)) desde = new Date(en.desde);
          h = (Date.now() - desde) / 3600000;
        }
        return { c, h: h != null ? Math.round(h * 10) / 10 : null, oficialH };
      })
      // horas creíbles: las oficiales del parte siempre; las nuestras solo si el
      // último parte que lo afectó es de <24 h (un 'sin servicio' sin noticias
      // en días es un restablecimiento que la UNE nunca anunció, no 100+ horas)
      .filter((x) => x.h != null && (x.oficialH || x.h <= 24))
      .sort((a, b) => b.h - a.h).slice(0, 5);
    let cards = "";
    if (top.length) {
      cards = `<div class="rc-box rc-box-cards">
        <div class="rc-box-t">Con más horas sin corriente</div>
        <div class="rc-cards">` + top.map(({ c, h, oficialH }) => `
        <a class="rc-card" href="circuitos.html?c=${encodeURIComponent(c.codigo)}"
           title="${oficialH ? "Horas declaradas por la UNE" : "Horas desde el último parte que lo afectó"} — ver ${esc(c.codigo)}">
          <span class="rc-card-cab"><span class="rc-dot"></span>${esc(c.codigo)}</span>
          <span class="rc-card-h">${h}<small>h sin luz${oficialH ? " · UNE" : ""}</small></span>
          <span class="rc-card-det">${c.calles ? esc(c.calles.slice(0, 42)) : "sin información de calles"}</span>
        </a>`).join("") + `</div></div>`;
    }
    // En MÓVIL el mapa manda: el resumen se colapsa a una barrita compacta que
    // se expande al tocar. En escritorio la barrita no existe y todo va abierto.
    const mini = `<button id="rc-toggle" class="rc-toggle" aria-expanded="${rcAbierto}">
        <span class="rc-mini-barra">
          <span class="seg sin" style="width:${pw(nsin)}%"></span>
          <span class="seg nd" style="width:${pw(nnd)}%"></span>
          <span class="seg con" style="width:${pw(ncon)}%"></span>
          <span class="seg asum" style="width:${pw(nasum)}%"></span>
        </span>
        <span class="rc-mini-txt"><b class="sin">${nsin}</b> sin luz · <b class="con">${ncon}</b> con luz${
          sinP != null ? ` · <b class="sin">~${nf(sinP / 1000)}k</b> personas sin corriente` : ""}</span>
        <span class="rc-flecha">${rcAbierto ? "▲" : "▼"}</span>
      </button>`;
    cont.innerHTML = `${mini}<div id="rc-cuerpo" class="rc-cuerpo${rcAbierto ? " abierto" : ""}">
      <div class="rc-grid${cards ? " tres" : ""}">
      <div class="rc-box">
        <div class="rc-box-t">Circuitos <span class="rc-n">${cat.length}</span>
          <a class="rc-mas" href="circuitos.html">ver todos →</a></div>
        <div class="rc-barra" role="img" aria-label="${ncon} con servicio, ${nsin} sin servicio, ${nnd} sin noticias, ${nasum} sin apagones reportados">
          <span class="seg sin" style="width:${pw(nsin)}%"></span>
          <span class="seg nd" style="width:${pw(nnd)}%"></span>
          <span class="seg con" style="width:${pw(ncon)}%"></span>
          <span class="seg asum" style="width:${pw(nasum)}%"></span>
        </div>
        <div class="rc-chips">
          <span class="rc-chip sin">${nsin} sin servicio</span>
          <span class="rc-chip con">${ncon} con servicio</span>
          ${nnd > 0 ? `<span class="rc-chip nd" tabindex="0" title="${tipNd}">${nnd} sin noticias +24h</span>` : ""}
          ${nasum > 0 ? `<span class="rc-chip asum" tabindex="0" title="${tipAsum}">${nasum} sin apagones reportados</span>` : ""}
        </div>
      </div>${cards}${pob}</div></div>`;
    document.getElementById("rc-toggle").onclick = () => {
      rcAbierto = !rcAbierto;
      document.getElementById("rc-cuerpo").classList.toggle("abierto", rcAbierto);
      const t = document.getElementById("rc-toggle");
      t.setAttribute("aria-expanded", rcAbierto);
      t.querySelector(".rc-flecha").textContent = rcAbierto ? "▲" : "▼";
      setTimeout(() => mapa && mapa.invalidateSize(), 250);  // el mapa recupera el alto
    };
  }

  document.getElementById("actualizado").textContent =
    `actualizado ${horaHabana(estado.generado)} (hora de La Habana)`;
  avisoNacional(estado.evento_nacional);
  avisoDeficit(estado.deficit);
  resumenCircuitos();

  // Muestra de puntos con bloque conocido (para diagnosticar direcciones y filtrar
  // reportes obsoletos). Se construye pronto porque varias capas la necesitan.
  const muestraBloques = [];
  for (const f of lineas.features) {
    const coords = f.geometry.type === "MultiLineString" ? f.geometry.coordinates.flat() : f.geometry.coordinates;
    for (let i = 0; i < coords.length; i += 4) {
      muestraBloques.push([coords[i][1], coords[i][0], f.properties.bloque]);
    }
  }
  for (const f of poligonos.features) {
    const pts = f.geometry.type === "Polygon" ? f.geometry.coordinates[0] : [f.geometry.coordinates];
    for (let i = 0; i < pts.length; i += 3) {
      muestraBloques.push([pts[i][1], pts[i][0], f.properties.bloque]);
    }
  }
  for (const f of zonas.features) {
    const [lon, lat] = f.geometry.coordinates;
    muestraBloques.push([lat, lon, f.properties.bloque]);
  }

  function bloqueEn(lat, lon) {  // bloque más cercano dentro de ~600 m, o null
    let mejor = null, dMin = 600;
    for (const [la, lo, b] of muestraBloques) {
      const d = Math.hypot((la - lat) * 111000, (lo - lon) * 102000);
      if (d < dMin) { dMin = d; mejor = b; }
    }
    return mejor;
  }

  // Un reporte "hay luz" (con corriente) queda OBSOLETO si es ANTERIOR al apagón
  // vigente del bloque (o a la caída del SEN): son puntos verdes de antes del corte
  // que confunden. Excepción: si lo confirman >=10 vecinos, se respeta (puede ser un
  // restablecimiento real). Los reportes "sin corriente" no se filtran.
  function reporteConObsoleto(fechaISO, lat, lon, confirmado) {
    if (confirmado) return false;
    const t = new Date(fechaISO);
    const en = estado.evento_nacional;
    if (en && t < new Date(en.desde)) return true;  // caída del SEN: todos apagados
    const b = bloqueEn(lat, lon);
    const est = b == null ? null : estado.bloques[b];
    return !!(est && est.estado === "afectado" && est.desde && t < new Date(est.desde));
  }

  // preferCanvas: miles de tramos de calle se dibujan en un solo canvas en vez
  // de un nodo SVG por tramo — imprescindible para que no se arrastre.
  const movil = window.innerWidth < 640;
  const mapa = L.map("mapa", { zoomControl: true, attributionControl: true, preferCanvas: true });
  if (movil) {
    // en vertical: costa arriba, ciudad ocupando la pantalla
    mapa.setView([23.0, -82.36], 11);
  } else {
    mapa.fitBounds(L.geoJSON(geo).getBounds(), { padding: [8, 8] });
  }
  mapa.attributionControl.setPrefix("");

  // Basemap de calles: tiles vectoriales de La Habana servidos como archivos
  // estáticos individuales (web/tiles/z/x/y.pbf) — mismo origen, sin CORS ni
  // range requests, funciona igual en Cloudflare y GitHub Pages.
  protomapsL
    .leafletLayer({
      url: "tiles/{z}/{x}/{y}.pbf",
      flavor: "light",
      lang: "es",
      maxDataZoom: 15,
      attribution: "© OpenStreetMap",
    })
    .addTo(mapa);

  // Municipios: solo contorno y popup de resumen; el color lo llevan las zonas.
  L.geoJSON(geo, {
    style: () => ({ color: "#7a8699", weight: 1.2, fillColor: "#ffffff", fillOpacity: 0 }),
    onEachFeature: (f, capa) => {
      const nombre = f.properties.municipio;
      capa.bindPopup(
        popupMunicipio(nombre, estado.municipios[nombre], bloquesMun, estado.bloques, sinUbicar),
        { maxWidth: 320 }
      );
      capa.bindTooltip(nombre, { sticky: true });
    },
  }).addTo(mapa);

  if (MOSTRAR_BLOQUES) {
  const zonasConLinea = new Set();

  // Zonas restablecidas dentro de un bloque afectado (parcial): se tiñen de verde
  // aunque su bloque siga rojo. Identidad = "municipio|nombre" (p. ej. Zona N Alamar).
  const zonasVerdes = new Set(estado.zonas_verdes || []);

  // Barrios/repartos con polígono o punto propio en OSM (Alamar, Cojímar...),
  // rellenos con el estado de su bloque. Tienen prioridad sobre líneas y círculos.
  for (const f of poligonos.features) {
    const p = f.properties;
    zonasConLinea.add(`${p.municipio}|${p.zona}`);
    const e = estado.bloques[p.bloque] || { estado: "desconocido" };
    const verde = zonasVerdes.has(`${p.municipio}|${p.nombre}`);
    const color = verde ? COLOR_ESTADO.restablecido : COLOR_ESTADO[e.estado];
    const desde = e.desde ? ` desde ${horaHabana(e.desde)}` : "";
    const cabecera = verde
      ? `✅ Con servicio (B${p.bloque} en apagón)`
      : `B${p.bloque} — ${ETIQUETA[e.estado]}${desde}`;
    const popup = `<div class="popup"><h3>${cabecera}</h3>
      <p>${esc(p.nombre)} (${esc(p.municipio)})</p><p class="hora">${esc(p.zona)}</p></div>`;
    let capa;
    if (f.geometry.type === "Polygon") {
      capa = L.polygon(
        f.geometry.coordinates[0].map(([lon, lat]) => [lat, lon]),
        { color, weight: 1.5, fillColor: color, fillOpacity: verde ? 0.5 : 0.4 }
      );
    } else {
      const [lon, lat] = f.geometry.coordinates;
      capa = L.circle([lat, lon], {
        radius: 260, weight: 1.5, color, fillColor: color, fillOpacity: verde ? 0.55 : 0.45,
      });
    }
    capa.bindTooltip(`B${p.bloque} · ${esc(p.nombre)}`).bindPopup(popup, { maxWidth: 300 }).addTo(mapa);
  }

  // Cuadrantes: relleno del área encerrada por las calles frontera de la zona.
  for (const f of cuadrantes.features) {
    const p = f.properties;
    const e = estado.bloques[p.bloque] || { estado: "desconocido" };
    L.polygon(
      f.geometry.coordinates[0].map(([lon, lat]) => [lat, lon]),
      { stroke: false, fillColor: COLOR_ESTADO[e.estado], fillOpacity: 0.22, interactive: false }
    ).addTo(mapa);
  }

  // Calles reales de cada zona (geometría OSM), pintadas con el estado del bloque.
  // Se construyen por lotes cediendo el control al navegador entre lote y lote,
  // para no bloquear el hilo principal ("Page Unresponsive") en equipos lentos.
  for (const f of lineas.features) {
    zonasConLinea.add(`${f.properties.municipio}|${f.properties.zona}`);
  }
  const pausa = () => new Promise((r) => setTimeout(r, 0));
  (async () => {
    let i = 0;
    for (const f of lineas.features) {
      const p = f.properties;
      const e = estado.bloques[p.bloque] || { estado: "desconocido" };
      const coords =
        f.geometry.type === "MultiLineString"
          ? f.geometry.coordinates.map((l) => l.map(([lon, lat]) => [lat, lon]))
          : f.geometry.coordinates.map(([lon, lat]) => [lat, lon]);
      const linea = L.polyline(coords, {
        color: COLOR_ESTADO[e.estado], weight: 5,
        opacity: e.estado === "desconocido" ? 0.4 : 0.75,
      }).addTo(mapa);
      const desde = e.desde ? ` desde ${horaHabana(e.desde)}` : "";
      linea.bindTooltip(`B${p.bloque} · ${esc(p.calle)}`, { sticky: true });
      linea.bindPopup(
        `<div class="popup"><h3>B${p.bloque} — ${ETIQUETA[e.estado]}${desde}</h3>
         <p>${esc(p.calle)} (${esc(p.municipio)})</p><p class="hora">${esc(p.zona)}</p></div>`,
        { maxWidth: 300 }
      );
      if (++i % 150 === 0) await pausa();
    }
  })();

  // Zonas sin geometría de calles: círculo aproximado en el centroide.
  for (const f of zonas.features) {
    const p = f.properties;
    if (zonasConLinea.has(`${p.municipio}|${p.zona.slice(0, 160)}`)) continue;
    const e = estado.bloques[p.bloque] || { estado: "desconocido" };
    const [lon, lat] = f.geometry.coordinates;
    const circulo = L.circle([lat, lon], {
      radius: 320,
      stroke: false,
      fillColor: COLOR_ESTADO[e.estado],
      fillOpacity: e.estado === "desconocido" ? 0.35 : 0.6,
    }).addTo(mapa);
    const desde = e.desde ? ` desde ${horaHabana(e.desde)}` : "";
    circulo.bindTooltip(`B${p.bloque} · ${esc(p.match)}`);
    circulo.bindPopup(
      `<div class="popup"><h3>B${p.bloque} — ${ETIQUETA[e.estado]}${desde}</h3>
       <p>${esc(p.municipio)}</p><p class="hora">${esc(p.zona)}</p></div>`,
      { maxWidth: 300 }
    );
  }

  }  // fin MOSTRAR_BLOQUES (pintado por bloque, latente)

  // Busca el límite real del barrio en el catálogo OSM (nombres canónicos).
  const canonJS = (t) =>
    t.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "")
      .replace(/\s*\(.*?\)/g, "").replace(/\b(reparto|rpto\.?|barrio|residencial)\b/g, " ")
      .replace(/[^a-z0-9 ]/g, " ").replace(/\s+/g, " ").trim();
  const polyDe = (nombre) => barriosPoly[canonJS(nombre)];

  // Capas opcionales: circuitos DAF (microcortes) y candidatas a zona protegida.
  const colorBarrio = { daf: "#e6a817", candidata_protegida: "#4a90d9" };
  const capas = { daf: L.layerGroup(), candidata_protegida: L.layerGroup() };
  if (MOSTRAR_PROTEGIDAS) for (const b of barrios) {
    const limite = polyDe(b.nombre);
    const forma = limite
      ? L.polygon(limite.anillo.map(([lon, lat]) => [lat, lon]), {
          color: colorBarrio[b.cat], weight: 1.5,
          fillColor: colorBarrio[b.cat],
          fillOpacity: b.cat === "daf" ? 0.35 : 0.18,
        })
      : L.circleMarker([b.lat, b.lon], {
          radius: 5, weight: 1, color: colorBarrio[b.cat],
          fillColor: colorBarrio[b.cat], fillOpacity: 0.7,
        });
    const nb = esc(b.nombre);
    forma
      .bindTooltip(nb)
      .bindPopup(
        b.cat === "daf"
          ? `<div class="popup"><h3>${nb}</h3><p>Circuito DAF: sufre microcortes por Disparo Automático de Frecuencia, pero no rota en los bloques.</p></div>`
          : b.confirmada
            ? `<div class="popup"><h3>${nb}</h3><p>✔ Zona sin apagón confirmada por vecinos.</p></div>`
            : `<div class="popup"><h3>${nb}</h3><p>No aparece en ningún bloque ni en los disparos DAF: candidata a zona que no rota (dato por exclusión, no oficial).</p></div>`
      )
      .addTo(capas[b.cat]);
  }
  // --- Capas dinámicas (dependen de estado.json): se repueblan en cada refresco
  // sin recrear el mapa ni los tiles. Se limpian y se vuelven a llenar con `estado`.
  const capaAverias = L.layerGroup().addTo(mapa);
  const capaEmergencia = L.layerGroup().addTo(mapa);
  const capaParciales = L.layerGroup().addTo(mapa);
  const capaComentarios = L.layerGroup().addTo(mapa);
  const capaCircuitos = L.layerGroup().addTo(mapa);

  const circuitoCapas = {};  // codigo -> capa del circuito (enfoque por ?c=)

  function poblarDinamicas() {
    capaAverias.clearLayers();
    capaEmergencia.clearLayers();
    capaParciales.clearLayers();
    capaComentarios.clearLayers();
    capaCircuitos.clearLayers();
    for (const k in circuitoCapas) delete circuitoCapas[k];

    // TODOS los circuitos conocidos, coloreados por su último estado (rojo = sin
    // corriente, verde = con servicio, azul = nunca reportado afectado -> se asume
    // con corriente por descarte). Calles reales si las tenemos; si no, bolita
    // grande que engloba la zona. Con el SEN caído, todos en rojo.
    const horasDef = {};
    if (estado.deficit && estado.deficit.circuitos)
      for (const c of estado.deficit.circuitos) horasDef[c.codigo] = c.horas;
    const COL_CIRC = {
      sin: { l: "#e5484d", b: "#8b0000", e: "🔴", txt: "sin corriente" },
      con: { l: "#46a758", b: "#1c5f2b", e: "🟢", txt: "con servicio" },
      asum: { l: "#4a90d9", b: "#2b5c94", e: "🔵", txt: "sin apagones reportados" },
      nd: { l: "#6b7686", b: "#4a525e", e: "⚪", txt: "sin noticias hace +24 h" },
    };
    for (const c of (catCircuitos.circuitos || [])) {
      if (!(c.lineas && c.lineas.length) && c.lat === undefined) continue;  // sin ubicar
      const v = circuitoVigente(c);  // misma regla que la portada y la pestaña Circuitos
      const col = COL_CIRC[v];
      const h = horasDef[c.codigo];
      const calles = c.calles ? esc(c.calles) : "sin información de calles por parte de la UNE";
      const fmtDia = (iso) => new Date(iso).toLocaleDateString("es-CU", { day: "numeric", month: "short", timeZone: "America/Havana" });
      const detalle = v === "asum"
        ? "Nunca ha aparecido afectado en los partes: por descarte se asume con corriente."
        : v === "nd"
          ? `Se reportó sin servicio el ${fmtDia(c.estado_fecha)} y la UNE no lo menciona desde entonces: estado real desconocido.`
          : `${h != null ? "Lleva " + h + "h de afectación. " : ""}Último dato oficial: ${fmtDia(c.ultima)}.`;
      const popup = `<div class="popup"><h3>${col.e} Circuito ${esc(c.codigo)} — ${col.txt}</h3>
         <p>${calles}${c.bloque ? " · (B" + c.bloque + ")" : ""}</p>
         <p class="hora">${detalle} Ubicación aproximada.</p>
         <p><a href="circuitos.html?c=${encodeURIComponent(c.codigo)}">📋 ver ${esc(c.codigo)} en Circuitos →</a></p></div>`;
      const tip = `${col.e} Circuito ${esc(c.codigo)} (${col.txt})`;
      const capa = (c.lineas && c.lineas.length)
        ? L.polyline(c.lineas.map((l) => l.map(([lo, la]) => [la, lo])), {
            color: col.l, weight: 4, opacity: 0.9,
          })
        : L.circle([c.lat, c.lon], {
            radius: 450, weight: 2, color: col.b, fillColor: col.l, fillOpacity: 0.22,
          });
      capa.bindTooltip(tip).bindPopup(popup, { maxWidth: 300 }).addTo(capaCircuitos);
      circuitoCapas[c.codigo] = capa;  // para enfocar con index.html?c=CODIGO
    }

    // Averías activas (roturas): del último parte oficial "Averías existentes".
    for (const a of estado.averias.items) {
      if (a.lat === undefined) continue;
      L.circleMarker([a.lat, a.lon], {
        radius: 7, weight: 2, color: "#7b2d8e", fillColor: "#b455c8", fillOpacity: 0.85,
      })
        .bindTooltip(`🚧 ${esc(a.tipo)}`)
        .bindPopup(
          `<div class="popup"><h3>🚧 ${esc(a.tipo)}</h3>
           <p>${esc(a.direccion)}${a.municipio ? " — " + esc(a.municipio) : ""}</p>
           <p class="hora">Interrupción por rotura (no es rotación de bloque).
           Parte oficial de las ${horaHabana(estado.averias.fecha)}.</p></div>`,
          { maxWidth: 300 }
        )
        .addTo(capaAverias);
    }

    // Cortes de emergencia en circuitos sueltos (sin bloque ni municipio).
    // Con límite real del barrio si OSM lo tiene; si no, círculo de área aproximada.
    for (const z of estado.emergencia.items) {
      if (z.lat === undefined) continue;
      const limite = polyDe(z.nombre);
      const borde = z.restablecido ? "#1c5f2b" : "#8b0000";
      const relleno = z.restablecido ? "#46a758" : "#e5484d";
      const forma = limite
        ? L.polygon(limite.anillo.map(([lon, lat]) => [lat, lon]), {
            color: borde, weight: 2, dashArray: "5",
            fillColor: relleno, fillOpacity: 0.45,
          })
        : L.circle([z.lat, z.lon], {
            radius: 400, weight: 2, color: borde, dashArray: "5",
            fillColor: relleno, fillOpacity: 0.4,
          });
      const nz = esc(z.nombre);
      forma
        .bindTooltip(z.restablecido ? `✅ En restablecimiento: ${nz}` : `⚠ Emergencia: ${nz}`)
        .bindPopup(
          z.restablecido
            ? `<div class="popup"><h3>✅ En restablecimiento</h3>
               <p>${nz}</p>
               <p class="hora">La Empresa anunció que se trabaja en restablecer este circuito
               tras el corte de emergencia de las ${horaHabana(estado.emergencia.fecha)}.</p></div>`
            : `<div class="popup"><h3>⚠ Corte de emergencia</h3>
               <p>${nz}${z.aproximado ? " (ubicación aproximada)" : ""}</p>
               <p class="hora">Afectado por emergencia en la generación nacional
               (circuito fuera de la rotación de bloques). Aviso oficial de las
               ${horaHabana(estado.emergencia.fecha)}.</p></div>`,
          { maxWidth: 300 }
        )
        .addTo(capaEmergencia);
    }

    // Circuitos con servicio dentro de un bloque afectado (restablecimiento parcial).
    for (const c of estado.parciales || []) {
      // parcial anterior al apagón vigente del bloque (o a la caída del SEN) = obsoleto:
      // p.ej. un circuito restablecido a las 11:47 quedó sin efecto al caer el SEN a las 16:18.
      const t = new Date(c.fecha), en = estado.evento_nacional, est = estado.bloques[c.bloque];
      if ((en && t < new Date(en.desde)) ||
          (est && est.estado === "afectado" && est.desde && t < new Date(est.desde))) continue;
      const etqB = c.bloque ? ` (B${c.bloque})` : "";
      const detalleB = c.bloque
        ? `El B${c.bloque} sigue en apagón, pero la Empresa reportó este circuito
           restablecido a las ${horaHabana(c.fecha)}. Puede volver a cortarse en la próxima rotación.`
        : `La Empresa reportó este circuito restablecido a las ${horaHabana(c.fecha)}.`;
      const cod = c.codigo ? ` ${esc(c.codigo)}` : "";
      const verC = c.codigo
        ? `<p><a href="circuitos.html?c=${encodeURIComponent(c.codigo)}">📋 ver ${esc(c.codigo)} en Circuitos →</a></p>` : "";
      L.circleMarker([c.lat, c.lon], {
        radius: 6, weight: 2, color: "#1c5f2b", fillColor: "#46a758", fillOpacity: 0.9,
      })
        .bindTooltip(`✅${cod ? cod + " con" : " Con"} servicio${etqB}: ${esc(c.direccion)}`)
        .bindPopup(
          `<div class="popup"><h3>✅ Circuito${cod} con servicio</h3>
           <p>${esc(c.direccion)}${c.municipio ? " — " + esc(c.municipio) : ""}</p>
           <p class="hora">${detalleB}</p>${verC}</div>`,
          { maxWidth: 300 }
        )
        .addTo(capaParciales);
    }

    // Reportes de vecinos extraídos de los comentarios del canal por el LLM.
    // "sin luz" -> círculo rojo que rodea la zona (como los circuitos); "ya hay luz"
    // -> puntito verde. Damos peso visible a los reportes de apagón de la gente.
    for (const c of estado.reportes_llm || []) {
      const con = c.tipo === "con_corriente";
      // comentario "ya hay luz" anterior al apagón vigente = obsoleto (no hay 10+ aquí)
      if (con && reporteConObsoleto(c.fecha, c.lat, c.lon, false)) continue;
      const horas = c.horas ? ` · ~${c.horas}h sin luz` : "";
      const popup = `<div class="popup"><h3>💬 ${con ? "🟢 Vecino: ya llegó la corriente" : "🔴 Vecino: sin corriente"}</h3>
           <p>${esc(c.lugar)}${horas}</p>
           <p class="hora">Reporte de un vecino en los comentarios del canal
           (${horaHabana(c.fecha)}). No verificado oficialmente.</p></div>`;
      if (con) {
        L.circleMarker([c.lat, c.lon], {
          radius: 6, weight: 1.5, color: "#2f7d3a", fillColor: "#7fd08c", fillOpacity: 0.8,
        }).bindTooltip(`💬 ya hay luz: ${esc(c.lugar)}`).bindPopup(popup, { maxWidth: 300 }).addTo(capaComentarios);
      } else {
        L.circle([c.lat, c.lon], {
          radius: 350, weight: 2, color: "#8b0000", fillColor: "#e5484d", fillOpacity: 0.2, dashArray: "4",
        }).bindTooltip(`💬 vecino sin luz: ${esc(c.lugar)}`).bindPopup(popup, { maxWidth: 300 }).addTo(capaComentarios);
      }
    }
  }
  poblarDinamicas();

  // Enfoque directo: index.html?c=CODIGO (desde la pestaña Circuitos) centra el
  // mapa en ese circuito y abre su popup.
  {
    const codFoco = (new URLSearchParams(location.search).get("c") || "").toUpperCase();
    const capa = codFoco && circuitoCapas[codFoco];
    if (capa) {
      mapa.setView(capa.getBounds ? capa.getBounds().getCenter() : capa.getLatLng(), 15);
      setTimeout(() => capa.openPopup(), 300);
    }
  }

  // Espacio negativo: celdas lejos de toda zona de bloque y de circuitos DAF
  // -> "probablemente no rota" (derivado por exclusión, no oficial). [latente]
  if (MOSTRAR_PROTEGIDAS) for (const f of noRota.features) {
    L.polygon(
      f.geometry.coordinates[0].map(([lon, lat]) => [lat, lon]),
      { stroke: false, fillColor: "#4a90d9", fillOpacity: 0.28, interactive: false }
    ).addTo(capas.candidata_protegida);
  }

  // El control se crea una vez; las capas dinámicas (circuitos, averías, etc.)
  // siempre están. Las capas de BLOQUE solo si MOSTRAR_BLOQUES (hoy latentes).
  const capasControl = {
    "🔴 Circuitos afectados (déficit)": capaCircuitos,
    "⚠️ Cortes de emergencia": capaEmergencia,
    "🟣 Averías/roturas": capaAverias,
    "✅ Circuitos con servicio (restablecidos)": capaParciales,
    "💬 Reportes de vecinos (comentarios)": capaComentarios,
  };
  if (MOSTRAR_PROTEGIDAS) {
    capas.daf.addTo(mapa);
    capas.candidata_protegida.addTo(mapa);
    capasControl["🟡 Circuitos DAF (microcortes)"] = capas.daf;
    capasControl["🔵 Posibles zonas sin apagón"] = capas.candidata_protegida;
  }
  L.control
    .layers(null, capasControl, { collapsed: window.innerWidth < 640 })
    .addTo(mapa);

  // --- Reportes vecinales: naranja = posible, rojo intenso = confirmado (>=10 IPs) ---
  const capaReportes = L.layerGroup().addTo(mapa);
  async function cargarReportes() {
    try {
      const r = await fetch(`${API_BASE}/api/reportes`).then((x) => x.json());
      capaReportes.clearLayers();
      for (const p of r.puntos || []) {
        const esCon = p.tipo === "con";
        // ocultar "hay luz" anteriores al apagón vigente (salvo confirmados)
        if (esCon && reporteConObsoleto(p.fecha, p.lat, p.lon, p.confirmado)) continue;
        const colores = esCon
          ? { borde: p.confirmado ? "#1c5f2b" : "#4f9e60", relleno: p.confirmado ? "#46a758" : "#8fd39b" }
          : { borde: p.confirmado ? "#8b0000" : "#e07b00", relleno: p.confirmado ? "#e5484d" : "#ffa733" };
        const titulo = esCon
          ? (p.confirmado ? "🟢 Corriente confirmada por vecinos" : "🌿 Posible regreso de la corriente")
          : (p.confirmado ? "🔴 Afectación confirmada" : "🟠 Posible afectación");
        const detalle = `${p.reportes} vecino(s) reportaron ${esCon ? "que ya hay" : "estar sin"} corriente en las últimas ${r.ventana_h}h` +
          (p.sin && p.con ? ` (${p.sin} sin · ${p.con} con)` : "") + ".";
        L.circleMarker([p.lat, p.lon], {
          radius: p.confirmado ? 10 : 7, weight: 2,
          color: colores.borde, fillColor: colores.relleno, fillOpacity: 0.85,
        })
          .bindPopup(
            `<div class="popup"><h3>${titulo}</h3>
             <p>${esc(p.direccion) || "Reporte vecinal"}</p>
             <p class="hora">${detalle} ${p.confirmado ? "" : `Se confirma con ${r.umbral} reportes.`}</p></div>`
          )
          .addTo(capaReportes);
      }
    } catch (e) { /* API no disponible (p.ej. en github.io sin worker) */ }
  }
  cargarReportes();
  setInterval(cargarReportes, 120000);

  // --- Auto-refresco de datos SIN recargar la página ni los recursos ---
  // Cada 90 s re-baja solo estado.json (con anti-caché) y re-pinta tarjetas,
  // banner y capas dinámicas. No se recrea el mapa: conserva tu zoom/posición
  // y no vuelve a bajar tiles, JS ni CSS.
  async function refrescar() {
    try {
      const [nuevo, cat] = await Promise.all([
        fetch(`data/estado.json?t=${Date.now()}`).then((r) => r.json()),
        fetch(`data/circuitos.json?t=${Date.now()}`).then((r) => r.json()).catch(() => catCircuitos),
      ]);
      estado = nuevo;
      catCircuitos = cat || catCircuitos;
      document.getElementById("actualizado").textContent =
        `actualizado ${horaHabana(estado.generado)} (hora de La Habana)`;
      avisoNacional(estado.evento_nacional);
      avisoDeficit(estado.deficit);
      resumenCircuitos();
      poblarDinamicas();
    } catch (e) { /* red intermitente: se reintenta al siguiente ciclo */ }
  }
  setInterval(refrescar, 90000);

  // Diagnóstico de una dirección buscada: no solo el bloque, también QUÉ tipo de
  // afectación tiene (avería/interrupción, corte de emergencia, bloque apagado,
  // microcortes DAF, o con corriente). Se decide de lo más específico a lo más
  // general y se añaden notas secundarias (avería cercana, DAF) cuando aplican.
  function diagnostico(lat, lon) {
    const dist = (la, lo) => Math.hypot((la - lat) * 111000, (lo - lon) * 102000);

    // ¿Dentro de un circuito DAF? (microcortes, no rota en los bloques)
    let enDaf = false;
    for (const b of barrios) {
      if (b.cat !== "daf") continue;
      const lim = polyDe(b.nombre);
      if (lim ? dentroPoly(lat, lon, lim.anillo) : dist(b.lat, b.lon) < 400) { enDaf = true; break; }
    }
    // Avería/rotura más cercana (interrupción puntual, no rotación)
    let av = null, avD = 500;
    for (const a of estado.averias.items) {
      if (a.lat === undefined) continue;
      const d = dist(a.lat, a.lon);
      if (d < avD) { avD = d; av = a; }
    }
    // ¿Dentro/junto a un corte de emergencia activo?
    let emg = null;
    for (const z of estado.emergencia.items) {
      if (z.lat === undefined || z.restablecido) continue;
      const lim = polyDe(z.nombre);
      if (lim ? dentroPoly(lat, lon, lim.anillo) : dist(z.lat, z.lon) < 450) { emg = z; break; }
    }
    // ¿Circuito con servicio dentro de un bloque afectado (restablecimiento parcial)?
    let par = null, parD = 350;
    for (const c of estado.parciales || []) {
      const d = dist(c.lat, c.lon);
      if (d < parD && (estado.bloques[c.bloque] || {}).estado === "afectado") { parD = d; par = c; }
    }
    // Bloque más cercano
    let mejor = null, dMin = Infinity;
    for (const [la, lo, b] of muestraBloques) {
      const d = dist(la, lo);
      if (d < dMin) { dMin = d; mejor = b; }
    }
    const e = mejor !== null ? (estado.bloques[mejor] || { estado: "desconocido" }) : { estado: "desconocido" };

    const notaDaf = enDaf
      ? `<p class="hora">🟡 <b>Circuito DAF</b>: no rota en los bloques, pero sufre microcortes por Disparo Automático de Frecuencia.</p>`
      : "";
    const notaAv = av
      ? `<p class="hora">🚧 <b>Avería</b> a ~${Math.round(avD)} m (${esc(av.tipo)})${av.direccion ? " — " + esc(av.direccion) : ""}: interrupción por rotura, no rotación del bloque. Parte de las ${horaHabana(estado.averias.fecha)}.</p>`
      : "";

    if (emg) {
      return `<p class="hora">⚠️ <b>Corte de emergencia</b> — ${esc(emg.nombre)}. Circuito fuera de la rotación
        de bloques, afectado por emergencia en la generación. Aviso de las ${horaHabana(estado.emergencia.fecha)}.</p>${notaAv}${notaDaf}`;
    }
    if (av && avD < 250) {  // avería prácticamente encima: es la causa
      return `${notaAv}${notaDaf}`;
    }
    if (par) {
      return `<p class="hora">🟢 <b>Con corriente</b>: aunque el <b>B${par.bloque}</b> sigue apagado, la Empresa
        reportó este circuito restablecido a las ${horaHabana(par.fecha)}. Puede volver a cortarse en la próxima rotación.</p>${notaAv}${notaDaf}`;
    }
    if (mejor !== null && dMin <= 350) {
      const cab = e.estado === "afectado"
        ? `🔴 <b>Sin corriente</b> — Bloque B${mejor} apagado${e.desde ? " (lleva " + duracion(e.desde) + ")" : ""}.`
        : e.estado === "restablecido"
          ? `🟢 <b>Con corriente</b> — zona del Bloque B${mejor} con servicio${e.desde ? " desde " + horaHabana(e.desde) : ""}.`
          : `⚪ Zona del Bloque B${mejor}: sin datos de estado ahora mismo.`;
      return `<p class="hora">${cab}</p>${notaAv}${notaDaf}`;
    }
    if (mejor !== null && dMin <= 900) {
      const txt = { afectado: "sin corriente", restablecido: "con servicio", desconocido: "sin datos" }[e.estado];
      return `<p class="hora">La zona de bloque más cercana es del <b>B${mejor}</b> (${txt}), a ~${Math.round(dMin)} m.
        Podría rotar con ese bloque o no — sin datos exactos.</p>${notaAv}${notaDaf}`;
    }
    if (notaAv || notaDaf) return `${notaAv}${notaDaf}`;
    return `<p class="hora">✅ Sin registro de afectaciones: no aparece en ningún bloque de apagón,
      así que lo más probable es que esta zona <b>no rote</b> (no se afecta). Salvedad: podría ser
      un vacío de información — si sabes que sí le quitan la corriente, repórtalo.</p>`;
  }

  // --- Buscador de calles y repartos (índice local, sin servicios externos) ---
  let indice = null;
  let marcadorBusqueda = null;
  const cajaResultados = document.getElementById("resultados");
  const entrada = document.getElementById("busqueda");
  const normalizar = (t) =>
    t.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");

  // Botones de reporte: coordenadas y dirección en data-* escapados; el manejo
  // va por delegación (abajo), nunca por onclick interpolado (evita inyección).
  function reportarAqui(lat, lon, nombre) {
    const d = `data-lat="${lat}" data-lon="${lon}" data-dir="${esc(nombre)}"`;
    return `<button class="btn-reporte" ${d} data-tipo="sin">🚨 No tengo corriente aquí</button>
      <button class="btn-reporte con" ${d} data-tipo="con">✅ Ya llegó la corriente</button>`;
  }
  async function enviarReporte(btn) {
    btn.disabled = true;
    btn.textContent = "Enviando…";
    try {
      const r = await fetch(`${API_BASE}/api/reporte`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          lat: +btn.dataset.lat, lon: +btn.dataset.lon,
          direccion: btn.dataset.dir, tipo: btn.dataset.tipo,
        }),
      });
      const d = await r.json();
      btn.textContent = r.ok ? "✔ Reporte enviado, gracias" : `✖ ${d.error}`;
      if (r.ok) cargarReportes();
    } catch {
      btn.textContent = "✖ Sin conexión con el servidor";
    }
  }
  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest(".btn-reporte");
    if (btn) enviarReporte(btn);
  });

  let cargandoIndice = null;  // guard: una sola descarga del índice, no una por tecla
  entrada.addEventListener("input", async () => {
    const q = normalizar(entrada.value.trim());
    if (q.length < 2) { cajaResultados.hidden = true; return; }
    if (!indice) {
      cargandoIndice = cargandoIndice || fetch("data/calles.json").then((r) => r.json());
      indice = await cargandoIndice;
      indice.forEach((e) => e.length < 5 && e.push(normalizar(e[0])));
    }
    const hits = indice.filter((e) => e[4].includes(q)).slice(0, 8);
    cajaResultados.innerHTML = hits
      .map(
        (e) => `<div data-i="${indice.indexOf(e)}">${esc(e[0])}
          <span class="muni">${esc(e[1]) || "reparto"}</span></div>`
      )
      .join("") || "<div>sin resultados</div>";
    cajaResultados.hidden = false;
    for (const div of cajaResultados.querySelectorAll("[data-i]")) {
      div.onclick = () => {
        const [nombre, muni, lat, lon] = indice[+div.dataset.i];
        cajaResultados.hidden = true;
        entrada.value = nombre;
        mapa.flyTo([lat, lon], 16);
        if (marcadorBusqueda) marcadorBusqueda.remove();
        marcadorBusqueda = L.marker([lat, lon], {
          icon: L.divIcon({
            className: "pin-busqueda",
            html: "📍",
            iconSize: [32, 32],
            iconAnchor: [16, 30],   // la punta del pin cae sobre el lugar
            popupAnchor: [0, -28],
          }),
        })
          .bindPopup(
            `<div class="popup"><h3>${esc(nombre)}</h3><p>${esc(muni)}</p>
             ${diagnostico(lat, lon)}
             ${reportarAqui(lat, lon, `${nombre}${muni ? ", " + muni : ""}`)}</div>`
          )
          .addTo(mapa)
          .openPopup();
      };
    }
  });
  document.addEventListener("click", (ev) => {
    if (!ev.target.closest("#buscador")) cajaResultados.hidden = true;
  });

  const leyenda = L.control({ position: "bottomleft" });
  leyenda.onAdd = () => {
    const div = L.DomUtil.create("div", "leyenda");
    div.innerHTML = movil
      ? "🔴 sin luz · 🟢 con luz · ⚪ sin noticias · 🔵 sin apagones<br>🟣 avería · 🟡 DAF · 🟠 reporte"
      : "Circuitos: 🔴 sin corriente · 🟢 con servicio · ⚪ sin noticias hace +24 h · " +
        "🔵 sin apagones reportados (se asume con corriente).<br>" +
        "🟣 averías/roturas · 🟡 circuitos DAF (microcortes).<br>" +
        "🟠 reporte vecinal (se confirma en rojo con 10+ vecinos).<br>" +
        "Ubicaciones aproximadas, datos no oficiales.";
    return div;
  };
  leyenda.addTo(mapa);
}

iniciar().catch((e) => {
  document.getElementById("actualizado").textContent = "error cargando datos";
  console.error(e);
});
