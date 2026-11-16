// Alta (4c): agregar hijos uno por uno. Cada hijo agregado queda en la lista con sus cuatro campos
// ocultos; el formulario se envía igual que siempre. Sólo orienta: el servidor valida todo.
(function () {
  "use strict";
  var f = document.getElementById("alta");
  if (!f) return;
  document.documentElement.classList.add("js");
  var lista = f.querySelector("[data-lista]");
  var nuevo = f.querySelector("[data-nuevo]");
  var agregar = f.querySelector("[data-agregar]");
  var error = f.querySelector("[data-error-hijo]");
  var maximo = parseInt(f.getAttribute("data-max"), 10) || 10;
  var CAMPOS = ["hijo_nombres", "hijo_paterno", "hijo_materno", "hijo_matricula"];

  function campo(nombre) { return nuevo.querySelector('[name="' + nombre + '"]'); }

  function resumen() {
    var val = function (n) { var c = f.querySelector('[name="' + n + '"]'); return c ? c.value.trim() : ""; };
    var nombre = [val("nombres"), val("apellido_1"), val("apellido_2")].filter(Boolean).join(" ");
    f.querySelector("[data-r-nombre]").textContent = nombre || "—";
    f.querySelector("[data-r-usuario]").textContent = val("telefono") || val("correo") || "—";
    var hijos = lista.querySelectorAll("li");
    f.querySelector("[data-r-cuantos]").textContent = hijos.length;
    var r = f.querySelector("[data-r-hijos]");
    r.textContent = "";
    hijos.forEach(function (li) {
      var s = document.createElement("span");
      var c = function (n) { var x = li.querySelector('input[name="' + n + '"]'); return x ? x.value.trim() : ""; };
      s.textContent = "· " + [c("hijo_nombres"), c("hijo_paterno"), c("hijo_materno")].filter(Boolean).join(" ");
      r.appendChild(s);
    });
    lista.hidden = hijos.length === 0;
    if (agregar) agregar.disabled = hijos.length >= maximo;
  }

  function agregarHijo() {
    if (lista.querySelectorAll("li").length >= maximo) return;  // tope (también con Enter)
    var v = CAMPOS.map(function (n) { return campo(n).value.trim(); });
    if (!v[0] || !v[1]) { error.hidden = false; campo(v[0] ? "hijo_paterno" : "hijo_nombres").focus(); return; }
    error.hidden = true;
    var li = document.createElement("li");
    li.className = "hijo-alta";
    var datos = document.createElement("span");
    datos.className = "opcion-texto";
    var n = document.createElement("span");
    n.className = "hijo-nombre";
    n.textContent = [v[0], v[1], v[2]].filter(Boolean).join(" ");
    var m = document.createElement("span");
    m.className = "chico";
    m.textContent = v[3] ? "Matrícula " + v[3] : "Sin matrícula";
    datos.appendChild(n); datos.appendChild(m); li.appendChild(datos);
    CAMPOS.forEach(function (nombre, i) {
      var h = document.createElement("input");
      h.type = "hidden"; h.name = nombre; h.value = v[i];
      li.appendChild(h);
      campo(nombre).value = "";
    });
    // El botón «Quitar» sale de un <template> de la página (sin armar HTML con texto).
    li.appendChild(document.getElementById("boton-quitar").content.cloneNode(true));
    lista.appendChild(li);
    resumen();
    campo("hijo_nombres").focus();
  }

  if (agregar) agregar.addEventListener("click", agregarHijo);
  if (nuevo) nuevo.addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); agregarHijo(); }  // Enter agrega, no envía
  });
  f.addEventListener("click", function (e) {
    var q = e.target.closest("[data-quitar]");
    if (q) { q.closest("li").remove(); resumen(); }
  });
  f.addEventListener("input", resumen);
  resumen();
})();
