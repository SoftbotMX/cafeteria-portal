// Pedido (5g/5t): semanas con flechas, una categoría a la vez y los +/−. Todo vive en un solo
// formulario: sin JS se ve completo y funciona igual. HTMX sólo recalcula el total.
(function () {
  "use strict";
  var f = document.getElementById("pedido");
  if (!f) return;
  document.documentElement.classList.add("js");

  // ---- semanas: se ve una, la del día elegido
  var semanas = Array.prototype.slice.call(f.querySelectorAll(".semana"));
  var actual = parseInt(f.querySelector(".semanas").getAttribute("data-actual"), 10) || 0;
  function verSemana(i) {
    actual = Math.max(0, Math.min(semanas.length - 1, i));
    semanas.forEach(function (s, k) {
      s.hidden = k !== actual;
      s.querySelectorAll("[data-ir]").forEach(function (b) {
        b.disabled = b.getAttribute("data-ir") === "-1" ? k === 0 : k === semanas.length - 1;
      });
    });
  }
  verSemana(actual);

  // ---- vistas: categorías (#categorias, #dia o nada) o una categoría (#cat-N)
  var paneles = Array.prototype.slice.call(f.querySelectorAll(".panel"));
  var desdeCategorias = false;
  function vista() {
    var m = /^#cat-\d+$/.test(location.hash) ? location.hash.slice(1) : null;
    // Una categoría que ya no existe (el menú cambió y se recargó #cat-N): a la lista.
    if (m && !document.getElementById(m)) m = null;
    f.setAttribute("data-vista", m ? "productos" : "categorias");
    paneles.forEach(function (p) { p.hidden = p.id !== m; });
    if (location.hash === "#dia") {
      document.getElementById("dia").scrollIntoView();
    } else {
      window.scrollTo(0, 0);
    }
  }
  window.addEventListener("hashchange", function (e) {
    desdeCategorias = /^#cat-\d+$/.test(location.hash) && !/#cat-\d+$/.test(e.oldURL);
    vista();
  });
  vista();

  // ---- cuántas piezas lleva en total y por categoría
  function cuentas() {
    var total = 0;
    paneles.forEach(function (p) {
      var n = 0;
      p.querySelectorAll("input[type=number]").forEach(function (c) {
        var v = parseInt(c.value, 10);
        v = isNaN(v) || v < 0 ? 0 : v;
        c.classList.toggle("cero", v === 0);
        n += v;
      });
      total += n;
      var ins = f.querySelector('[data-insignia="' + p.id + '"]');
      if (ins) { ins.hidden = n === 0; ins.textContent = n + " en tu pedido"; }
    });
    var c = f.querySelector("[data-cuenta]");
    if (c) c.textContent = total + (total === 1 ? " producto" : " productos");
  }
  cuentas();

  f.addEventListener("click", function (e) {
    var b = e.target.closest("[data-ir]");
    if (b) { verSemana(actual + parseInt(b.getAttribute("data-ir"), 10)); return; }
    var p = e.target.closest("[data-paso]");
    if (p) {
      var campo = p.parentNode.querySelector("input");
      if (p.getAttribute("data-paso") === "1") { campo.stepUp(); } else { campo.stepDown(); }
      campo.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }
    // «‹ Categorías»: si se llegó desde la lista, regresar en el historial (no apilar entradas).
    if (e.target.closest("[data-a-categorias]") && desdeCategorias) {
      e.preventDefault();
      history.back();
    }
  });
  // Una cantidad no válida (p. ej. 21) en una categoría oculta bloquearía «Listo» sin que el
  // navegador pueda señalarla: se abre esa categoría y se muestra el aviso en el campo.
  // Sólo el PRIMER campo inválido de cada intento (con dos categorías inválidas, cada una abriría
  // la suya y se pisarían): la categoría se muestra al momento y una bandera evita volver a entrar.
  var revelando = false;
  f.addEventListener("invalid", function (e) {
    var p = e.target.closest(".panel");
    if (revelando || !p || !p.hidden) return;
    revelando = true;
    location.hash = p.id;  // una sola entrada en el historial
    f.setAttribute("data-vista", "productos");
    paneles.forEach(function (x) { x.hidden = x !== p; });
    setTimeout(function () {
      e.target.focus();
      e.target.reportValidity();
      revelando = false;
    }, 0);
  }, true);
  f.addEventListener("input", cuentas);
  f.addEventListener("change", function (e) {
    cuentas();
    if (e.target.name === "fecha") {
      var larga = e.target.getAttribute("data-larga");
      f.querySelectorAll("[data-entrega]").forEach(function (x) { x.textContent = larga; });
    }
  });
})();
