/*
 * Tiquetera — menú móvil de navegación.
 * Reemplaza el patrón checkbox + label por un botón semántico controlado
 * por JavaScript, con soporte de teclado y cierre con Escape.
 * No depende de frameworks ni librerías externas.
 */
(function () {
  "use strict";

  var btn = document.getElementById("nav_toggle_btn");
  var menu = document.getElementById("navList");

  if (!btn || !menu) {
    return;
  }

  function isOpen() {
    return menu.classList.contains("is-open");
  }

  function openMenu() {
    menu.classList.add("is-open");
    btn.setAttribute("aria-expanded", "true");
    var firstLink = menu.querySelector("a");
    if (firstLink) {
      firstLink.focus();
    }
  }

  function closeMenu(returnFocusToToggle) {
    menu.classList.remove("is-open");
    btn.setAttribute("aria-expanded", "false");
    if (returnFocusToToggle) {
      btn.focus();
    }
  }

  btn.addEventListener("click", function () {
    if (isOpen()) {
      closeMenu(false);
    } else {
      openMenu();
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && isOpen()) {
      closeMenu(true);
    }
  });

  // Si la ventana vuelve a tamaño de escritorio mientras el menú está
  // abierto, se limpia el estado para evitar un menú "abierto" oculto
  // por las reglas de escritorio.
  window.addEventListener("resize", function () {
    if (window.innerWidth > 768 && isOpen()) {
      closeMenu(false);
    }
  });
})();
