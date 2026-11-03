/* toast.js — transient notifications for AJAX actions (F1.4, F6.8).
 *
 * Built on Toastify (static/js/.. loaded as the `Toastify` global in
 * base.html, before this file). Toastify only positions/stacks/animates;
 * the message box itself is a plain DOM node we build and style with the
 * app's own design tokens, passed in via Toastify's `node` option, so it
 * looks identical to the modal and inline UI (guide.md §5).
 *
 * Django's {% messages %} block (partials/messages.html) still handles
 * non-AJAX page loads; this is the in-page equivalent for fetch() results.
 *
 *   KeyTrack.toast("Key RM303 released.", "success");
 *   KeyTrack.toast("You do not have permission.", "error");
 *   KeyTrack.networkErrorToast(retryFn, "Search failed.");
 *   KeyTrack.sessionExpiredToast("/accounts/login/?next=...");
 *
 * Requires: csrf.js (window.KeyTrack, refreshIcons), Toastify.
 */
(function () {
  "use strict";

  var KeyTrack = (window.KeyTrack = window.KeyTrack || {});

  var AUTO_DISMISS_MS = 4000;
  var RETRY_DISMISS_MS = 10000;

  /* Toastify's `stopOnFocus` clears the countdown on mouseover and only
   * restarts it on mouseleave, so a toast under a resting cursor never goes
   * away — easy to hit now that toasts are top-centre. Keep the pause (it
   * lets you finish reading) but cap how long it can hold the toast open. */
  var HOVER_GRACE_MS = 4000;

  /* react-toastify's shape: one white card for every variant, the meaning
   * carried by the icon and by the timer line that drains along the bottom.
   * Only the accent colour changes per variant (source.css .kt-toast). */
  var VARIANTS = {
    success: { icon: "check-circle", iconCls: "text-[#1E6B4A]" },
    error: { icon: "alert-circle", iconCls: "text-red-600" },
    warning: { icon: "alert-triangle", iconCls: "text-amber-600" },
    info: { icon: "info", iconCls: "text-stone-500" },
  };

  /* message is inserted as TEXT, never HTML — 422 bodies echo user input. */
  function buildNode(message, variantName, opts) {
    var v = VARIANTS[variantName];

    var el = document.createElement("div");
    el.className =
      "kt-toast kt-toast-" + variantName +
      " flex items-start gap-3 px-5 py-3.5 text-[14px] leading-snug text-stone-700";
    el.setAttribute("role", variantName === "error" ? "alert" : "status");

    var icon = document.createElement("i");
    icon.setAttribute("data-lucide", v.icon);
    icon.className = "w-5 h-5 mt-px shrink-0 " + v.iconCls;

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
        dismiss(el._ktToastInstance);
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
          dismiss(el._ktToastInstance);
          opts.action.onClick(e);
        });
      }
      body.appendChild(actionEl);
    }

    var close = document.createElement("button");
    close.type = "button";
    close.className = "shrink-0 mt-px opacity-50 hover:opacity-100 rounded cursor-pointer";
    close.setAttribute("aria-label", "Dismiss notification");
    var closeIcon = document.createElement("i");
    closeIcon.setAttribute("data-lucide", "x");
    closeIcon.className = "w-4 h-4 pointer-events-none";
    close.appendChild(closeIcon);
    close.addEventListener("click", function (e) {
      e.stopPropagation();
      dismiss(el._ktToastInstance);
    });

    el.appendChild(icon);
    el.appendChild(body);
    el.appendChild(close);

    /* The timer line. Purely CSS (transform: scaleX, so it composites) and
     * paused on hover, which is exactly when Toastify's `stopOnFocus` pauses
     * the countdown — the line and the real timer stay in step. A toast that
     * never expires on its own (the session-expired one) gets no line. */
    if (opts.duration !== 0 && opts.durationMs > 0) {
      var bar = document.createElement("div");
      bar.className = "kt-toast-bar";
      bar.setAttribute("aria-hidden", "true");
      bar.style.animationDuration = opts.durationMs + "ms";
      el.appendChild(bar);
    }
    return el;
  }

  /* toast() returns the Toastify instance (has .hideToast()); pass it back
   * to KeyTrack.dismissToast() to close it programmatically. */
  function toast(message, variant, options) {
    var opts = options || {};
    var variantName = Object.prototype.hasOwnProperty.call(VARIANTS, variant)
      ? variant
      : "info";

    var defaultDuration = (typeof opts.onRetry === "function") ? RETRY_DISMISS_MS : AUTO_DISMISS_MS;
    var ms = opts.duration === undefined ? defaultDuration : opts.duration;

    /* buildNode needs the resolved duration to time the progress line. */
    opts.durationMs = ms;
    var node = buildNode(message, variantName, opts);

    var instance = window.Toastify({
      node: node,
      duration: ms > 0 ? ms : -1, /* Toastify treats a non-positive duration as persistent */
      close: false, /* we draw our own close button, styled to match */
      gravity: "top",
      /* Top-centre: Toastify's `center` position centres each toast with
       * `left:0; right:0; margin-inline:auto`, which composes with the
       * vertical `offset` transform below (that only translates on Y). */
      position: "center",
      stopOnFocus: true,
      /* Polite, not assertive: a toast must not interrupt a screen reader
       * mid-sentence. Errors that block the user belong inline, not here. */
      ariaLive: "polite",
      /* Clears the sticky navbar (static/css/navbar.css .app-header, ~81px).
       * A transform, so it composes with Toastify's own top-stacking offset
       * instead of fighting it — see the .toastify comment in source.css. */
      offset: { x: 0, y: 68 },
      style: { background: "transparent", boxShadow: "none" },
    });
    instance.showToast();
    node._ktToastInstance = instance;

    /* Hard ceiling on the hover pause above. A persistent toast (ms <= 0,
     * e.g. the session-expired one) is left alone: it closes on its action. */
    if (ms > 0) {
      instance._ktHardCap = window.setTimeout(function () {
        dismiss(instance);
      }, ms + HOVER_GRACE_MS);
    }

    /* Freshly injected <i data-lucide> elements are inert until this runs. */
    if (KeyTrack.refreshIcons) KeyTrack.refreshIcons(node);

    return instance;
  }

  function dismiss(instance) {
    if (!instance) return;
    if (instance._ktHardCap) {
      window.clearTimeout(instance._ktHardCap);
      instance._ktHardCap = null;
    }
    if (typeof instance.hideToast === "function") instance.hideToast();
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

  /* Django messages rendered by partials/messages.html after a full page
   * load (toggle, delete, bulk action, a no-JS form) are shown as toasts
   * too. Errors and warnings stay up longer: they explain why nothing
   * changed. */
  function showFlashMessages() {
    var list = document.getElementById("flash-messages");
    if (!list) return;
    var items = list.querySelectorAll("[data-flash-level]");
    for (var i = 0; i < items.length; i++) {
      var tags = (items[i].getAttribute("data-flash-level") || "").split(/\s+/);
      var level = "info";
      ["error", "warning", "success", "info"].some(function (name) {
        if (tags.indexOf(name) !== -1) { level = name; return true; }
        return false;
      });
      toast(items[i].textContent.trim(), level,
            level === "error" || level === "warning" ? { duration: 8000 } : {});
    }
    list.parentNode.removeChild(list);
  }

  /* A toast fired just before window.location.reload() is wiped by the
   * reload. reloadWithToast() parks it in sessionStorage and the next page
   * shows it. */
  var PENDING_KEY = "keytrack-pending-toast";

  function reloadWithToast(message, variant) {
    try {
      window.sessionStorage.setItem(PENDING_KEY, JSON.stringify({ m: message, v: variant || "success" }));
    } catch (e) {
      toast(message, variant); /* storage blocked: at least flash it */
    }
    window.location.reload();
  }

  function showPendingToast() {
    var raw = null;
    try {
      raw = window.sessionStorage.getItem(PENDING_KEY);
      window.sessionStorage.removeItem(PENDING_KEY);
    } catch (e) { return; }
    if (!raw) return;
    try {
      var p = JSON.parse(raw);
      if (p && p.m) toast(p.m, p.v);
    } catch (e) { /* ignore a corrupt entry */ }
  }

  function onLoad() {
    showPendingToast();
    showFlashMessages();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", onLoad);
  } else {
    onLoad();
  }

  KeyTrack.reloadWithToast = reloadWithToast;

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
