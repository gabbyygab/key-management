/*
 * scroll-restore.js: keep the reader where they were across full page loads.
 *
 * Without this, every plain form POST (archive, restore, toggle, a no-JS
 * save, ...) redirects back to the same page and the browser starts it at
 * the top. The sidebar nav is its own scroller, so it also jumps back to the
 * top on every navigation, even when the link clicked sat at its bottom.
 *
 *   1. Sidebar (and any [data-preserve-scroll="<key>"] element): its scroll
 *      offset is saved on pagehide and restored on the next page, whatever
 *      the URL. A key ending in ":page" is restored only on the same path.
 *   2. Window: restored only when the next page has the same path (a POST
 *      that redirects back, a reload, a no-JS filter or pagination submit).
 *      A different page still opens at the top, a #hash still wins, and
 *      back/forward is left to the browser.
 *
 * Loaded right after the sidebar include in base.html (before csrf.js), so
 * the sidebar is restored before first paint. The window offset waits for
 * DOMContentLoaded, and is retried on load in case late content grew the
 * page, unless the user has scrolled in the meantime.
 */
(function () {
  "use strict";

  var KEY = "kt:scroll";
  var MAX_AGE_MS = 10 * 60 * 1000;
  var path = window.location.pathname;

  function read() {
    try {
      var raw = window.sessionStorage.getItem(KEY);
      if (!raw) return null;
      var data = JSON.parse(raw);
      if (!data || Date.now() - data.t > MAX_AGE_MS) return null;
      return data;
    } catch (e) {
      return null;
    }
  }

  function save() {
    var els = {};
    document.querySelectorAll("[data-preserve-scroll]").forEach(function (el) {
      var k = el.getAttribute("data-preserve-scroll");
      if (k) els[k] = [el.scrollLeft, el.scrollTop];
    });
    try {
      window.sessionStorage.setItem(KEY, JSON.stringify({
        t: Date.now(),
        path: path,
        x: window.scrollX,
        y: window.scrollY,
        els: els
      }));
    } catch (e) { /* private mode / storage full: nothing to keep */ }
  }

  function navType() {
    try {
      var nav = performance.getEntriesByType("navigation")[0];
      if (nav) return nav.type;
    } catch (e) { /* old browser */ }
    return "navigate";
  }

  var saved = read();
  var samePath = !!saved && saved.path === path;

  function restoreElements(root) {
    if (!saved || !saved.els) return;
    (root || document).querySelectorAll("[data-preserve-scroll]").forEach(function (el) {
      var k = el.getAttribute("data-preserve-scroll");
      var pos = saved.els[k];
      if (!pos) return;
      if (/:page$/.test(k) && !samePath) return;
      el.scrollLeft = pos[0];
      el.scrollTop = pos[1];
    });
  }

  /* 1. Elements that already exist (the sidebar) — now, before paint. */
  restoreElements(document);

  /* 2. Window + elements further down the page. */
  var wantWindow = samePath && !window.location.hash && navType() !== "back_forward" &&
    (saved.y > 0 || saved.x > 0);
  var userMoved = false;

  function markMoved() { userMoved = true; }

  function restoreWindow() {
    if (!wantWindow || userMoved) return;
    window.scrollTo(saved.x, saved.y);
  }

  if (wantWindow) {
    ["wheel", "touchstart", "keydown", "mousedown"].forEach(function (evt) {
      window.addEventListener(evt, markMoved, { passive: true, once: true });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    restoreElements(document);
    restoreWindow();
  });
  window.addEventListener("load", function () {
    /* Late layout (fonts, icons) may have changed the height; re-apply
     * only if the first attempt fell short and the user has not moved. */
    if (wantWindow && Math.abs(window.scrollY - saved.y) > 2) restoreWindow();
  });

  window.addEventListener("pagehide", save);
})();
