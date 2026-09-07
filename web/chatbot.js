(function () {
  'use strict';

  if (document.getElementById("chatbot-widget")) return;

  const API = location.hostname.endsWith("pages.dev") ? "" : "https://apagones-habana.pages.dev";

  var css = document.createElement("style");
  // Paleta en tokens compartidos (D9): el widget sigue el tema claro/oscuro
  // del sitio con el mismo contrato de tokens de style.css.
  css.textContent = `
    #chatbot-widget { position: fixed; bottom: 20px; right: 20px; z-index: 9999; font-family: var(--fuente-sans); }
    #chatbot-toggle { width: 56px; height: 56px; border-radius: 50%; border: none; background: var(--cta); color: var(--on-cta); font-size: 24px; cursor: pointer; box-shadow: 0 4px 12px rgba(0,0,0,0.25); display: flex; align-items: center; justify-content: center; transition: transform .2s; }
    #chatbot-toggle:hover { transform: scale(1.1); }
    #chatbot-panel { position: fixed; bottom: 90px; right: 20px; width: 360px; max-height: 520px; background: var(--surface); border-radius: 16px; box-shadow: 0 8px 32px rgba(0,0,0,0.4); display: none; flex-direction: column; overflow: hidden; color: var(--text); }
    #chatbot-panel.abierto { display: flex; }
    #chatbot-header { padding: 14px 16px; background: var(--cta); color: var(--on-cta); font-weight: 600; font-size: 14px; display: flex; justify-content: space-between; align-items: center; }
    #chatbot-header small { font-weight: 400; opacity: .8; }
    #chatbot-close { background: none; border: none; color: var(--text-strong); font-size: 18px; cursor: pointer; padding: 0 4px; }
    #chatbot-mensajes { flex: 1; overflow-y: auto; padding: 12px 16px; display: flex; flex-direction: column; gap: 8px; font-size: 13px; line-height: 1.5; }
    .cb-msg { max-width: 85%; padding: 8px 12px; border-radius: 12px; word-wrap: break-word; }
    .cb-usr { background: var(--cta); color: var(--text-strong); align-self: flex-end; border-bottom-right-radius: 4px; }
    .cb-bot { background: var(--surface-2); color: var(--text); align-self: flex-start; border-bottom-left-radius: 4px; }
    .cb-bot a { color: var(--accent); }
    .cb-bot.loading { opacity: .6; }
    .cb-error { background: var(--red-bg); color: var(--red-t); align-self: flex-start; border-bottom-left-radius: 4px; }
    #chatbot-input { display: flex; padding: 10px 12px; gap: 8px; border-top: 1px solid var(--border); background: var(--surface); }
    #chatbot-input input { flex: 1; border: 1px solid var(--border); border-radius: 20px; padding: 8px 14px; font-size: 13px; background: var(--surface-2); color: var(--text); outline: none; }
    #chatbot-input input:focus { border-color: var(--cta); }
    #chatbot-input input::placeholder { color: var(--text-dim); }
    #chatbot-input button { border: none; background: var(--cta); color: var(--on-cta); border-radius: 20px; padding: 8px 16px; font-size: 13px; cursor: pointer; }
    #chatbot-input button:disabled { opacity: .5; cursor: default; }
    #chatbot-sugerencias { padding: 8px 12px; display: flex; gap: 6px; flex-wrap: wrap; border-top: 1px solid var(--border); }
    .cb-sug { background: var(--surface-2); border: 1px solid var(--border-2); border-radius: 14px; padding: 4px 10px; font-size: 11px; color: var(--text-muted); cursor: pointer; }
    .cb-sug:hover { background: var(--border); }
    .cb-card { background: var(--surface-2); border: 1px solid var(--border); border-radius: 12px; padding: 10px 12px; align-self: flex-start; max-width: 85%; display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
    .cb-card-t { font-weight: 600; color: var(--text); }
    .cb-card-l { color: var(--text); }
    .cb-card-c { color: var(--text-muted); font-size: 11px; }
    .cb-card-b { display: flex; gap: 8px; }
    .cb-card button { flex: 1; border: none; border-radius: 14px; padding: 6px 10px; font-size: 12px; cursor: pointer; }
    .cb-ok { background: var(--cta); color: var(--on-cta); }
    .cb-no { background: var(--surface); color: var(--accent); border: 1px solid var(--border-2); }
    .cb-card button:disabled { opacity: .5; cursor: default; }
    .cb-card-ok { color: var(--accent); }
    .cb-card-err { background: var(--red-bg); color: var(--red-t); }
    @media (max-width: 480px) { #chatbot-panel { right: 8px; bottom: 80px; width: calc(100vw - 16px); max-height: 70vh; } }
  `;
  document.head.appendChild(css);

  var html =
    '<div id="chatbot-widget">' +
      '<button id="chatbot-toggle" title="Pregunta sobre el estado eléctrico"><svg class="ico" aria-hidden="true"><use href="/icons.svg#icon-chat"></use></svg></button>' +
      '<div id="chatbot-panel">' +
        '<div id="chatbot-header"><svg class="ico" aria-hidden="true"><use href="/icons.svg#icon-bot"></use></svg> Apagones Bot <small>datos en vivo + histórico</small><button id="chatbot-close" aria-label="Cerrar el chatbot"><svg class="ico" aria-hidden="true"><use href="/icons.svg#icon-x"></use></svg></button></div>' +
        '<div id="chatbot-mensajes">' +
          '<div class="cb-msg cb-bot">Hola! Pregúntame sobre el estado eléctrico de La Habana. Ej: "¿qué pasa en Marianao?" o "¿cuándo quitan la corriente en 23 y 12?"</div>' +
        '</div>' +
        // La Empresa dejó de reportar por bloque y ahora informa por circuito
        // (ver aviso en app.js): las sugerencias apuntan a circuito/calle.
        '<div id="chatbot-sugerencias">' +
          '<span class="cb-sug">¿qué pasa en Marianao?</span>' +
          '<span class="cb-sug">¿qué circuitos están afectados ahora?</span>' +
          '<span class="cb-sug">¿cuáles son los peores circuitos del mes?</span>' +
        '</div>' +
        '<div id="chatbot-input">' +
          '<input id="cb-inp" type="text" placeholder="Escribe tu pregunta…" autocomplete="off">' +
          '<button id="cb-enviar">Enviar</button>' +
        '</div>' +
      '</div>' +
    '</div>';

  document.body.insertAdjacentHTML("beforeend", html);

  var panel = document.getElementById("chatbot-panel");
  var toggle = document.getElementById("chatbot-toggle");
  var close = document.getElementById("chatbot-close");

  toggle.onclick = function () { panel.classList.toggle("abierto"); };
  close.onclick = function () { panel.classList.remove("abierto"); };

  var inp = document.getElementById("cb-inp");
  var btn = document.getElementById("cb-enviar");
  var msgs = document.getElementById("chatbot-mensajes");
  var historial = [];

  function agregar(texto, clase) {
    var d = document.createElement("div");
    d.className = "cb-msg " + clase;
    d.innerHTML = texto;
    msgs.appendChild(d);
    msgs.scrollTop = msgs.scrollHeight;
    return d;
  }

  // Tarjeta de confirmación del reporte pendiente (D3): los datos vienen del
  // resultado determinista de la herramienta reportar (no del texto del LLM).
  // Todos los valores dinámicos pasan por esc(); el envío real es el POST a
  // /api/reporte al confirmar — Cancelar no hace ninguna petición.
  function tarjetaReporte(p) {
    var card = document.createElement("div");
    card.className = "cb-card";
    card.innerHTML =
      '<div class="cb-card-t">' + (p.tipo === "con" ? "¿Ya volvió la corriente?" : "¿Se fue la luz?") + "</div>" +
      '<div class="cb-card-l"><strong>' + esc(p.codigo) + "</strong> · " + esc(p.direccion) + "</div>" +
      '<div class="cb-card-c">confianza ' + esc(p.confianza) + "</div>" +
      '<div class="cb-card-b">' +
      '<button class="cb-ok" data-lat="' + esc(p.lat) + '" data-lon="' + esc(p.lon) +
        '" data-dir="' + esc(p.direccion) + '" data-tipo="' + esc(p.tipo) +
        '" data-cod="' + esc(p.codigo) + '">Confirmar reporte</button>' +
      '<button class="cb-no">Cancelar</button>' +
      "</div>";
    return card;
  }

  // Un solo listener delegado para todas las tarjetas (nada de onclick
  // interpolado). dataset.done hace la confirmación de un solo uso y ambos
  // botones quedan deshabilitados tras el primer clic.
  msgs.addEventListener("click", function (e) {
    var no = e.target.closest(".cb-no");
    if (no) {
      var suTarjeta = no.closest(".cb-card");
      if (suTarjeta) suTarjeta.remove();
      return;  // cancelar: sin petición
    }
    var boton = e.target.closest(".cb-ok");
    if (!boton || boton.dataset.done) return;
    boton.dataset.done = "1";
    var card = boton.closest(".cb-card");
    card.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
    fetch(API + "/api/reporte", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ lat: +boton.dataset.lat, lon: +boton.dataset.lon,
                             direccion: boton.dataset.dir, tipo: boton.dataset.tipo,
                             codigo: boton.dataset.cod }),
    }).then(function (r) {
      return r.json().then(function (d) { return { ok: r.ok, d: d }; });
    }).then(function (res) {
      var d = res.d;
      if (res.ok) {
        card.className = "cb-card cb-card-ok";
        card.innerHTML = "tu reporte quedó registrado (circuito " + esc(boton.dataset.cod) + ")";
      } else {
        card.className = "cb-card cb-card-err";
        card.innerHTML = esc(d && d.error) || "no se pudo registrar el reporte";
      }
      msgs.scrollTop = msgs.scrollHeight;
    }).catch(function () {
      card.className = "cb-card cb-card-err";
      card.innerHTML = "Error de conexión. Intenta de nuevo.";
    });
  });

  function enviar() {
    var q = inp.value.trim();
    if (!q) return;
    inp.value = "";
    agregar(esc(q), "cb-usr");
    historial.push({ role: "user", content: q });
    var loading = agregar("Pensando…", "cb-bot loading");
    btn.disabled = true;
    fetch(API + "/api/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ consulta: q, historial: historial.slice(0, -1) }),
    }).then(function (r) { return r.json(); }).then(function (d) {
      loading.remove();
      if (d.respuesta) {
        agregar(esc(d.respuesta).replace(/\n/g, "<br>"), "cb-bot");
        historial.push({ role: "assistant", content: d.respuesta });
        if (d.reporte_pendiente) {
          msgs.appendChild(tarjetaReporte(d.reporte_pendiente));
          msgs.scrollTop = msgs.scrollHeight;
        }
      } else agregar("Lo siento, no pude procesar la consulta.", "cb-error");
    }).catch(function () {
      loading.remove();
      agregar("Error de conexión. Intenta de nuevo.", "cb-error");
    }).finally(function () { btn.disabled = false; });
  }

  btn.onclick = enviar;
  inp.onkeydown = function (e) { if (e.key === "Enter") enviar(); };

  document.querySelectorAll(".cb-sug").forEach(function (el) {
    el.onclick = function () { inp.value = el.textContent; enviar(); };
  });

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
    });
  }
})();