/* toast.js — transient notifications for AJAX actions (F1.4, F6.8).
 *
 * Django's {% messages %} block (partials/messages.html) still handles
 * non-AJAX page loads; this is the in-page equivalent for fetch() results.
 *
 *   KeyTrack.toast("Key RM303 released.", "success");
 *   KeyTrack.toast("You do not have permission.", "error");
 *   KeyTrack.networkErrorToast(retryFn, "Search failed.");
 *   KeyTrack.sessionExpiredToast("/accounts/login/?next=...");
 *
 * Requires: csrf.js (window.KeyTrack, refreshIcons).
 */
(function () {
  "use strict";

  var KeyTrack = (window.KeyTrack = window.KeyTrack || {});

  var AUTO_DISMISS_MS = 4000;
  var RETRY_DISMISS_MS = 10000;

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
    container = document.getElementById("toast-host");
    if (!container) {
      container = document.createElement("div");
      container.id = "toast-host";
      container.className =
        "fixed top-4 right-4 z-[100] flex flex-col gap-2 w-[min(24rem,calc(100vw-2rem))]";
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
    var variantName = Object.prototype.hasOwnProperty.call(VARIANTS, variant)
      ? variant
      : "info";
    var v = VARIANTS[variantName];

    var el = document.createElement("div");
    el.className =
      "flex items-start gap-2.5 rounded-xl border px-4 py-2.5 shadow-sm text-[13px] " +
      "transition duration-200 ease-out " +
      v.cls;
    el.setAttribute("role", variantName === "error" ? "alert" : "status");

    var icon = document.createElement("i");
    icon.setAttribute("data-lucide", v.icon);
    icon.className = "w-4 h-4 mt-0.5 shrink-0 " + v.iconCls;

    var body = document.createElement("div");
    body.className = "flex-1 min-w-0";

    var textSpan = document.createElement("span");
    textSpan.textContent = message == null ? "" : String(message);
    body.appendChild(textSpan);

    /* F6.8: Retry action button for network failures */
    if (typeof opts.onRetry === "function") {
      var retryBtn = document.createElement("button");
      retryBtn.type = "button";
      retryBtn.className =
        "ml-2 font-semibold underline hover:no-underline text-inherit focus:outline-none focus:ring-1 focus:ring-current rounded px-1 shrink-0 cursor-pointer";
      retryBtn.textContent = opts.retryLabel || "Retry";
      retryBtn.setAttribute("aria-label", opts.retryAriaLabel || "Retry failed request");
      retryBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        dismiss(el);
        opts.onRetry();
      });
      body.appendChild(retryBtn);
    }

    /* F6.8: Link or custom action (e.g. "Log in again") */
    if (opts.action && opts.action.label) {
      var actionEl;
      if (opts.action.href) {
        actionEl = document.createElement("a");
        actionEl.href = opts.action.href;
      } else {
        actionEl = document.createElement("button");
        actionEl.type = "button";
      }
      actionEl.className =
        "ml-2 font-semibold underline hover:no-underline text-inherit focus:outline-none focus:ring-1 focus:ring-current rounded px-1 shrink-0 inline-flex items-center gap-1 cursor-pointer";
      actionEl.textContent = opts.action.label;
      if (opts.action.onClick) {
        actionEl.addEventListener("click", function (e) {
          dismiss(el);
          opts.action.onClick(e);
        });
      }
      body.appendChild(actionEl);
    }

    var close = document.createElement("button");
    close.type = "button";
    close.className = "shrink-0 opacity-50 hover:opacity-100 rounded cursor-pointer";
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
    if (KeyTrack.refreshIcons) KeyTrack.refreshIcons(el);

    var defaultDuration = (typeof opts.onRetry === "function") ? RETRY_DISMISS_MS : AUTO_DISMISS_MS;
    var ms = opts.duration === undefined ? defaultDuration : opts.duration;
    if (ms > 0) {
      var remaining = ms;
      var timer = null;
      var startedAt = 0;
      function resumeTimer() {
        if (timer || el.dataset.leaving === "1") return;
        startedAt = Date.now();
        timer = window.setTimeout(function () {
          timer = null;
          dismiss(el);
        }, remaining);
      }
      function pauseTimer() {
        if (!timer) return;
        window.clearTimeout(timer);
        timer = null;
        remaining = Math.max(0, remaining - (Date.now() - startedAt));
      }

      /* Pause while hovered or keyboard-focused so the close button remains usable. */
      el.addEventListener("mouseenter", function () {
        pauseTimer();
      });
      el.addEventListener("mouseleave", resumeTimer);
      el.addEventListener("focusin", pauseTimer);
      el.addEventListener("focusout", function (event) {
        if (!el.contains(event.relatedTarget)) resumeTimer();
      });
      resumeTimer();
    }

    return el;
  }

  /* F6.8: Dedicated network failure toast with clickable retry option */
  function networkErrorToast(onRetry, message) {
    return toast(
      message || "Network connection failed. Check your connection and try again.",
      "error",
      {
        onRetry: onRetry,
        duration: RETRY_DISMISS_MS,
      }
    );
  }

  /* F6.8: Session expired warning toast with direct login path */
  function sessionExpiredToast(loginUrl) {
    var next = window.location.pathname + window.location.search;
    var url = loginUrl || ("/accounts/login/?next=" + encodeURIComponent(next));
    return toast(
      "Your session has expired. Please log in again.",
      "warning",
      {
        duration: 0, /* persistent until user clicks or dismisses */
        action: {
          label: "Log in again",
          href: url,
        },
      }
    );
  }

  KeyTrack.toast = toast;
  KeyTrack.dismissToast = dismiss;
  KeyTrack.networkErrorToast = networkErrorToast;
  KeyTrack.toastNetworkError = networkErrorToast;
  KeyTrack.sessionExpiredToast = sessionExpiredToast;
  KeyTrack.toastSessionExpired = sessionExpiredToast;

  /* Compat alias for code written against window.KeyTrackToast (currently-out panel). */
  window.KeyTrackToast = {
    show: function (m, v) { return KeyTrack.toast(m, v); },
    success: function (m) { return KeyTrack.toast(m, "success"); },
    error: function (m) { return KeyTrack.toast(m, "error"); },
    warning: function (m) { return KeyTrack.toast(m, "warning"); },
    info: function (m) { return KeyTrack.toast(m, "info"); },
    networkError: networkErrorToast,
    sessionExpired: sessionExpiredToast,
  };
})();
