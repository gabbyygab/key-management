/* csrf.js — CSRF token helper + icon refresh helper.
 *
 * Load this before any other KEYTRACK script: modal-form.js, pos.js,
 * search.js and schedule-grid.js all depend on window.KeyTrack.
 *
 * Django's CsrfViewMiddleware stays enabled for AJAX, so every unsafe
 * request (POST/PUT/PATCH/DELETE) must carry the X-CSRFToken header.
 */
(function () {
  "use strict";

  var KeyTrack = (window.KeyTrack = window.KeyTrack || {});

  /* Read the csrftoken cookie Django set on this response. */
  function getCsrfToken() {
    var match = document.cookie
      .split("; ")
      .find(function (c) {
        return c.startsWith("csrftoken=");
      });
    return match ? decodeURIComponent(match.split("=")[1]) : "";
  }

  /* Headers for a JSON body. */
  function jsonHeaders() {
    return {
      "X-CSRFToken": getCsrfToken(),
      "Content-Type": "application/json",
      "X-Requested-With": "XMLHttpRequest",
    };
  }

  /* Headers for a FormData / urlencoded body — do NOT set Content-Type here,
   * the browser must add the multipart boundary itself. */
  function formHeaders() {
    return {
      "X-CSRFToken": getCsrfToken(),
      "X-Requested-With": "XMLHttpRequest",
    };
  }

  /* Re-run the Lucide icon factory.
   *
   * Lucide replaces each <i data-lucide="..."> with an <svg> ONCE, at the
   * time createIcons() runs. Any markup injected afterwards (an AJAX modal
   * body, a POS search result, a repainted schedule grid) still holds a bare
   * <i>, which renders as blank space. Call this after EVERY DOM injection.
   *
   * Optionally scoped to one container so a big page does not re-walk
   * everything: KeyTrack.refreshIcons(modalBody).
   */
  function refreshIcons(root) {
    if (typeof window.lucide === "undefined") return;
    window.lucide.createIcons(root ? { root: root } : {});
  }

  KeyTrack.getCsrfToken = getCsrfToken;
  KeyTrack.jsonHeaders = jsonHeaders;
  KeyTrack.formHeaders = formHeaders;
  KeyTrack.refreshIcons = refreshIcons;

  /* Backwards-compatible bare global, as spelled in GUIDE.md §5. */
  window.getCsrfToken = getCsrfToken;
})();
