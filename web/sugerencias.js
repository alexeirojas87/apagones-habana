// El API vive en el worker de Cloudflare Pages (mismo origen en pages.dev).

// Icono del sprite externo (R8/D4): decorativo (aria-hidden); el texto
// adyacente da el nombre.
const icono = (nombre, clase) =>
  '<svg class="ico' + (clase ? " " + clase : "") + '" aria-hidden="true">' +
  '<use href="/icons.svg#icon-' + nombre + '"></use></svg>';

const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const form = document.getElementById("sug-form");
const btn = document.getElementById("sug-enviar");
const estado = document.getElementById("sug-estado");
const resumen = document.getElementById("sug-resumen");

// Validación en línea por campo (R11/D7): cada campo con su hueco de error.
const CAMPOS = ["tipo", "titulo", "detalle"];
const campoDe = (nombre) => document.getElementById("sug-" + nombre);
const errorDe = (nombre) => document.getElementById("sug-error-" + nombre);

function ponerError(nombre, mensaje) {
  const campo = campoDe(nombre);
  campo.setAttribute("aria-invalid", "true");
  campo.setAttribute("aria-describedby", "sug-error-" + nombre);
  const caja = errorDe(nombre);
  caja.textContent = mensaje;
  caja.hidden = false;
}

function limpiarErrores() {
  for (const nombre of CAMPOS) {
    campoDe(nombre).removeAttribute("aria-invalid");
    const caja = errorDe(nombre);
    caja.textContent = "";
    caja.hidden = true;
  }
  resumen.hidden = true;
  resumen.innerHTML = "";
}

form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  limpiarErrores();
  const tipo = campoDe("tipo").value;
  const titulo = campoDe("titulo").value.trim();
  const detalle = campoDe("detalle").value.trim();
  const fallos = [];
  if (titulo.length < 5) {
    const mensaje = "Escribe un título un poco más descriptivo (mínimo 5 caracteres).";
    ponerError("titulo", mensaje);
    fallos.push({ nombre: "titulo", mensaje });
  }
  if (fallos.length) {
    resumen.innerHTML =
      "<p>Revisa estos campos:</p><ul>" +
      fallos.map((f) => `<li><a href="#sug-${f.nombre}">${esc(f.mensaje)}</a></li>`).join("") +
      "</ul>";
    resumen.hidden = false;
    resumen.focus();
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
      // Éxito (D7): la caja sustituye al formulario
      form.hidden = true;
      resumen.hidden = true;
      document.getElementById("sug-exito").hidden = false;
    } else {
      estado.className = "sug-estado err";
      estado.innerHTML = icono("x", "est-sin") + " " + esc(d.error || "No se pudo enviar.");
    }
  } catch {
    estado.className = "sug-estado err";
    estado.innerHTML = icono("x", "est-sin") + " Sin conexión con el servidor. Inténtalo más tarde.";
  } finally {
    btn.disabled = false;
    btn.textContent = antes;
  }
});
