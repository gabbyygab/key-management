/* pos.js — guard POS borrow/return screen (Section 3, F6.8).
 *
 * Provides:
 *   - Live debounced search (275ms) for borrowers and available keys
 *   - Loading skeletons for live search results
 *   - Double-submit protection on Confirm / Return buttons
 *   - No-active-shift blocking state (F3.8)
 *   - POS keyboard shortcuts (F3.9)
 *   - Network-failure toast with retry & session expired handling (F6.8)
 *   - KeyTrack.pos.* helper seams for programmatic search/release
 *
 * Loaded only by transactions/borrow_dashboard.html via {% block extra_js %}.
 *
 * Requires: csrf.js, toast.js, skeletons.js, search.js (modal.js for the return confirm).
 */
(function () {
  "use strict";

  var KeyTrack = (window.KeyTrack = window.KeyTrack || {});

  var SEARCH_DEBOUNCE_MS = 275; /* strictly within 250–300ms band */

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
      if (window.KeyTrackModal && window.KeyTrackModal.anyOpen && window.KeyTrackModal.anyOpen()) return;

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

  /* ---- F6.8 Live debounced POS search with loading skeletons -------- */

  function bindLivePosSearch() {
    var borrowerInput = document.querySelector("#pos-q-borrower, [data-pos-borrower-search]");
    var borrowerResults = document.querySelector("[data-pos-borrower-results], [aria-label='Borrower search results']");
    var borrowerForm = document.querySelector("[data-pos-borrower-form], form[aria-label='Search for a borrower']");

    var keyInput = document.querySelector("#pos-q-key, [data-pos-key-search]");
    var keyResults = document.querySelector("[data-pos-key-results], [aria-label='Available keys']");
    var keyForm = document.querySelector("[data-pos-key-form], form[aria-label='Search available keys']");

    var borrowerController = null;
    var keyController = null;

    /* Live borrower search */
    if (borrowerInput && borrowerResults) {
      var fetchBorrowers = KeyTrack.debounce(function (q) {
        if (borrowerController) borrowerController.abort();
        borrowerController = new AbortController();

        // Render loading skeleton
        if (KeyTrack.skeletons) {
          KeyTrack.skeletons.posBorrower(borrowerResults, 4);
        } else {
          borrowerResults.setAttribute("aria-busy", "true");
        }

        var url = new URL(window.location.href);
        url.searchParams.set("q_borrower", q);

        fetch(url.toString(), {
          headers: { "X-Requested-With": "XMLHttpRequest" },
          signal: borrowerController.signal,
          credentials: "same-origin",
        })
          .then(function (res) {
            if (KeyTrack.isSessionExpired && KeyTrack.isSessionExpired(res)) {
              KeyTrack.handleSessionExpired();
              throw new Error("session_expired");
            }
            if (res.status === 403) {
              if (KeyTrack.toast) KeyTrack.toast("You do not have permission.", "error");
              throw new Error("forbidden");
            }
            if (!res.ok) throw new Error("search failed: " + res.status);
            return res.text().then(function (html) {
              return { res: res, html: html };
            });
          })
          .then(function (pair) {
            if (KeyTrack.isSessionExpired && KeyTrack.isSessionExpired(pair.res, pair.html)) {
              KeyTrack.handleSessionExpired();
              throw new Error("session_expired");
            }
            var doc = new DOMParser().parseFromString(pair.html, "text/html");
            var fresh = doc.querySelector("[data-pos-borrower-results], [aria-label='Borrower search results']");
            if (fresh) {
              borrowerResults.innerHTML = fresh.innerHTML;
              KeyTrack.refreshIcons(borrowerResults);
            }
            if (window.history && window.history.replaceState) {
              window.history.replaceState(null, "", url.toString());
            }
          })
          .catch(function (err) {
            if (err.name === "AbortError" || err.message === "session_expired") return;
            if (KeyTrack.networkErrorToast) {
              KeyTrack.networkErrorToast(function () {
                fetchBorrowers(q);
              }, "Could not search borrowers due to network error.");
            }
          })
          .finally(function () {
            borrowerResults.removeAttribute("aria-busy");
          });
      }, SEARCH_DEBOUNCE_MS);

      borrowerInput.addEventListener("input", function () {
        fetchBorrowers(borrowerInput.value.trim());
      });

      if (borrowerForm) {
        borrowerForm.addEventListener("submit", function (e) {
          e.preventDefault();
          fetchBorrowers.cancel();
          fetchBorrowers(borrowerInput.value.trim());
        });
      }
    }

    /* Live available keys search */
    if (keyInput && keyResults) {
      var fetchKeys = KeyTrack.debounce(function (q) {
        if (keyController) keyController.abort();
        keyController = new AbortController();

        // Render loading skeleton
        if (KeyTrack.skeletons) {
          KeyTrack.skeletons.posKey(keyResults, 3);
        } else {
          keyResults.setAttribute("aria-busy", "true");
        }

        var url = new URL(window.location.href);
        url.searchParams.set("q_key", q);

        fetch(url.toString(), {
          headers: { "X-Requested-With": "XMLHttpRequest" },
          signal: keyController.signal,
          credentials: "same-origin",
        })
          .then(function (res) {
            if (KeyTrack.isSessionExpired && KeyTrack.isSessionExpired(res)) {
              KeyTrack.handleSessionExpired();
              throw new Error("session_expired");
            }
            if (res.status === 403) {
              if (KeyTrack.toast) KeyTrack.toast("You do not have permission.", "error");
              throw new Error("forbidden");
            }
            if (!res.ok) throw new Error("search failed: " + res.status);
            return res.text().then(function (html) {
              return { res: res, html: html };
            });
          })
          .then(function (pair) {
            if (KeyTrack.isSessionExpired && KeyTrack.isSessionExpired(pair.res, pair.html)) {
              KeyTrack.handleSessionExpired();
              throw new Error("session_expired");
            }
            var doc = new DOMParser().parseFromString(pair.html, "text/html");
            var fresh = doc.querySelector("[data-pos-key-results], [aria-label='Available keys']");
            if (fresh) {
              keyResults.innerHTML = fresh.innerHTML;
              KeyTrack.refreshIcons(keyResults);
            }
            if (window.history && window.history.replaceState) {
              window.history.replaceState(null, "", url.toString());
            }
          })
          .catch(function (err) {
            if (err.name === "AbortError" || err.message === "session_expired") return;
            if (KeyTrack.networkErrorToast) {
              KeyTrack.networkErrorToast(function () {
                fetchKeys(q);
              }, "Could not search keys due to network error.");
            }
          })
          .finally(function () {
            keyResults.removeAttribute("aria-busy");
          });
      }, SEARCH_DEBOUNCE_MS);

      keyInput.addEventListener("input", function () {
        fetchKeys(keyInput.value.trim());
      });

      if (keyForm) {
        keyForm.addEventListener("submit", function (e) {
          e.preventDefault();
          fetchKeys.cancel();
          fetchKeys(keyInput.value.trim());
        });
      }
    }
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
      delay: SEARCH_DEBOUNCE_MS,
      onResults: onResults,
      onError: function (err) {
        if (KeyTrack.networkErrorToast) {
          KeyTrack.networkErrorToast(function () {
            borrowerSearch(onResults);
          }, "Borrower search failed due to network error.");
        } else {
          KeyTrack.toast("Borrower search failed.", "error");
        }
      },
    });
  }

  /* Debounced available-key search (backend §4.2). */
  function keySearch(onResults) {
    return KeyTrack.createSearch({
      url: ENDPOINTS.keySearch,
      delay: SEARCH_DEBOUNCE_MS,
      onResults: onResults,
      onError: function (err) {
        if (KeyTrack.networkErrorToast) {
          KeyTrack.networkErrorToast(function () {
            keySearch(onResults);
          }, "Key search failed due to network error.");
        } else {
          KeyTrack.toast("Key search failed.", "error");
        }
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
    })
      .then(function (res) {
        return res
          .json()
          .catch(function () {
            return {};
          })
          .then(function (body) {
            if (KeyTrack.isSessionExpired && KeyTrack.isSessionExpired(res, body)) {
              KeyTrack.handleSessionExpired();
              var authErr = new Error("session_expired");
              authErr.status = res.status;
              throw authErr;
            }
            if (res.status === 200 || res.status === 201) return body;
            var err = new Error(body.error || "Release failed.");
            err.status = res.status;
            err.body = body;
            throw err;
          });
      })
      .catch(function (err) {
        if (err.message === "session_expired") throw err;
        if (!err.status && KeyTrack.networkErrorToast) {
          KeyTrack.networkErrorToast(function () {
            release(payload);
          }, "Release failed due to network error.");
        }
        throw err;
      });
  }

  /* Render results into a container, then refresh icons — the step that is
   * forgotten most often and leaves every badge blank. */
  function renderInto(container, html) {
    container.innerHTML = html;
    KeyTrack.refreshIcons(container);
  }

  /* ---- F5.6 Room schedule on key selection -------------------------- */

  function loadRoomSchedule(panel, roomId) {
    if (!panel || !roomId) return;
    if (panel.querySelector(".today-in-room-widget")) return;

    panel.innerHTML =
      '<div class="py-2.5 text-[12px] text-stone-500 flex items-center gap-2">' +
      '<i data-lucide="loader-circle" class="w-4 h-4 animate-spin text-stone-400"></i>' +
      '<span>Loading today\'s schedule for this room...</span>' +
      '</div>';
    KeyTrack.refreshIcons(panel);

    fetch("/api/scheduling/rooms/" + roomId + "/today/", {
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    })
      .then(function (res) {
        if (!res.ok) throw new Error("Status: " + res.status);
        return res.json();
      })
      .then(function (data) {
        if (data.html) {
          panel.innerHTML = data.html;
          KeyTrack.refreshIcons(panel);
        }
      })
      .catch(function () {
        panel.innerHTML =
          '<div class="py-2 text-[12px] text-stone-400 flex items-center gap-1.5">' +
          '<i data-lucide="alert-circle" class="w-3.5 h-3.5 text-stone-400"></i>' +
          '<span>Unable to load room schedule.</span>' +
          '</div>';
        KeyTrack.refreshIcons(panel);
      });
  }

  function bindRoomScheduleToggle() {
    document.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-pos-toggle-schedule]");
      if (!btn) return;
      var keyId = btn.getAttribute("data-pos-toggle-schedule");
      var panel = document.querySelector("[data-pos-schedule-panel='" + keyId + "']");
      if (!panel) return;

      var isHidden = panel.classList.contains("hidden");
      if (isHidden) {
        panel.classList.remove("hidden");
        btn.setAttribute("aria-expanded", "true");
        var roomId = panel.getAttribute("data-room-id");
        loadRoomSchedule(panel, roomId);
      } else {
        panel.classList.add("hidden");
        btn.setAttribute("aria-expanded", "false");
      }
    });

    document.addEventListener("focusin", function (e) {
      var input = e.target.closest("[data-pos-key-input]");
      if (!input) return;
      var keyId = input.getAttribute("data-pos-key-input");
      var card = document.querySelector("[data-pos-key-card='" + keyId + "']");
      if (card) {
        document.querySelectorAll("[data-pos-key-card]").forEach(function (c) {
          c.classList.remove("bg-emerald-50/40", "border-emerald-300", "ring-1", "ring-emerald-300");
        });
        card.classList.add("bg-emerald-50/40", "border-emerald-300", "ring-1", "ring-emerald-300");
      }
    });
  }

  KeyTrack.pos = {
    endpoints: ENDPOINTS,
    hasActiveShift: hasActiveShift,
    borrowerSearch: borrowerSearch,
    keySearch: keySearch,
    release: release,
    renderInto: renderInto,
    bindLivePosSearch: bindLivePosSearch,
    loadRoomSchedule: loadRoomSchedule,
    bindRoomScheduleToggle: bindRoomScheduleToggle,
  };

  document.addEventListener("DOMContentLoaded", function () {
    root = document.querySelector("[data-pos-root]");
    applyShiftBlock();
    guardDoubleSubmit();
    bindShortcuts();
    bindLivePosSearch();
    bindRoomScheduleToggle();
    if (root) bindPosTabs();
  });
})();
