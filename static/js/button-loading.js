/* button-loading.js — one busy state for every button in the app.
 *
 * Two jobs:
 *
 *   1. KeyTrack.buttonLoading(btn, "Archiving…") -> restore()
 *      Disables the button, puts a spinner in front of its label (or in place
 *      of it, when a label is passed) and returns the function that puts it
 *      back. Used by bulk-actions.js and available to any per-page script.
 *      modal-form.js keeps its own copy for the modal save path; everything
 *      else goes through here.
 *
 *   2. Automatic busy state on plain (non-AJAX) form submits.
 *      A full-page POST — Archive, Restore, Logout, the password forms — can
 *      take a round trip with no feedback at all, and a second click
 *      double-posts it. A delegated submit listener marks the submitter busy
 *      and leaves it that way: the page is on its way out.
 *
 *      Submits already handled in JS (modal-form.js, list-region.js,
 *      confirm.js's first pass) call preventDefault, so they are skipped by
 *      the defaultPrevented check — this file must load AFTER those three.
 *
 * The original markup is never replaced: a spinner node is inserted and
 * removed again, so restoring (including after a bfcache Back) cannot lose
 * a button's icon or label.
 *
 * Requires: csrf.js (window.KeyTrack, refreshIcons).
 */
(function () {
  "use strict";

  var KeyTrack = (window.KeyTrack = window.KeyTrack || {});

  function makeSpinner() {
    /* Lucide, like every other icon: refreshIcons() turns it into an <svg>.
     * `animate-spin` is already in the build (modal-form.js uses it) and is
     * switched off under prefers-reduced-motion by source.css. */
    var i = document.createElement("i");
    i.setAttribute("data-lucide", "loader-circle");
    i.setAttribute("aria-hidden", "true");
    i.className = "kt-spinner w-4 h-4 animate-spin shrink-0";
    return i;
  }

  function clear(btn) {
    var nodes = btn.querySelectorAll(".kt-spinner, .kt-busy-text");
    for (var i = 0; i < nodes.length; i++) nodes[i].remove();
    /* Put the button's own content back exactly as it was. */
    var keep = btn.querySelector(".kt-busy-original");
    if (keep) {
      while (keep.firstChild) btn.insertBefore(keep.firstChild, keep);
      keep.remove();
    }
    btn.classList.remove("kt-busy");
    btn.removeAttribute("aria-busy");
    btn.style.minWidth = "";
    btn.dataset.ktLoading = "";
  }

  function buttonLoading(btn, label) {
    if (!btn || btn.dataset.ktLoading === "1") return function () {};

    /* Hold the width so a row of buttons does not jump while one is busy. */
    var width = btn.offsetWidth;
    if (width) btn.style.minWidth = width + "px";

    btn.dataset.ktLoading = "1";
    btn.disabled = true;
    btn.setAttribute("aria-busy", "true");
    btn.classList.add("kt-busy");

    if (label) {
      /* Park the button's own content — icon, label, the bare text node a
       * label usually is — in a hidden wrapper instead of overwriting it, so
       * restoring it cannot lose anything. A CSS rule that hid the children
       * could not touch a bare text node. */
      var keep = document.createElement("span");
      keep.className = "kt-busy-original";
      while (btn.firstChild) keep.appendChild(btn.firstChild);
      btn.appendChild(keep);
    }

    btn.insertBefore(makeSpinner(), btn.firstChild);

    if (label) {
      var text = document.createElement("span");
      text.className = "kt-busy-text";
      text.textContent = label;
      btn.appendChild(text);
    }
    if (KeyTrack.refreshIcons) KeyTrack.refreshIcons(btn);

    return function restore() {
      if (btn.dataset.ktLoading !== "1") return;
      btn.disabled = false;
      clear(btn);
    };
  }

  /* ---- automatic busy state for ordinary form submits ----------------- */

  function submitterFor(e, form) {
    if (e.submitter && e.submitter.tagName === "BUTTON") return e.submitter;
    return form.querySelector("button[type=submit], button:not([type])");
  }

  document.addEventListener("submit", function (e) {
    /* Handled by modal-form.js / list-region.js / confirm.js's first pass. */
    if (e.defaultPrevented) return;
    var form = e.target;
    if (!form.matches || !form.matches("form") || form.hasAttribute("data-no-loading")) return;
    if ((form.getAttribute("method") || "get").toLowerCase() !== "post") return;

    var btn = submitterFor(e, form);
    if (!btn) return;
    keepSubmitterValue(form, e.submitter === btn ? btn : null);
    buttonLoading(btn, btn.getAttribute("data-loading-label") || "");
  });

  /* A disabled control is left out of the form data, and the browser builds
   * that data only AFTER the submit listeners run. Disabling the submitter
   * here therefore silently drops its name=value, so a form with two submit
   * buttons (Auto-schedule's "Preview" vs "Run and save") could no longer
   * tell them apart. Carry the pair in a hidden input instead. */
  function keepSubmitterValue(form, btn) {
    var old = form.querySelectorAll("input[data-kt-submitter]");
    for (var i = 0; i < old.length; i++) old[i].remove();
    if (!btn || !btn.name) return;
    var input = document.createElement("input");
    input.type = "hidden";
    input.name = btn.name;
    input.value = btn.value;
    input.setAttribute("data-kt-submitter", "");
    form.appendChild(input);
  }

  /* Coming Back to a page the browser kept alive (bfcache) would otherwise
   * leave the button spinning and disabled for good. */
  window.addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    var stale = document.querySelectorAll("input[data-kt-submitter]");
    for (var j = 0; j < stale.length; j++) stale[j].remove();
    var busy = document.querySelectorAll("[data-kt-loading='1']");
    for (var i = 0; i < busy.length; i++) {
      busy[i].disabled = false;
      clear(busy[i]);
    }
  });

  KeyTrack.buttonLoading = buttonLoading;
})();
