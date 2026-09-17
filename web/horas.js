// horas.js — selector de rango para "Circuitos más afectados" (páginas de
// municipio). Lee el JSON embebido #datos-horas-circuitos (SOLO circuitos del
// municipio, escrito por build_seo.py) y re-ranquea <ol id="afect-ranking"
// class="afect-ranking"> al cambiar de píldora: suma las horas por día del
// rango (7/30/90 días calendario en HORA_CUBA, UTC-4 fijo) o muestra el
// histórico completo. Determinismo: el fin de la ventana es el `generado` del
// JSON, NUNCA el reloj del navegador — mismos datos, mismo orden, aunque este
// JS venga de caché. Sin dependencias; si falta el JSON o la lista, no hace
// nada.
//
// Estructura testable (R3-002): las funciones PURAS (parseIso, diaHabana,
// restarDias, horasEnRango, formatoHoras, ranquear) viven arriba y reciben los
// datos por parámetro — con module.exports se prueban con node sin DOM — y la
// parte DOM queda encapsulada bajo la guarda `typeof document`. El selector se
// busca doble: #afect-ranking (id que emite build_seo.py) o, de respaldo,
// cualquier ol.afect-ranking (la clase).
(function () {
  "use strict";

  // Parseo UTC explícito: los ISO de los datos traen offset; uno naive se
  // interpreta como UTC (igual que el builder en Python) — nunca la hora local
  // del visitante, que rompería el determinismo del orden.
  function parseIso(iso) {
    if (typeof iso !== "string") return NaN;
    var s = /[zZ]$|[+-][0-9]{2}:?[0-9]{2}$/.test(iso) ? iso : iso + "Z";
    return Date.parse(s);
  }

  // Día calendario YYYY-MM-DD en La Habana (UTC-4 fijo): el builder reparte
  // las horas por ese huso, así que la ventana se corta en el mismo.
  function diaHabana(iso) {
    var t = parseIso(iso);
    return isNaN(t) ? null : new Date(t - 4 * 3600000).toISOString().slice(0, 10);
  }

  function restarDias(dia, n) {
    return new Date(Date.parse(dia + "T00:00:00Z") - n * 86400000)
      .toISOString().slice(0, 10);
  }

  // Horas del circuito `cod` en los N últimos días calendario de `datos`
  // (incluido el día del `generado`: el borde cuenta) o el histórico completo
  // (`total`, acumulado del build) cuando `dias` es null.
  function horasEnRango(datos, cod, dias) {
    if (dias == null) return (datos.total || {})[cod] || 0;
    var generado = diaHabana(datos.generado);
    if (!generado) return (datos.total || {})[cod] || 0;  // sin ventana: todo
    var desde = restarDias(generado, dias - 1);
    var suma = 0;
    var porDia = (datos.por_dia || {})[cod] || {};
    for (var dia in porDia) {
      if (dia >= desde) suma += porDia[dia];  // ISO YYYY-MM-DD: compara léxico
    }
    return suma;
  }

  // Misma regla que _formato_horas en build_seo.py: "5.3 h" bajo 48 h;
  // a partir de ahí "2 d 4 h" (días completos + horas enteras).
  function formatoHoras(h) {
    if (h >= 48) {
      var dias = Math.floor(h / 24);
      var resto = Math.round(h - dias * 24);
      if (resto >= 24) { dias += 1; resto = 0; }
      return dias + " d " + resto + " h";
    }
    return (Math.round(h * 10) / 10).toFixed(1) + " h";
  }

  // Ranking puro del rango: pares [codigo, horas] con horas > 0, ordenados
  // horas desc con desempate por veces desc y código asc (igual que el server).
  function ranquear(datos, rango) {
    var dias = rango === "todo" ? null : parseInt(rango, 10);
    var veces = datos.veces || {};
    var conHoras = [];
    for (var cod in datos.por_dia) {
      var h = horasEnRango(datos, cod, dias);
      if (h > 0) conHoras.push([cod, h]);
    }
    conHoras.sort(function (x, y) {
      return (y[1] - x[1]) ||
             ((veces[y[0]] || 0) - (veces[x[0]] || 0)) ||
             (x[0] < y[0] ? -1 : x[0] > y[0] ? 1 : 0);
    });
    return conHoras;
  }

  // Export para pruebas con node (sin DOM): solo lo puro, sin reloj.
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { parseIso: parseIso, diaHabana: diaHabana,
                       restarDias: restarDias, horasEnRango: horasEnRango,
                       formatoHoras: formatoHoras, ranquear: ranquear };
  }

  if (typeof document === "undefined") return;  // fuera del navegador: nada de DOM

  var nodo = document.getElementById("datos-horas-circuitos");
  // Defensa doble (bug del selector: build_seo emite id + clase; si el id
  // cambia de forma, la clase aún encuentra la lista).
  var lista = document.getElementById("afect-ranking") ||
              document.querySelector("ol.afect-ranking");
  if (!lista || !nodo) return;
  var datos;
  try {
    datos = JSON.parse(nodo.textContent);
  } catch (e) {
    return;
  }
  if (!datos || !datos.por_dia) return;

  var veces = datos.veces || {};

  function textoPartes(n) {
    return n === 1 ? "1 parte" : n + " partes";
  }

  function fila(cod, horas) {
    var li = document.createElement("li");
    var a = document.createElement("a");
    a.className = "circ-cod";
    a.href = "/circuitos?c=" + encodeURIComponent(cod);
    a.textContent = cod;
    var h = document.createElement("span");
    h.className = "afect-h";
    h.textContent = formatoHoras(horas) + " sin corriente";
    var p = document.createElement("span");
    p.className = "afect-p";
    p.textContent = textoPartes(veces[cod] || 0);
    li.appendChild(a);
    li.appendChild(h);
    li.appendChild(p);
    return li;
  }

  function render(rango) {
    var conHoras = ranquear(datos, rango);
    lista.textContent = "";
    if (!conHoras.length) {
      var vacio = document.createElement("li");
      vacio.className = "afect-vacio";
      vacio.textContent = "Sin datos históricos de horas todavía.";
      lista.appendChild(vacio);
      return;
    }
    conHoras.slice(0, 10).forEach(function (par) {
      lista.appendChild(fila(par[0], par[1]));
    });
  }

  var pildoras = document.querySelectorAll(".afect-rango");
  Array.prototype.forEach.call(pildoras, function (boton) {
    boton.addEventListener("click", function () {
      Array.prototype.forEach.call(pildoras, function (otra) {
        otra.classList.toggle("activo", otra === boton);
        if (otra === boton) {
          otra.setAttribute("aria-pressed", "true");
        } else {
          otra.removeAttribute("aria-pressed");
        }
      });
      render(boton.getAttribute("data-rango"));
    });
  });
})();
