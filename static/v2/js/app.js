/*
 * Tiquetera v2 — interacciones mínimas con mejora progresiva.
 *  - Cajón de navegación en tablet/móvil (botón, Escape, clic fuera, cierre al salir el foco,
 *    devolución de foco).
 *  - Panel de filtros colapsable en móvil (aria-expanded).
 * Todo el contenido funciona sin este archivo. Sin dependencias externas.
 */
(function () {
  "use strict";

  var mqDrawer = window.matchMedia("(max-width: 64rem)");

  /* --- Cajón de navegación ------------------------------------------------ */
  var toggle = document.querySelector("[data-nav-toggle]");
  var drawer = document.getElementById("nav-drawer");
  var backdrop = document.querySelector("[data-nav-backdrop]");

  function isOpen() {
    return drawer && drawer.classList.contains("is-open");
  }

  function openDrawer() {
    drawer.classList.add("is-open");
    toggle.setAttribute("aria-expanded", "true");
    if (backdrop) backdrop.hidden = false;
    var first = drawer.querySelector("a, button:not([disabled])");
    if (first) first.focus();
  }

  function closeDrawer(returnFocus) {
    drawer.classList.remove("is-open");
    toggle.setAttribute("aria-expanded", "false");
    if (backdrop) backdrop.hidden = true;
    if (returnFocus) toggle.focus();
  }

  if (toggle && drawer) {
    toggle.addEventListener("click", function () {
      if (isOpen()) closeDrawer(true); else openDrawer();
    });
    if (backdrop) backdrop.addEventListener("click", function () { closeDrawer(true); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && isOpen()) closeDrawer(true);
    });
    mqDrawer.addEventListener("change", function (e) {
      if (!e.matches && isOpen()) closeDrawer(false);
    });
    // Si el foco del teclado sale del cajón abierto, se cierra (el foco no queda tapado).
    document.addEventListener("focusin", function (e) {
      if (isOpen() && !drawer.contains(e.target) && e.target !== toggle) closeDrawer(false);
    });
  }

  /* --- Panel de filtros (móvil) ------------------------------------------- */
  var fToggle = document.querySelector("[data-filters-toggle]");
  var fPanel = document.getElementById("filters-panel");
  var mqFilters = window.matchMedia("(max-width: 47.5rem)");

  function setFilters(expanded) {
    fPanel.setAttribute("data-collapsed", expanded ? "false" : "true");
    fToggle.setAttribute("aria-expanded", expanded ? "true" : "false");
  }

  if (fToggle && fPanel) {
    // En móvil arranca colapsado, salvo que haya errores de filtro que mostrar.
    var hasErrors = fPanel.querySelector("[aria-invalid='true']") !== null;
    setFilters(!mqFilters.matches || hasErrors);
    fToggle.addEventListener("click", function () {
      var expanded = fToggle.getAttribute("aria-expanded") === "true";
      setFilters(!expanded);
      if (!expanded) {
        var firstField = fPanel.querySelector("input, select");
        if (firstField) firstField.focus();
      }
    });
    mqFilters.addEventListener("change", function (e) {
      if (!e.matches) setFilters(true);
    });
  }
})();
