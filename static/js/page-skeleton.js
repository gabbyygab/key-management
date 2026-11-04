/* page-skeleton.js — instant feedback while a full page loads.
 *
 * Pages are rendered on the server and each database query costs a round trip
 * to Supabase, so a sidebar click can sit on the old page for a moment. After
 * SHOW_DELAY ms of waiting, <main> is swapped for a skeleton shaped like the
 * destination (dashboard cards, report tables, list table, timetable grid).
 * Fast loads never flash it, and the browser still does the real navigation,
 * so this works without any server cooperation.
 *
 * Triggers: a click on a sidebar link, or on any [data-page-skeleton] link;
 * a submit of a GET form marked [data-page-skeleton] (e.g. report filters).
 * The kind comes from data-page-skeleton="dashboard|table|reports|grid", else
 * from the destination path. The POS (own layout) is skipped.
 *
 * Requires: csrf.js (window.KeyTrack), skeletons.js (tableRows, scheduleGrid).
 */
(function () {
  "use strict";

  var KeyTrack = (window.KeyTrack = window.KeyTrack || {});
  var SHOW_DELAY = 400;
  var timer = null;

  function bar(w, h) {
    return '<div class="' + (h || "h-4") + " bg-stone-200 rounded " + w + '"></div>';
  }

  function cards(n) {
    var out = [];
    for (var i = 0; i < n; i++) {
      out.push(
        '<div class="bg-white rounded-xl border border-stone-200 shadow-sm p-4">' +
          '<div class="flex items-start justify-between">' + bar("w-24", "h-3.5") +
          '<div class="w-8 h-8 rounded-lg bg-stone-100"></div></div>' +
          bar("w-16 mt-2", "h-7") + bar("w-28 mt-2", "h-3") +
        "</div>"
      );
    }
    return '<div class="grid grid-cols-2 xl:grid-cols-4 gap-3">' + out.join("") + "</div>";
  }

  function heading() {
    return '<div class="mb-4 space-y-2">' + bar("w-48", "h-6") + bar("w-80 max-w-full", "h-3.5") + "</div>";
  }

  function panel(inner) {
    return '<div class="bg-white rounded-xl border border-stone-200 shadow-sm overflow-hidden">' + inner + "</div>";
  }

  function tablePanel(cols, rows) {
    var head = "";
    for (var i = 0; i < cols; i++) head += '<th class="p-3">' + bar("w-16", "h-3") + "</th>";
    return panel(
      '<table class="min-w-full text-[13px]"><thead><tr class="border-b border-stone-200 bg-stone-50/50">' +
        head + "</tr></thead><tbody>" + KeyTrack.skeletons.tableRows(cols, rows) + "</tbody></table>"
    );
  }

  function build(kind) {
    switch (kind) {
      case "dashboard":
        return heading() + cards(4) +
          '<div class="grid lg:grid-cols-2 gap-3 mt-3">' + tablePanel(3, 5) + tablePanel(3, 5) + "</div>";
      case "reports":
        return heading() + '<div class="mb-3">' + bar("w-full max-w-xl", "h-10") + "</div>" + cards(4) +
          '<div class="mt-3">' + tablePanel(6, 8) + "</div>";
      case "grid":
        return heading() + '<div class="mb-3">' + bar("w-full max-w-2xl", "h-10") + "</div>" +
          KeyTrack.skeletons.scheduleGrid();
      default:
        return heading() + '<div class="flex gap-2 mb-3">' + bar("w-64", "h-9") + bar("w-32", "h-9") + "</div>" +
          tablePanel(5, 10);
    }
  }

  function kindFor(url, hinted) {
    if (hinted) return hinted;
    var p = url.pathname;
    if (p === "/") return "dashboard";
    if (p.indexOf("/reports/") === 0) return "reports";
    if (p.indexOf("/scheduling/timetable") === 0) return "grid";
    return "table";
  }

  function schedule(url, hinted) {
    if (url.origin !== window.location.origin) return;
    if (url.pathname.indexOf("/transactions/borrow") === 0) return; /* POS has its own layout */
    if (!KeyTrack.skeletons || !KeyTrack.skeletons.tableRows) return;
    var kind = kindFor(url, hinted);
    clearTimeout(timer);
    timer = setTimeout(function () {
      var main = document.getElementById("main-content");
      if (!main) return;
      main.setAttribute("aria-busy", "true");
      main.innerHTML =
        '<div class="animate-pulse" role="status" aria-label="Loading page...">' + build(kind) +
        '<span class="sr-only">Loading page...</span></div>';
    }, SHOW_DELAY);
  }

  document.addEventListener("click", function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest && e.target.closest("a.sidebar-link[href], a[data-page-skeleton][href]");
    if (!a || a.target === "_blank" || a.hasAttribute("download") || a.hasAttribute("data-modal-open")) return;
    var href = a.getAttribute("href");
    if (!href || href.charAt(0) === "#" || href.indexOf("javascript:") === 0) return;
    var url = new URL(a.href, window.location.href);
    if (url.pathname === window.location.pathname && url.search === window.location.search) return;
    schedule(url, a.getAttribute("data-page-skeleton"));
  });

  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (e.defaultPrevented || !f.matches || !f.matches("form[data-page-skeleton]")) return;
    if ((f.getAttribute("method") || "get").toLowerCase() !== "get") return;
    schedule(new URL(f.getAttribute("action") || window.location.pathname, window.location.origin),
      f.getAttribute("data-page-skeleton"));
  });

  /* Back/forward can restore the page with the skeleton still in it. */
  window.addEventListener("pageshow", function (e) {
    var main = document.getElementById("main-content");
    if (e.persisted && main && main.getAttribute("aria-busy") === "true") window.location.reload();
  });
  window.addEventListener("pagehide", function () { clearTimeout(timer); });
})();
