/* pos.js — guard POS borrow/return screen (Section 3).
 *
 * SCOPE NOTE (F6.9): the POS rewrite is F3.1–F3.9 and its JSON endpoints
 * (/api/borrowers/search/, /api/keys/search/, /api/transactions/borrow/) are
 * backend §3.4/§4.1/§4.2 — none of them exist in this repo yet. This file
 * therefore does two things:
 *
 *   1. Enhancements that work against TODAY's server-rendered
 *      transactions/borrow_dashboard.html: double-submit protection on the
 *      Confirm/Return buttons, the no-active-shift blocking state (F3.8), and
 *      the POS keyboard shortcuts (F3.9).
 *   2. KeyTrack.pos.* seams that the live-search rewrite plugs into once the
 *      endpoints land, so F3.2/F3.4 does not have to re-invent the debounce,
 *      the CSRF plumbing or the icon refresh.
 *
 * Loaded only by transactions/borrow_dashboard.html via {% block extra_js %}.
 *
 * Requires: csrf.js, toast.js, search.js (modal.js for the return confirm).
 */
(function () {
  "use strict";

  var KeyTrack = (window.KeyTrack = window.KeyTrack || {});

  var ENDPOINTS = {
    borrowerSearch: "/api/borrowers/search/",
    keySearch: "/api/keys/search/",
    borrow: "/api/transactions/borrow/",
  };

  var root = null;

  /* ---- F3.8 no-active-shift blocking -------------------------------- */

  /* The server rejects every borrow without an open GuardShift. Reflect that
   * before the guard fills anything in, rather than after they hit Confirm.
   * borrow_dashboard.html renders the shift banner into #shift; a data flag
   * on the POS root is the explicit signal. */
  function hasActiveShift() {
    if (!root) return true;
    if (root.dataset.shiftActive !== undefined) {
      return root.dataset.shiftActive === "1";
    }
    /* Fall back to the existing markup: the "no shift" banner is amber. */
    var banner = document.getElementById("shift");
    return !(banner && /no active shift/i.test(banner.textContent || ""));
  }

  function applyShiftBlock() {
    if (hasActiveShift()) return;

    /* Disable every release control and say why, once. */
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-pos-release], form[action*='borrow/confirm'] button"),
      function (btn) {
        btn.disabled = true;
        btn.classList.add("opacity-50", "cursor-not-allowed");
        btn.title = "You must start a shift before releasing keys.";
      }
    );

    Array.prototype.forEach.call(
      document.querySelectorAll("[data-pos-blocked-notice]"),
      function (el) {
        el.classList.remove("hidden");
      }
    );
  }

  /* ---- double-submit protection ------------------------------------- */

  /* A double-click on Confirm would issue the key twice. The service layer is
   * atomic with row locking, so the second request fails cleanly — but the
   * guard should never be able to fire it. */
  function guardDoubleSubmit() {
    document.addEventListener(
      "submit",
      function (e) {
        var form = e.target;
        if (!form.matches("form")) return;
        var action = form.getAttribute("action") || "";
        if (!/borrow\/confirm|\/return\//.test(action)) return;

        if (form.dataset.submitting === "1") {
          e.preventDefault();
          return;
        }
        form.dataset.submitting = "1";

        var btn = form.querySelector("[type=submit], button:not([type=button])");
        if (btn) {
          btn.disabled = true;
          btn.classList.add("opacity-70");
          /* This is a real navigation, so the page is about to be replaced;
           * the spinner just covers the round-trip. */
          btn.innerHTML =
            '<i data-lucide="loader-circle" class="w-4 h-4 animate-spin"></i>';
          KeyTrack.refreshIcons(btn);
        }
      },
      true
    );
  }

  /* ---- F3.9 keyboard shortcuts -------------------------------------- */

  function focusSearch(selector) {
    var el = document.querySelector(selector);
    if (!el) return false;
    el.focus();
    if (el.select) el.select();
    return true;
  }

  function isTyping(e) {
    var t = e.target;
    return (
      t &&
      (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT")
    );
  }

  function bindShortcuts() {
    document.addEventListener("keydown", function (e) {
      /* Never steal keystrokes from an open modal — modal.js owns that
       * state, so ask it rather than inferring from a body class. */
      if (window.KeyTrackModal && window.KeyTrackModal.anyOpen()) return;

      if (e.key === "F1" || (e.key === "/" && !isTyping(e))) {
        if (focusSearch("[name=q_borrower], [data-pos-borrower-search]")) {
          e.preventDefault();
        }
        return;
      }

      if (e.key === "F2") {
        if (focusSearch("[name=q_key], [data-pos-key-search]")) {
          e.preventDefault();
        }
        return;
      }

      if (e.key === "Escape" && isTyping(e) && e.target.value) {
        e.target.value = "";
        e.target.dispatchEvent(new Event("input", { bubbles: true }));
      }
    });
  }

  /* ---- responsive transaction panes ------------------------------- */

  function bindPosTabs() {
    var tabs = Array.prototype.slice.call(
      root.querySelectorAll("[data-pos-tab]")
    );
    if (!tabs.length) return;

    var initial = root.dataset.selectedBorrower === "1" ? "release" : "borrowers";

    function activate(name, focusTab) {
      tabs.forEach(function (tab) {
        var selected = tab.getAttribute("data-pos-tab") === name;
        tab.setAttribute("aria-selected", selected ? "true" : "false");
        tab.tabIndex = selected ? 0 : -1;
        if (focusTab && selected) tab.focus();
      });

      root.querySelectorAll("[data-pos-panel]").forEach(function (panel) {
        panel.classList.toggle(
          "hidden",
          panel.getAttribute("data-pos-panel") !== name
        );
      });
    }

    tabs.forEach(function (tab, index) {
      tab.addEventListener("click", function () {
        activate(tab.getAttribute("data-pos-tab"), false);
      });
      tab.addEventListener("keydown", function (event) {
        var nextIndex = index;
        if (event.key === "ArrowRight") nextIndex = (index + 1) % tabs.length;
        else if (event.key === "ArrowLeft") nextIndex = (index - 1 + tabs.length) % tabs.length;
        else if (event.key === "Home") nextIndex = 0;
        else if (event.key === "End") nextIndex = tabs.length - 1;
        else return;

        event.preventDefault();
        var next = tabs[nextIndex];
        activate(next.getAttribute("data-pos-tab"), true);
      });
    });

    activate(initial, false);
  }

  /* ---- seams for the F3.2 / F3.4 live-search rewrite ---------------- */

  /* Debounced borrower search. onResults receives the parsed JSON body.
   * Expected shape (backend §4.1): {results: [{id, full_name, id_number,
   * borrower_type, department, can_borrow, active_transaction}]}. */
  function borrowerSearch(onResults) {
    return KeyTrack.createSearch({
      url: ENDPOINTS.borrowerSearch,
      onResults: onResults,
      onError: function () {
        KeyTrack.toast("Borrower search failed.", "error");
      },
    });
  }

  /* Debounced available-key search (backend §4.2). */
  function keySearch(onResults) {
    return KeyTrack.createSearch({
      url: ENDPOINTS.keySearch,
      onResults: onResults,
      onError: function () {
        KeyTrack.toast("Key search failed.", "error");
      },
    });
  }

  /* POST a release. Resolves with the JSON body on 200/201; rejects with an
   * Error carrying .status and .body for 422/403 so the caller can render the
   * business-rule message inline (F3.5). */
  function release(payload) {
    return fetch(ENDPOINTS.borrow, {
      method: "POST",
      headers: KeyTrack.jsonHeaders(),
      body: JSON.stringify(payload),
      credentials: "same-origin",
    }).then(function (res) {
      return res
        .json()
        .catch(function () {
          return {};
        })
        .then(function (body) {
          if (res.status === 200 || res.status === 201) return body;
          var err = new Error(body.error || "Release failed.");
          err.status = res.status;
          err.body = body;
          throw err;
        });
    });
  }

  /* Render results into a container, then refresh icons — the step that is
   * forgotten most often and leaves every badge blank. */
  function renderInto(container, html) {
    container.innerHTML = html;
    KeyTrack.refreshIcons(container);
  }

  KeyTrack.pos = {
    endpoints: ENDPOINTS,
    hasActiveShift: hasActiveShift,
    borrowerSearch: borrowerSearch,
    keySearch: keySearch,
    release: release,
    renderInto: renderInto,
  };

  document.addEventListener("DOMContentLoaded", function () {
    root = document.querySelector("[data-pos-root]");
    applyShiftBlock();
    guardDoubleSubmit();
    bindShortcuts();
    if (root) bindPosTabs();
  });
})();
