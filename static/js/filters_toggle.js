/*
 * Tiquetera — Incremento 4.3D: botón colapsable de filtros en móvil.
 *
 * Mejora progresiva: el panel de filtros (#filters_container) es visible
 * y utilizable por CSS puro, con o sin este script. Si este script no
 * carga (JS deshabilitado, error de red, etc.), el panel se queda tal
 * como está en el HTML -- expandido -- y el formulario sigue funcionando
 * por GET normalmente. Este script solo:
 *   1. Si el viewport es movil (<=768px) al cargar la pagina, colapsa el
 *      panel para ahorrar espacio (nunca lo oculta si no se cumple eso).
 *   2. Conecta el boton "Filtros" para expandir/contraer el panel.
 *   3. Permite cerrar con Escape devolviendo el foco al boton.
 *
 * Mismo patron ya usado en nav.js (boton semantico + aria-expanded +
 * clase CSS + manejo de foco), sin frameworks ni dependencias.
 */
(function () {
  "use strict";

  var btn = document.getElementById("filters_toggle_btn");
  var panel = document.getElementById("filters_container");

  if (!btn || !panel) {
    return;
  }

  function isCollapsed() {
    return panel.classList.contains("is-collapsed");
  }

  function collapse(returnFocusToToggle) {
    panel.classList.add("is-collapsed");
    btn.setAttribute("aria-expanded", "false");
    if (returnFocusToToggle) {
      btn.focus();
    }
  }

  function expand(focusFirstField) {
    panel.classList.remove("is-collapsed");
    btn.setAttribute("aria-expanded", "true");
    if (focusFirstField) {
      var firstField = panel.querySelector("select, input, a, button");
      if (firstField) {
        firstField.focus();
      }
    }
  }

  btn.addEventListener("click", function () {
    if (isCollapsed()) {
      expand(true);
    } else {
      collapse(false);
    }
  });

  panel.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !isCollapsed()) {
      collapse(true);
    }
  });

  if (window.matchMedia && window.matchMedia("(max-width: 768px)").matches) {
    collapse(false);
  }
})();
