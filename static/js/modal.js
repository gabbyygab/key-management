/* modal.js — one delegated modal controller for every modal in KEYTRACK (F1.2).
 *
 * Replaces the per-id IIFE that used to sit inline in
 * partials/logout_modal.html. Copying that script once per modal is the F7.10
 * defect; this is the single controller.
 *
 * Markup contract:
 *   <div id="room-form" class="hidden fixed inset-0 z-50 ..."
 *        role="dialog" aria-modal="true" aria-labelledby="room-form-title">
 *     <div data-modal-overlay class="absolute inset-0 bg-stone-900/60"></div>
 *     <div class="relative ...">  ... <button data-modal-close>  ... </div>
 *   </div>
 *
 *   open:  <button data-modal-open="room-form">
 *   close: <button data-modal-close>, overlay click, Escape
 *
 * API: window.KeyTrackModal = { open(id), close(id), closeTop(), isOpen(id) }
 *
 * Requires: csrf.js (window.KeyTrack, refreshIcons).
 */
(function () {
  "use strict";

  var KeyTrack = (window.KeyTrack = window.KeyTrack || {});

  var BASE_Z = 50; /* matches the z-50 already on logout-modal */
  var stack = []; /* [{ id, el, lastFocused }] — innermost modal is last */

  var FOCUSABLE = [
    "a[href]",
    "button:not([disabled])",
    "input:not([disabled]):not([type=hidden])",
    "select:not([disabled])",
    "textarea:not([disabled])",
    '[tabindex]:not([tabindex="-1"])',
  ].join(",");

  function focusableIn(el) {
    return Array.prototype.filter.call(
      el.querySelectorAll(FOCUSABLE),
      function (node) {
        /* offsetParent is null for display:none; also skip aria-hidden. */
        return node.offsetParent !== null && !node.closest("[aria-hidden=true]");
      }
    );
  }

  function entryFor(id) {
    for (var i = 0; i < stack.length; i++) {
      if (stack[i].id === id) return stack[i];
    }
    return null;
  }

  function top() {
    return stack.length ? stack[stack.length - 1] : null;
  }

  /* Focus the first real input, else the first focusable thing, else the
   * dialog itself. The logout modal focused Cancel; a form modal should land
   * on the first field instead. */
  function focusInitial(el) {
    var preferred = el.querySelector("[data-modal-autofocus]");
    if (!preferred) {
      preferred = el.querySelector(
        "input:not([disabled]):not([type=hidden]):not([type=submit]), select:not([disabled]), textarea:not([disabled])"
      );
    }
    if (!preferred) {
      var all = focusableIn(el);
      preferred = all.length ? all[0] : null;
    }
    if (preferred) {
      preferred.focus();
    } else {
      el.setAttribute("tabindex", "-1");
      el.focus();
    }
  }

  function updateStackZIndexes() {
    for (var i = 0; i < stack.length; i++) {
      stack[i].el.style.zIndex = String(BASE_Z + (i + 1) * 10);
    }
  }

  function open(id, options) {
    var el = document.getElementById(id);
    if (!el) return null;
    if (entryFor(id)) return el; /* already open — don't double-stack */

    options = options || {};
    var entry = {
      id: id,
      el: el,
      lastFocused: options.trigger || document.activeElement,
    };
    stack.push(entry);

    /* Stacked modals: each one sits above the previous (confirm-inside-form). */
    updateStackZIndexes();
    el.classList.remove("hidden");
    el.removeAttribute("aria-hidden");
    document.body.classList.add("overflow-hidden");

    focusInitial(el);
    KeyTrack.refreshIcons(el);
    el.dispatchEvent(new CustomEvent("modal:open", { bubbles: true, detail: { id: id } }));
    return el;
  }

  function close(id) {
    var entry = entryFor(id);
    if (!entry) return;

    var wasTop = top() === entry;
    stack.splice(stack.indexOf(entry), 1);
    entry.el.classList.add("hidden");
    entry.el.setAttribute("aria-hidden", "true");
    entry.el.style.zIndex = "";
    updateStackZIndexes();

    /* Only release the scroll lock once the last modal has closed. */
    if (!stack.length) document.body.classList.remove("overflow-hidden");

    /* Restore focus to whatever opened it, per the a11y behavior the logout
     * modal already got right. */
    var back = entry.lastFocused;
    if (
      wasTop &&
      back &&
      typeof back.focus === "function" &&
      document.body.contains(back)
    ) {
      back.focus();
    }
    entry.el.dispatchEvent(
      new CustomEvent("modal:close", { bubbles: true, detail: { id: id } })
    );
  }

  function closeTop() {
    var t = top();
    if (t) close(t.id);
  }

  document.addEventListener("click", function (e) {
    var opener = e.target.closest("[data-modal-open]");
    if (opener) {
      e.preventDefault();
      open(opener.getAttribute("data-modal-open"), { trigger: opener });
      return;
    }

    var closer = e.target.closest("[data-modal-close]");
    if (closer) {
      e.preventDefault();
      var owner = closer.closest("[role=dialog]");
      if (owner && owner.id) close(owner.id);
      else closeTop();
      return;
    }

    /* Overlay click — only the overlay of the topmost modal. */
    var overlay = e.target.closest("[data-modal-overlay]");
    if (overlay) {
      var dialog = overlay.closest("[role=dialog]");
      var t = top();
      if (dialog && t && dialog.id === t.id) close(t.id);
    }
  });

  document.addEventListener("keydown", function (e) {
    var t = top();
    if (!t) return;

    if (e.key === "Escape") {
      /* A modal that must not be dismissed by accident opts out. */
      if (t.el.hasAttribute("data-modal-no-escape")) return;
      e.preventDefault();
      close(t.id);
      return;
    }

    /* Focus trap: Tab must not escape the topmost dialog. */
    if (e.key === "Tab") {
      var items = focusableIn(t.el);
      if (!items.length) {
        e.preventDefault();
        return;
      }
      var first = items[0];
      var last = items[items.length - 1];
      if (e.shiftKey && (document.activeElement === first || !t.el.contains(document.activeElement))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  });

  window.KeyTrackModal = {
    open: open,
    close: close,
    closeTop: closeTop,
    isOpen: function (id) {
      return !!entryFor(id);
    },
    /* True while ANY modal is open — other scripts use this to avoid
     * stealing keystrokes from a dialog. */
    anyOpen: function () {
      return stack.length > 0;
    },
    topId: function () {
      var t = top();
      return t ? t.id : null;
    },
  };
})();
