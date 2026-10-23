/* init-icons.js — the first Lucide pass for a server-rendered page.
 *
 * This replaces the `<script>lucide.createIcons();</script>` that used to sit
 * inline in base.html and login.html (F6.9 — no inline <script> in templates).
 *
 * It only covers markup present in the initial HTML response. Anything
 * injected afterwards — an AJAX modal body, a POS search result, a repainted
 * schedule grid — must call KeyTrack.refreshIcons() itself, or those icons
 * render as blank space.
 *
 * Loaded after lucide and csrf.js; safe on the login page, which has no
 * KeyTrack (falls back to calling lucide directly).
 */
(function () {
  "use strict";

  function init() {
    if (window.KeyTrack && window.KeyTrack.refreshIcons) {
      window.KeyTrack.refreshIcons();
    } else if (typeof window.lucide !== "undefined") {
      window.lucide.createIcons();
    }
  }

  /* The script tags sit at the end of <body>, so the DOM is normally already
   * parsed; the listener is the safety net if that ever changes. */
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
