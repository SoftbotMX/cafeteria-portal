// Confirmaciones de la administración con el estilo del portal (no el confirm() del navegador).
// Un formulario las pide con data-confirmar="¿Pregunta?" y, opcionales, data-confirmar-detalle,
// data-confirmar-boton (texto del botón) y data-confirmar-peligro (botón rojo). Sin JavaScript el
// formulario se envía directo: la confirmación sólo evita un toque por error.
(function () {
  "use strict";
  var d = document.getElementById("confirmar");
  if (!d || typeof d.showModal !== "function") return;
  var pendiente = null;
  var titulo = d.querySelector("[data-titulo]");
  var detalle = d.querySelector("[data-detalle]");
  var si = d.querySelector("[data-si]");

  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (!f.matches || !f.matches("form[data-confirmar]")) return;
    if (f.dataset.enviando === "1") { e.preventDefault(); return; }  // ya va en camino: no se repite
    if (f.dataset.confirmado === "1") {  // el paso único que deja pasar el envío confirmado
      delete f.dataset.confirmado;
      f.dataset.enviando = "1";
      f.querySelectorAll("button[type=submit], button:not([type])").forEach(function (b) {
        if (!b.disabled) { b.disabled = true; b.dataset.deshabilitado = "1"; }
      });
      return;
    }
    e.preventDefault();
    pendiente = f;
    titulo.textContent = f.dataset.confirmar;
    detalle.textContent = f.dataset.confirmarDetalle || "";
    detalle.hidden = !f.dataset.confirmarDetalle;
    si.textContent = f.dataset.confirmarBoton || "Confirmar";
    si.classList.toggle("peligro", "confirmarPeligro" in f.dataset);
    d.returnValue = "";  // Escape no lo cambia: sin esto repetiría la respuesta anterior
    d.showModal();
    si.focus();
  });

  d.addEventListener("close", function () {
    var f = pendiente;
    pendiente = null;
    if (f && d.returnValue === "si") {
      f.dataset.confirmado = "1";
      if (f.requestSubmit) f.requestSubmit(); else f.submit();
    }
  });

  // Al volver con «Atrás» el navegador puede restaurar la página tal cual: se desbloquea.
  // Sólo los que este script deshabilitó (marcados con data-enviando), no los que ya venían así.
  window.addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    document.querySelectorAll("form[data-enviando]").forEach(function (f) {
      delete f.dataset.enviando;
      delete f.dataset.confirmado;
      f.querySelectorAll("button[data-deshabilitado]").forEach(function (b) {
        b.disabled = false;
        delete b.dataset.deshabilitado;
      });
    });
  });

  // Tocar fuera del recuadro cancela.
  d.addEventListener("click", function (e) {
    if (e.target === d) d.close("no");
  });
})();
