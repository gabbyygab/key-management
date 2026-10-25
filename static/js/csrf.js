/* csrf.js — CSRF token helper + icon refresh + auth/session helper (F6.8).
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
  var ICON_ATTRS = { focusable: "false" };

  function refreshIcons(root) {
    if (typeof window.lucide === "undefined") return;
    window.lucide.createIcons(
      root ? { root: root, attrs: ICON_ATTRS } : { attrs: ICON_ATTRS }
    );
  }

  /* F6.8: Check whether a fetch response indicates an expired or unauthorized session */
  function isSessionExpired(res, data) {
    if (!res) return false;
    // 401 Unauthorized
    if (res.status === 401) return true;

    // Followed redirect to login page
    if (res.redirected) {
      var u = res.url || "";
      if (u.indexOf("/login") !== -1 || u.indexOf("/accounts/login") !== -1) {
        return true;
      }
    }

    // 403 Forbidden with session expired message
    if (res.status === 403) {
      if (data && typeof data === "object" && data.error) {
        if (/session.*expired|sign.*in|log.*in/i.test(String(data.error))) {
          return true;
        }
      }
      // If server returned HTML login page directly
      if (typeof data === "string" && (data.indexOf('name="username"') !== -1 || data.indexOf("id_username") !== -1)) {
        return true;
      }
    }

    // 200 OK but response is the HTML login page because fetch followed a redirect
    if (res.status === 200 && typeof data === "string") {
      if (data.indexOf('name="username"') !== -1 && data.indexOf('name="password"') !== -1 && data.indexOf("KEYTRACK") !== -1) {
        return true;
      }
    }

    return false;
  }

  /* F6.8: Fallback modal injection if session-expired-modal is not in the DOM */
  function createSessionExpiredModal(loginUrl) {
    var existing = document.getElementById("session-expired-modal");
    if (existing) {
      var btn = existing.querySelector("[data-session-login-btn]");
      if (btn) btn.href = loginUrl;
      if (window.KeyTrackModal) window.KeyTrackModal.open("session-expired-modal");
      return existing;
    }

    var el = document.createElement("div");
    el.id = "session-expired-modal";
    el.className = "hidden fixed inset-0 z-[60] flex items-center justify-center p-4";
    el.setAttribute("role", "alertdialog");
    el.setAttribute("aria-modal", "true");
    el.setAttribute("aria-labelledby", "session-expired-modal-title");
    el.setAttribute("aria-describedby", "session-expired-modal-desc");
    el.innerHTML =
      '<div data-modal-overlay class="absolute inset-0 bg-stone-900/60"></div>' +
      '<div class="relative bg-white rounded-2xl border border-stone-200 shadow-xl w-full max-w-sm p-6">' +
        '<div class="flex items-start gap-3">' +
          '<div class="w-10 h-10 rounded-xl bg-amber-50 border border-amber-200 flex items-center justify-center shrink-0">' +
            '<i data-lucide="shield-alert" class="w-5 h-5 text-amber-600"></i>' +
          '</div>' +
          '<div class="min-w-0">' +
            '<h2 id="session-expired-modal-title" class="text-[15px] font-bold text-stone-900">Session expired</h2>' +
            '<p id="session-expired-modal-desc" class="text-[13px] text-stone-500 mt-1">Your session has timed out or you are no longer signed in. Please log in again to continue.</p>' +
          '</div>' +
        '</div>' +
        '<div class="flex items-center justify-end gap-2 mt-5">' +
          '<button type="button" data-modal-close class="px-4 py-2 rounded-lg border border-stone-200 bg-white text-[13px] font-semibold text-stone-700 hover:bg-stone-50">Dismiss</button>' +
          '<a href="' + loginUrl + '" data-session-login-btn class="px-4 py-2 rounded-lg bg-[#0F2E1F] text-white text-[13px] font-semibold hover:bg-[#143d28] inline-flex items-center gap-2">' +
            '<i data-lucide="log-in" class="w-4 h-4"></i> Log in again' +
          '</a>' +
        '</div>' +
      '</div>';
    document.body.appendChild(el);
    refreshIcons(el);
    if (window.KeyTrackModal) window.KeyTrackModal.open("session-expired-modal");
    return el;
  }

  /* F6.8: Provide visible "session expired, please log in again" path */
  function handleSessionExpired(options) {
    var opts = options || {};
    var next = opts.next || (window.location.pathname + window.location.search);
    var loginUrl = "/accounts/login/?next=" + encodeURIComponent(next);

    // Close any other open modal so the alert dialog is visible and accessible
    if (window.KeyTrackModal && window.KeyTrackModal.anyOpen) {
      if (!window.KeyTrackModal.isOpen("session-expired-modal")) {
        while (window.KeyTrackModal.anyOpen()) {
          window.KeyTrackModal.closeTop();
        }
      }
    }

    var modal = document.getElementById("session-expired-modal");
    if (modal && window.KeyTrackModal) {
      var loginBtn = modal.querySelector("[data-session-login-btn]");
      if (loginBtn) loginBtn.setAttribute("href", loginUrl);
      window.KeyTrackModal.open("session-expired-modal");
    } else {
      createSessionExpiredModal(loginUrl);
    }

    // Always show persistent toast as well
    if (KeyTrack.sessionExpiredToast) {
      KeyTrack.sessionExpiredToast(loginUrl);
    }
  }

  KeyTrack.getCsrfToken = getCsrfToken;
  KeyTrack.jsonHeaders = jsonHeaders;
  KeyTrack.formHeaders = formHeaders;
  KeyTrack.refreshIcons = refreshIcons;
  KeyTrack.isSessionExpired = isSessionExpired;
  KeyTrack.handleSessionExpired = handleSessionExpired;

  /* Backwards-compatible bare global, as spelled in GUIDE.md §5. */
  window.getCsrfToken = getCsrfToken;
})();

/* postJson: fetch() with the headers Django expects on a JSON POST.
 * Used by the currently-out panel / return flow. Built on KeyTrack.jsonHeaders. */
function postJson(url, payload) {
  return fetch(url, {
    method: "POST",
    headers: window.KeyTrack.jsonHeaders(),
    body: JSON.stringify(payload || {}),
  }).then(function (res) {
    if (window.KeyTrack.isSessionExpired(res)) {
      window.KeyTrack.handleSessionExpired();
      var err = new Error("session_expired");
      err.status = res.status;
      throw err;
    }
    return res;
  });
}
