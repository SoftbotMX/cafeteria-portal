// Recarga (5k): total y número de hijos en la barra mientras se escribe. Sólo orienta: el
// servidor valida todo al revisar (pesos enteros, mínimo y máximo).
(function () {
  "use strict";
  var f = document.getElementById("recarga");
  if (!f) return;
  var total = f.querySelector("[data-total]");
  var hijos = f.querySelector("[data-hijos]");
  var pesos = new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN" });
  function sumar() {
    var suma = 0, n = 0;
    f.querySelectorAll('input[name^="importe_"]').forEach(function (c) {
      var v = /^\d{1,7}$/.test(c.value.trim()) ? parseInt(c.value, 10) : 0;
      c.classList.toggle("cero", v === 0);
      if (v > 0) { suma += v; n += 1; }
    });
    total.textContent = pesos.format(suma).replace("MX", "");
    hijos.textContent = "total · " + n + (n === 1 ? " hijo" : " hijos");
  }
  f.addEventListener("input", sumar);
  sumar();
})();
