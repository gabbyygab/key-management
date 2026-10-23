/* navbar.js — F2.3 navbar enhancements.
 *
 * 1. Admin quick-add "+" dropdown
 *    Opens/closes with click or keyboard (Space/Enter); dismisses on Escape or
 *    outside click.  Delegates to window.KeyTrackModal.open() so each menu
 *    item just carries data-modal-open and the modal system does the rest.
 *
 * 2. Server clock (POS screen)
 *    Ticks every second.  Seeded from data-server-now (ISO string written by
 *    the template at render time) so it always matches server time, not the
 *    guard's browser clock which may be minutes off.
 *
 * Loaded from base.html — always present when the user is authenticated.
 * Requires: modal.js (window.KeyTrackModal).
 */
(function () {
  "use strict";

  /* ── 1. Admin quick-add dropdown ──────────────────────────────────────── */

  var dropdown = document.getElementById("navbar-quickadd-dropdown");
  var trigger  = document.getElementById("navbar-quickadd-btn");

  if (dropdown && trigger) {
    function openDropdown() {
      dropdown.classList.remove("hidden");
      dropdown.removeAttribute("aria-hidden");
      trigger.setAttribute("aria-expanded", "true");
      /* Focus the first item for keyboard users. */
      var first = dropdown.querySelector("[role=menuitem]");
      if (first) first.focus();
    }

    function closeDropdown() {
      dropdown.classList.add("hidden");
      dropdown.setAttribute("aria-hidden", "true");
      trigger.setAttribute("aria-expanded", "false");
    }

    function toggleDropdown() {
      if (dropdown.classList.contains("hidden")) {
        openDropdown();
      } else {
        closeDropdown();
      }
    }

    trigger.addEventListener("click", function (e) {
      e.stopPropagation();
      toggleDropdown();
    });

    /* Space / Enter on the trigger also toggle it. */
    trigger.addEventListener("keydown", function (e) {
      if (e.key === " " || e.key === "Enter") {
        e.preventDefault();
        toggleDropdown();
      }
      if (e.key === "Escape") {
        closeDropdown();
        trigger.focus();
      }
    });

    /* Arrow keys move focus within the menu. */
    dropdown.addEventListener("keydown", function (e) {
      var items = Array.prototype.slice.call(
        dropdown.querySelectorAll("[role=menuitem]")
      );
      if (!items.length) return;
      var idx = items.indexOf(document.activeElement);

      if (e.key === "ArrowDown") {
        e.preventDefault();
        items[(idx + 1) % items.length].focus();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        items[(idx - 1 + items.length) % items.length].focus();
      } else if (e.key === "Escape") {
        e.preventDefault();
        closeDropdown();
        trigger.focus();
      }
    });

    /* Close when clicking outside. */
    document.addEventListener("click", function (e) {
      if (!trigger.contains(e.target) && !dropdown.contains(e.target)) {
        closeDropdown();
      }
    });

    /* Close when a modal opens from inside the dropdown (item was activated). */
    document.addEventListener("modal:open", function () {
      closeDropdown();
    }, true);
  }

  /* ── 2. Server clock ──────────────────────────────────────────────────── */

  var clockEl = document.getElementById("navbar-server-clock");
  if (clockEl) {
    var seed = clockEl.getAttribute("data-server-now");
    if (seed) {
      /* Compute the offset between server time and browser time at page load,
       * then apply it on every tick so the displayed time stays server-accurate
       * even if the client clock drifts. */
      var serverMs  = new Date(seed).getTime();
      var clientMs  = Date.now();
      var offsetMs  = serverMs - clientMs;

      function pad(n) { return n < 10 ? "0" + n : String(n); }

      function tick() {
        var now = new Date(Date.now() + offsetMs);
        var h   = pad(now.getHours());
        var m   = pad(now.getMinutes());
        var s   = pad(now.getSeconds());
        clockEl.textContent = h + ":" + m + ":" + s;
      }

      tick();
      setInterval(tick, 1000);
    }
  }
})();
