/* toast.js — transient notifications for AJAX actions (F1.4).
 *
 * Django's {% messages %} block (partials/messages.html) still handles
 * non-AJAX page loads; this is the in-page equivalent for fetch() results.
 *
 *   KeyTrack.toast("Key RM303 released.", "success");
 *   KeyTrack.toast("You do not have permission.", "error");
 *
 * Requires: csrf.js (window.KeyTrack, refreshIcons).
 */
(function () {
  "use strict";

  var KeyTrack = (window.KeyTrack = window.KeyTrack || {});

  var AUTO_DISMISS_MS = 4000;

  /* Design tokens per §5 — do not invent new ones. */
  var VARIANTS = {
    success: {
      icon: "check-circle",
      cls: "bg-green-50 border-green-200 text-green-800",
      iconCls: "text-green-600",
    },
    error: {
      icon: "alert-circle",
      cls: "bg-red-50 border-red-200 text-red-800",
      iconCls: "text-red-600",
    },
    warning: {
      icon: "alert-triangle",
      cls: "bg-amber-50 border-amber-200 text-amber-800",
      iconCls: "text-amber-600",
    },
    info: {
      icon: "info",
      cls: "bg-white border-stone-200 text-stone-700",
      iconCls: "text-stone-500",
    },
  };

  var container = null;

  function getContainer() {
    if (container && document.body.contains(container)) return container;
    container = document.getElementById("toast-container");
    if (!container) {
      container = document.createElement("div");
      container.id = "toast-container";
      container.className =
        "fixed top-4 right-4 z-[100] flex flex-col gap-2 w-[min(22rem,calc(100vw-2rem))]";
      /* Polite, not assertive: a toast must not interrupt a screen reader
       * mid-sentence. Errors that block the user belong inline, not here. */
      container.setAttribute("aria-live", "polite");
      container.setAttribute("aria-atomic", "false");
      document.body.appendChild(container);
    }
    return container;
  }

  function dismiss(el) {
    if (!el || el.dataset.leaving === "1") return;
    el.dataset.leaving = "1";
    el.classList.add("opacity-0", "translate-x-2");
    window.setTimeout(function () {
      if (el.parentNode) el.parentNode.removeChild(el);
    }, 200);
  }

  /* message is inserted as TEXT, never HTML — 422 bodies echo user input. */
  function toast(message, variant, options) {
    var opts = options || {};
    var v = VARIANTS[variant] || VARIANTS.info;

    var el = document.createElement("div");
    el.className =
      "flex items-start gap-2.5 rounded-xl border px-4 py-2.5 shadow-sm text-[13px] " +
      "transition duration-200 ease-out " +
      v.cls;
    el.setAttribute("role", variant === "error" ? "alert" : "status");

    var icon = document.createElement("i");
    icon.setAttribute("data-lucide", v.icon);
    icon.className = "w-4 h-4 mt-0.5 shrink-0 " + v.iconCls;

    var body = document.createElement("div");
    body.className = "flex-1 min-w-0";
    body.textContent = message == null ? "" : String(message);

    var close = document.createElement("button");
    close.type = "button";
    close.className = "shrink-0 opacity-50 hover:opacity-100 rounded";
    close.setAttribute("aria-label", "Dismiss notification");
    var closeIcon = document.createElement("i");
    closeIcon.setAttribute("data-lucide", "x");
    closeIcon.className = "w-3.5 h-3.5 pointer-events-none";
    close.appendChild(closeIcon);
    close.addEventListener("click", function () {
      dismiss(el);
    });

    el.appendChild(icon);
    el.appendChild(body);
    el.appendChild(close);
    getContainer().appendChild(el);

    /* Freshly injected <i data-lucide> elements are inert until this runs. */
    KeyTrack.refreshIcons(el);

    var ms = opts.duration === undefined ? AUTO_DISMISS_MS : opts.duration;
    if (ms > 0) {
      var timer = window.setTimeout(function () {
        dismiss(el);
      }, ms);
      /* Don't yank a message out from under someone reading it. */
      el.addEventListener("mouseenter", function () {
        window.clearTimeout(timer);
      });
    }

    return el;
  }

  KeyTrack.toast = toast;
  KeyTrack.dismissToast = dismiss;
})();
