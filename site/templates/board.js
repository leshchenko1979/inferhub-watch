(function () {
  function openHash() {
    var id = location.hash.replace(/^#/, "");
    if (!id) return;
    var el = document.getElementById(id);
    if (el && el.tagName === "DETAILS") el.open = true;
  }
  var menu = document.querySelector(".nav-menu");
  var summary = menu && menu.querySelector("summary");
  var mq = window.matchMedia("(max-width: 720px)");
  function syncExpanded() {
    if (!summary || !menu) return;
    summary.setAttribute("aria-expanded", menu.open ? "true" : "false");
  }
  function adaptNav() {
    if (!menu) return;
    if (mq.matches) {
      menu.removeAttribute("open");
    } else {
      menu.setAttribute("open", "");
    }
    syncExpanded();
  }
  function closeNav() {
    if (menu && mq.matches) menu.removeAttribute("open");
    syncExpanded();
  }
  openHash();
  window.addEventListener("hashchange", openHash);
  if (mq.addEventListener) mq.addEventListener("change", adaptNav);
  else mq.addListener(adaptNav);
  adaptNav();
  if (menu) menu.addEventListener("toggle", syncExpanded);
  document.querySelectorAll(".site-nav a").forEach(function (a) {
    a.addEventListener("click", closeNav);
  });
})();

/* Timeline tooltips: hover on pointer devices, tap to toggle on touch. */
(function () {
  var cells = document.querySelectorAll("td[data-tip]");
  if (!cells.length) return;

  var tip = document.createElement("div");
  tip.id = "tipbox";
  tip.className = "tipbox";
  tip.setAttribute("role", "tooltip");
  tip.hidden = true;
  document.body.appendChild(tip);

  var current = null;

  function hide() {
    tip.hidden = true;
    if (current) current.classList.remove("tip-open");
    current = null;
  }

  function place(cell) {
    var r = cell.getBoundingClientRect();
    var tw = tip.offsetWidth;
    var th = tip.offsetHeight;
    var left = r.left + r.width / 2 - tw / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - tw - 8));
    var top = r.top - th - 8;
    if (top < 8) top = r.bottom + 8;
    tip.style.left = left + window.scrollX + "px";
    tip.style.top = top + window.scrollY + "px";
  }

  function show(cell) {
    var text = cell.getAttribute("data-tip");
    if (!text) return;
    tip.textContent = text;
    tip.hidden = false;
    if (current && current !== cell) current.classList.remove("tip-open");
    current = cell;
    cell.classList.add("tip-open");
    place(cell);
  }

  if (window.matchMedia("(hover: hover)").matches) {
    cells.forEach(function (cell) {
      cell.addEventListener("mouseenter", function () { show(cell); });
      cell.addEventListener("mouseleave", function () { hide(); });
    });
  }

  cells.forEach(function (cell) {
    cell.addEventListener("focus", function () { show(cell); });
    cell.addEventListener("blur", function () { hide(); });
    cell.addEventListener("click", function (e) {
      e.stopPropagation();
      if (current === cell && !tip.hidden) hide();
      else show(cell);
    });
  });

  document.addEventListener("click", function (e) {
    if (!tip.hidden && e.target !== tip && !tip.contains(e.target)) hide();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") hide();
  });
  window.addEventListener("scroll", function () {
    if (!tip.hidden) hide();
  }, { passive: true });
})();
