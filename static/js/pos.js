/* pos.js — guard POS borrow screen (Section 3, F3.2–F3.9, F6.8).
 *
 * Progressive enhancement over transactions/borrow_dashboard.html. Without JS
 * the page is a set of GET searches and a POST to borrow_confirm. With JS:
 *
 *   1. Borrower search  — GET /api/borrowers/search/?q=   (live, debounced)
 *   2. Key search       — GET /api/keys/search/?q=&building= (live; scanner
 *                         Enter picks the exact code match)
 *   3. Confirm panel    — GET /api/keys/<id>/?borrower=   (schedule check)
 *                         POST /api/transactions/borrow/  (release)
 *      A 422 with schedule_warning opens the typed-override box (F3.6).
 *   4. Currently out    — refreshed via KeyTrackCurrentlyOut.refresh().
 *
 * Also: no-shift blocking (F3.8), shortcuts + help modal (F3.9), double-submit
 * protection, session-expired + network-retry handling (F6.8), mobile tabs.
 *
 * Requires: csrf.js, toast.js, skeletons.js, search.js, modal.js.
 */
(function () {
  "use strict";

  var KeyTrack = (window.KeyTrack = window.KeyTrack || {});
  var SEARCH_DEBOUNCE_MS = 275; /* within the 250–300ms band */

  var root = null;
  var ENDPOINTS = {};
  var state = { borrower: null, key: null, keys: [], borrowers: [], building: "", releasing: false };
  var el = {};

  /* ---- helpers ------------------------------------------------------- */

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function hasActiveShift() {
    return !root || root.dataset.shiftActive === "1";
  }

  /* fetch JSON with the shared session / permission handling. Resolves
   * {status, body}; rejects only for network errors and aborts. */
  function getJSON(url, signal) {
    return fetch(url, {
      headers: { "X-Requested-With": "XMLHttpRequest", Accept: "application/json" },
      credentials: "same-origin",
      signal: signal,
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (body) {
        if (KeyTrack.isSessionExpired && KeyTrack.isSessionExpired(res, body)) {
          KeyTrack.handleSessionExpired();
          var e = new Error("session_expired");
          throw e;
        }
        return { status: res.status, body: body };
      });
    });
  }

  function handleFetchError(err, retry, what) {
    if (!err || err.name === "AbortError" || err.message === "session_expired") return;
    if (KeyTrack.networkErrorToast) KeyTrack.networkErrorToast(retry, what + " failed due to a network error.");
    else KeyTrack.toast(what + " failed. Check your connection.", "error");
  }

  /* "2026-10-31T17:30:00+08:00" -> "2026-10-31T17:30". The server sends its
   * own local wall time and reads naive values back in that zone, so keeping
   * the wall-clock part is correct whatever the browser's time zone is. */
  function toLocalInput(iso) {
    return iso ? String(iso).slice(0, 16) : "";
  }

  /* ---- mobile tabs --------------------------------------------------- */

  var activateTab = function () {};

  function bindPosTabs() {
    var tabs = Array.prototype.slice.call(root.querySelectorAll("[data-pos-tab]"));
    if (!tabs.length) return;

    activateTab = function (name, focusTab) {
      tabs.forEach(function (tab) {
        var on = tab.getAttribute("data-pos-tab") === name;
        tab.setAttribute("aria-selected", on ? "true" : "false");
        tab.tabIndex = on ? 0 : -1;
        if (focusTab && on) tab.focus();
      });
      root.querySelectorAll("[data-pos-panel]").forEach(function (panel) {
        panel.classList.toggle("hidden", panel.getAttribute("data-pos-panel") !== name);
      });
    };

    tabs.forEach(function (tab, index) {
      tab.addEventListener("click", function () { activateTab(tab.getAttribute("data-pos-tab"), false); });
      tab.addEventListener("keydown", function (e) {
        var next = index;
        if (e.key === "ArrowRight") next = (index + 1) % tabs.length;
        else if (e.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
        else if (e.key === "Home") next = 0;
        else if (e.key === "End") next = tabs.length - 1;
        else return;
        e.preventDefault();
        activateTab(tabs[next].getAttribute("data-pos-tab"), true);
      });
    });

    activateTab(root.dataset.selectedBorrower === "1" ? "release" : "borrowers", false);
  }

  /* ---- 1 · borrower search ------------------------------------------- */

  var borrowerController = null;

  function searchBorrowers(q) {
    if (borrowerController) borrowerController.abort();
    borrowerController = new AbortController();
    if (KeyTrack.skeletons) KeyTrack.skeletons.posBorrower(el.borrowerResults, 4);
    el.borrowerResults.setAttribute("aria-busy", "true");

    return getJSON(ENDPOINTS.borrowerSearch + "?q=" + encodeURIComponent(q), borrowerController.signal)
      .then(function (r) {
        if (r.status === 403) { KeyTrack.toast("You do not have permission.", "error"); return; }
        state.borrowers = r.body.results || [];
        renderBorrowers(q);
      })
      .catch(function (err) { handleFetchError(err, function () { searchBorrowers(q); }, "Borrower search"); })
      .then(function () { el.borrowerResults.removeAttribute("aria-busy"); });
  }

  function renderBorrowers(q) {
    if (!state.borrowers.length) {
      el.borrowerResults.innerHTML = '<p class="px-2 py-3 text-[13px] text-stone-500">' +
        (q ? "No borrower matches “" + esc(q) + "”." : "Search by name or ID to find a borrower.") + "</p>";
      return;
    }
    el.borrowerResults.innerHTML = state.borrowers.map(function (b, i) {
      var badge = "";
      if (!b.can_borrow) {
        badge = '<span class="rounded border border-red-200 bg-red-50 px-1.5 py-0.5 text-[11px] font-semibold text-red-800">' +
          (b.is_authorized ? "Authorization expired" : "Not authorized") + "</span>";
      } else if (b.active_transaction) {
        badge = '<span class="rounded border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-[11px] font-semibold text-blue-800">Has ' +
          esc(b.active_transaction.key_code) + "</span>";
      }
      var current = state.borrower && state.borrower.id === b.id;
      return '<button type="button" data-pos-pick-borrower="' + i + '"' + (current ? ' aria-current="true"' : "") +
        ' class="flex w-full items-center justify-between gap-2 rounded-lg px-2 py-3 text-left hover:bg-stone-50 focus:bg-stone-50 focus:outline-none focus:ring-2 focus:ring-[#1E6B4A]' +
        (current ? " bg-emerald-50/60" : "") + '">' +
        '<span class="min-w-0 text-[13px]"><b class="block truncate">' + esc(b.last_name) + ", " + esc(b.first_name) + "</b>" +
        '<span class="font-mono text-[12px] text-stone-500">' + esc(b.id_number) + "</span>" +
        '<span class="text-[12px] text-stone-400"> · ' + esc(b.borrower_type_display) + (b.department ? " · " + esc(b.department) : "") + "</span></span>" +
        '<span class="flex shrink-0 items-center gap-1.5">' + badge +
        '<i data-lucide="chevron-right" aria-hidden="true" class="h-4 w-4 text-stone-400"></i></span></button>';
    }).join("");
    KeyTrack.refreshIcons(el.borrowerResults);
  }

  /* ---- 2 · borrower card + key search -------------------------------- */

  function selectBorrower(b) {
    state.borrower = b;
    clearKey();
    renderBorrowers(el.borrowerInput.value.trim());

    var card = '<div class="flex items-center gap-2 rounded-lg bg-[#FAF7F0] px-3 py-2 text-[13px]">' +
      '<i data-lucide="user-round" aria-hidden="true" class="h-4 w-4 shrink-0 text-stone-500"></i>' +
      '<span class="min-w-0 flex-1"><span class="block truncate font-semibold">' + esc(b.full_name) + "</span>" +
      '<span class="font-mono text-[12px] text-stone-500">' + esc(b.id_number) + "</span>" +
      '<span class="text-[12px] text-stone-400"> · ' + esc(b.borrower_type_display) + (b.department ? " · " + esc(b.department) : "") + "</span></span>" +
      '<button type="button" data-pos-clear-borrower class="shrink-0 rounded-lg border border-stone-200 bg-white px-2 py-1 text-[12px] text-stone-600 hover:bg-stone-50">Change</button></div>';

    var blocked = "";
    if (!b.can_borrow) {
      blocked = '<p role="alert" class="mt-2 rounded-lg border border-red-200 bg-red-50 p-3 text-[13px] text-red-800">' +
        (b.is_authorized
          ? "This borrower's authorization expired on " + esc(b.authorized_until) + ". An admin must renew it before they can borrow."
          : "This borrower is not authorized to borrow keys.") + "</p>";
    } else if (b.active_transaction) {
      var t = b.active_transaction;
      blocked = '<p role="alert" class="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-[13px] text-amber-900">' +
        "Already holding <b class=\"font-mono\">" + esc(t.key_code) + "</b> (" + esc(t.room) + ", " + esc(t.transaction_no) + ")" +
        (t.status === "OVERDUE" ? ", which is <b>overdue</b>" : "") +
        ". Receive it in “Currently out” before releasing another key.</p>";
    }

    el.borrowerCard.innerHTML = card + blocked;
    KeyTrack.refreshIcons(el.borrowerCard);
    el.keyArea.classList.toggle("hidden", !!blocked);
    el.keyArea.classList.toggle("flex", !blocked);
    activateTab("release", false);

    if (!blocked) {
      el.keyInput.value = "";
      searchKeys("");
      el.keyInput.focus();
    }
  }

  function clearBorrower() {
    state.borrower = null;
    clearKey();
    el.borrowerCard.innerHTML = '<p class="text-[13px] text-stone-500">Choose a borrower in the first pane to begin.</p>';
    el.keyArea.classList.add("hidden");
    el.keyArea.classList.remove("flex");
    renderBorrowers(el.borrowerInput.value.trim());
    activateTab("borrowers", false);
    el.borrowerInput.focus();
    el.borrowerInput.select();
  }

  var keyController = null;

  function searchKeys(q, opts) {
    opts = opts || {};
    if (keyController) keyController.abort();
    keyController = new AbortController();
    if (KeyTrack.skeletons) KeyTrack.skeletons.posKey(el.keyResults, 3);
    el.keyResults.setAttribute("aria-busy", "true");

    var url = ENDPOINTS.keySearch + "?q=" + encodeURIComponent(q) +
      (state.building ? "&building=" + encodeURIComponent(state.building) : "");

    return getJSON(url, keyController.signal)
      .then(function (r) {
        if (r.status === 403) { KeyTrack.toast("You do not have permission.", "error"); return; }
        state.keys = r.body.results || [];
        renderKeys(q);
        if (opts.pick) {
          /* Scanner / Enter: the exact code match wins, else a lone result. */
          var pick = null;
          if (r.body.exact) pick = state.keys.filter(function (k) { return k.id === r.body.exact; })[0];
          else if (state.keys.length === 1) pick = state.keys[0];
          if (pick) selectKey(pick);
          else if (q && !state.keys.length) KeyTrack.toast("No available key matches “" + q + "”.", "error");
        }
      })
      .catch(function (err) { handleFetchError(err, function () { searchKeys(q, opts); }, "Key search"); })
      .then(function () { el.keyResults.removeAttribute("aria-busy"); });
  }

  function renderKeys(q) {
    if (!state.keys.length) {
      el.keyResults.innerHTML = '<p class="rounded-lg bg-stone-50 p-3 text-[13px] text-stone-500">' +
        (q || state.building ? "No available key matches this search." : "No keys are available right now.") + "</p>";
      return;
    }
    el.keyResults.innerHTML = state.keys.map(function (k, i) {
      var picked = state.key && state.key.id === k.id;
      return '<button type="button" data-pos-pick-key="' + i + '"' + (picked ? ' aria-current="true"' : "") +
        ' class="flex w-full items-center gap-3 rounded-xl border p-3 text-left text-[13px] transition-colors focus:outline-none focus:ring-2 focus:ring-[#1E6B4A] ' +
        (picked ? "border-emerald-300 bg-emerald-50/50 ring-1 ring-emerald-300" : "border-stone-200 hover:border-stone-300 hover:bg-stone-50") + '">' +
        '<i data-lucide="key-round" aria-hidden="true" class="h-4 w-4 shrink-0 text-[#1E6B4A]"></i>' +
        '<span class="min-w-0 flex-1"><b class="font-mono text-stone-900">' + esc(k.key_code) + "</b>" +
        '<span class="text-stone-500"> · ' + esc(k.room) + (k.room_name ? " " + esc(k.room_name) : "") + "</span>" +
        '<span class="block text-[12px] text-stone-400">' + esc(k.category) + (k.is_original ? "" : " · copy " + esc(k.copy_number)) + "</span></span>" +
        '<span class="shrink-0 text-[12px] font-semibold text-[#1E6B4A]">Select</span></button>';
    }).join("");
    KeyTrack.refreshIcons(el.keyResults);
  }

  /* ---- 3 · confirm panel --------------------------------------------- */

  function setOverride(show, message) {
    el.override.classList.toggle("hidden", !show);
    el.overrideMessage.textContent = message || "";
    el.overrideInput.required = !!show;
    if (!show) el.overrideInput.value = "";
  }

  function showError(msg) {
    el.confirmError.textContent = msg || "";
    el.confirmError.classList.toggle("hidden", !msg);
  }

  function setReleaseEnabled(on, why) {
    el.releaseBtn.disabled = !on;
    el.releaseBtn.title = on ? "" : (why || "");
  }

  function scheduleBox(kind, html) {
    var styles = {
      ok: "border-green-200 bg-green-50 text-green-800",
      warn: "border-amber-300 bg-amber-50 text-amber-900",
      block: "border-red-200 bg-red-50 text-red-800",
      info: "border-stone-200 bg-stone-50 text-stone-700",
    };
    el.confirmSchedule.className = "mt-3 rounded-lg border px-3 py-2 text-[13px] " + styles[kind];
    el.confirmSchedule.innerHTML = html;
    el.confirmSchedule.classList.remove("hidden");
  }

  function describeSchedule(s, borrowerName) {
    var nextLine = s.next ? '<span class="block text-[12px] opacity-80">Next: ' + esc(s.next.title) + " · " + esc(s.next.time_display) + "</span>" : "";
    if (s.state === "holder") {
      scheduleBox("ok", "<b>" + esc(borrowerName) + "</b> is scheduled here now: " + esc(s.current.title) + " · " + esc(s.current.time_display) + "." + nextLine);
      return "ok";
    }
    if (s.state === "other") {
      var who = s.current.instructor ? esc(s.current.instructor) : "another class";
      var line = "Room is scheduled to <b>" + who + "</b> right now: " + esc(s.current.title) + " · " + esc(s.current.time_display) + ".";
      if (s.enforcement === "block") {
        scheduleBox("block", line + '<span class="block mt-1 font-semibold">Release is blocked by the schedule policy. Ask an admin if this is wrong.</span>');
        return "block";
      }
      if (s.enforcement === "warn") {
        scheduleBox("warn", line + nextLine);
        return "warn";
      }
      scheduleBox("info", line + nextLine);
      return "ok";
    }
    if (s.no_timetable) {
      scheduleBox("info", "No timetable has been generated for " + esc(s.term || "this semester") + ", so there is nothing to check this room against.");
      return "ok";
    }
    scheduleBox("ok", "Room is free right now." + nextLine);
    return "ok";
  }

  var detailController = null;

  function selectKey(k) {
    state.key = k;
    renderKeys(el.keyInput.value.trim());
    showError("");
    setOverride(false);
    el.confirmKey.textContent = k.key_code;
    el.confirmRoom.textContent = "· " + k.room + (k.room_name ? " " + k.room_name : "");
    el.confirmBorrower.textContent = state.borrower.full_name;
    el.purpose.value = "";
    el.due.value = "";
    el.confirm.classList.remove("hidden");
    el.confirmSchedule.classList.add("hidden");
    setReleaseEnabled(hasActiveShift(), "You must start a shift before releasing keys.");
    el.confirm.scrollIntoView({ block: "nearest" });
    el.purpose.focus();

    if (detailController) detailController.abort();
    detailController = new AbortController();
    var url = ENDPOINTS.keyDetail.replace("/0/", "/" + encodeURIComponent(k.id) + "/") +
      "?borrower=" + encodeURIComponent(state.borrower.id);
    getJSON(url, detailController.signal)
      .then(function (r) {
        if (r.status !== 200 || state.key !== k) return;
        var s = r.body.schedule || {};
        var verdict = describeSchedule(s, state.borrower.full_name);
        if (s.expected_return_at && !el.due.value) el.due.value = toLocalInput(s.expected_return_at);
        if (verdict === "warn") {
          setOverride(true, "This room is scheduled to someone else. Give a reason to release it anyway.");
        } else if (verdict === "block") {
          setReleaseEnabled(false, "Blocked by the room schedule.");
        }
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") return;
        /* Not fatal: the server re-checks the schedule on release. */
        scheduleBox("info", "Couldn't load today's schedule for this room. The release will still be checked.");
      });
  }

  function clearKey() {
    state.key = null;
    if (detailController) detailController.abort();
    if (el.confirm) el.confirm.classList.add("hidden");
    if (el.keyResults && state.keys.length) renderKeys(el.keyInput.value.trim());
  }

  function release(payload) {
    return fetch(ENDPOINTS.borrow, {
      method: "POST",
      headers: KeyTrack.jsonHeaders(),
      body: JSON.stringify(payload),
      credentials: "same-origin",
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (body) {
        if (KeyTrack.isSessionExpired && KeyTrack.isSessionExpired(res, body)) {
          KeyTrack.handleSessionExpired();
          throw new Error("session_expired");
        }
        return { status: res.status, body: body };
      });
    });
  }

  function submitRelease() {
    if (state.releasing || !state.key || !state.borrower) return;
    if (!hasActiveShift()) {
      showError("You must start a shift before releasing keys. Log out and back in to start one.");
      return;
    }
    var needsReason = !el.override.classList.contains("hidden");
    var reason = el.overrideInput.value.trim();
    if (needsReason && reason.length < 5) {
      showError("Type a reason (at least 5 characters) to override the schedule.");
      el.overrideInput.focus();
      return;
    }

    state.releasing = true;
    showError("");
    setReleaseEnabled(false);
    var label = el.releaseLabel.textContent;
    el.releaseLabel.textContent = "Releasing…";

    var payload = {
      borrower_id: state.borrower.id,
      key_id: state.key.id,
      purpose: el.purpose.value.trim(),
      expected_return_at: el.due.value || null,
      override_reason: needsReason ? reason : "",
    };

    release(payload)
      .then(function (r) {
        if (r.status === 201 || r.status === 200) {
          var b = r.body;
          KeyTrack.toast("Released " + b.key_code + " to " + b.borrower + " (" + b.transaction_no + ")" +
                         (b.overridden ? " — schedule override recorded." : "."), "success");
          if (window.KeyTrackCurrentlyOut) window.KeyTrackCurrentlyOut.refresh();
          /* Ready for the next person in the queue. */
          el.borrowerInput.value = "";
          state.borrowers = [];
          clearBorrower();
          return;
        }
        if (r.status === 403) {
          KeyTrack.toast("You do not have permission.", "error");
          return;
        }
        if (r.body.schedule_warning) {
          if (r.body.schedule) describeSchedule(Object.assign({ enforcement: "warn" }, r.body.schedule), state.borrower.full_name);
          setOverride(true, r.body.error);
          el.overrideInput.focus();
          return;
        }
        showError(r.body.error || "The key could not be released.");
        /* The key may have just gone to someone else: refresh the list. */
        searchKeys(el.keyInput.value.trim());
      })
      .catch(function (err) {
        if (err && err.message === "session_expired") return;
        handleFetchError(err, submitRelease, "Release");
      })
      .then(function () {
        state.releasing = false;
        el.releaseLabel.textContent = label;
        if (state.key) setReleaseEnabled(hasActiveShift() && el.confirmSchedule.className.indexOf("bg-red-50") === -1);
      });
  }

  /* ---- no-JS fallback guard ------------------------------------------ */

  /* The fallback forms are plain POSTs; stop a double-click issuing twice. */
  function guardDoubleSubmit() {
    document.addEventListener("submit", function (e) {
      var form = e.target;
      if (!form.matches("form[action*='borrow/confirm']")) return;
      if (form.dataset.submitting === "1") { e.preventDefault(); return; }
      form.dataset.submitting = "1";
      var btn = form.querySelector("[type=submit], button:not([type=button])");
      if (btn) btn.disabled = true;
    }, true);
  }

  /* ---- shortcuts (F3.9) ---------------------------------------------- */

  function isTyping(e) {
    var t = e.target;
    return t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
  }

  function focusInput(input) {
    if (!input) return false;
    input.focus();
    if (input.select) input.select();
    return true;
  }

  function moveWithin(container, selector, dir) {
    var items = Array.prototype.slice.call(container.querySelectorAll(selector));
    if (!items.length) return;
    var idx = items.indexOf(document.activeElement);
    var next = idx === -1 ? (dir > 0 ? 0 : items.length - 1) : Math.max(0, Math.min(items.length - 1, idx + dir));
    items[next].focus();
  }

  function bindShortcuts() {
    document.addEventListener("keydown", function (e) {
      if (window.KeyTrackModal && window.KeyTrackModal.anyOpen && window.KeyTrackModal.anyOpen()) return;

      if (e.key === "F1" || (e.key === "/" && !isTyping(e))) {
        if (focusInput(el.borrowerInput)) e.preventDefault();
        return;
      }
      if (e.key === "F2") {
        if (state.borrower && !el.keyArea.classList.contains("hidden") && focusInput(el.keyInput)) e.preventDefault();
        return;
      }
      if (e.key === "?" && !isTyping(e)) {
        if (document.getElementById("pos-help-modal")) {
          e.preventDefault();
          window.KeyTrackModal.open("pos-help-modal");
        }
        return;
      }
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && state.key) {
        e.preventDefault();
        submitRelease();
        return;
      }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        var dir = e.key === "ArrowDown" ? 1 : -1;
        if (e.target === el.borrowerInput || el.borrowerResults.contains(e.target)) {
          e.preventDefault();
          moveWithin(el.borrowerResults, "[data-pos-pick-borrower]", dir);
        } else if (e.target === el.keyInput || el.keyResults.contains(e.target)) {
          e.preventDefault();
          moveWithin(el.keyResults, "[data-pos-pick-key]", dir);
        }
        return;
      }
      if (e.key === "Escape") {
        if (isTyping(e) && e.target.value && (e.target === el.borrowerInput || e.target === el.keyInput)) {
          e.target.value = "";
          e.target.dispatchEvent(new Event("input", { bubbles: true }));
        } else if (state.key) {
          clearKey();
          focusInput(el.keyInput);
        }
      }
    });
  }

  /* ---- wiring -------------------------------------------------------- */

  function bind() {
    var runBorrowerSearch = KeyTrack.debounce(function () {
      searchBorrowers(el.borrowerInput.value.trim());
    }, SEARCH_DEBOUNCE_MS);
    var runKeySearch = KeyTrack.debounce(function () {
      searchKeys(el.keyInput.value.trim());
    }, SEARCH_DEBOUNCE_MS);

    el.borrowerInput.addEventListener("input", runBorrowerSearch);
    el.borrowerForm.addEventListener("submit", function (e) {
      e.preventDefault();
      if (runBorrowerSearch.cancel) runBorrowerSearch.cancel();
      var q = el.borrowerInput.value.trim();
      /* Enter: pick an exact ID match (card scanner) or the only result. */
      searchBorrowers(q).then(function () {
        var exact = state.borrowers.filter(function (b) { return b.id_number.toLowerCase() === q.toLowerCase(); })[0];
        var pick = exact || (state.borrowers.length === 1 ? state.borrowers[0] : null);
        if (pick) selectBorrower(pick);
      });
    });

    el.borrowerResults.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-pos-pick-borrower]");
      if (!btn) return;
      e.preventDefault();
      selectBorrower(state.borrowers[parseInt(btn.getAttribute("data-pos-pick-borrower"), 10)]);
    });

    el.keyInput.addEventListener("input", runKeySearch);
    el.keyForm.addEventListener("submit", function (e) {
      e.preventDefault();
      if (runKeySearch.cancel) runKeySearch.cancel();
      searchKeys(el.keyInput.value.trim(), { pick: true });
    });

    el.keyResults.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-pos-pick-key]");
      if (!btn) return;
      selectKey(state.keys[parseInt(btn.getAttribute("data-pos-pick-key"), 10)]);
    });

    el.chips.addEventListener("click", function (e) {
      var chip = e.target.closest("[data-building]");
      if (!chip) return;
      state.building = chip.getAttribute("data-building");
      Array.prototype.forEach.call(el.chips.querySelectorAll("[data-building]"), function (c) {
        c.setAttribute("aria-pressed", c === chip ? "true" : "false");
      });
      clearKey();
      searchKeys(el.keyInput.value.trim());
    });

    root.addEventListener("click", function (e) {
      if (e.target.closest("[data-pos-clear-borrower]")) clearBorrower();
      if (e.target.closest("[data-pos-unselect-key]")) { clearKey(); focusInput(el.keyInput); }
    });

    el.confirmForm.addEventListener("submit", function (e) {
      e.preventDefault();
      submitRelease();
    });
    el.overrideInput.addEventListener("input", function () { showError(""); });

    /* A return in the right pane may free the selected borrower. */
    document.addEventListener("keytrack:out-changed", function () {
      if (state.borrower && state.borrower.active_transaction) {
        searchBorrowers(el.borrowerInput.value.trim() || state.borrower.id_number);
      }
    });
  }

  function init() {
    root = document.querySelector("[data-pos-root]");
    if (!root) return;
    guardDoubleSubmit();
    bindPosTabs();

    ENDPOINTS = {
      borrowerSearch: root.dataset.borrowerSearchUrl,
      keySearch: root.dataset.keySearchUrl,
      keyDetail: root.dataset.keyDetailUrl,
      borrow: root.dataset.borrowUrl,
    };

    var q = function (s) { return root.querySelector(s); };
    el = {
      borrowerForm: q("[data-pos-borrower-form]"),
      borrowerInput: q("[data-pos-borrower-search]"),
      borrowerResults: q("[data-pos-borrower-results]"),
      borrowerCard: q("[data-pos-borrower-card]"),
      keyArea: q("[data-pos-key-area]"),
      keyForm: q("[data-pos-key-form-js]"),
      keyInput: q("[data-pos-key-search-js]"),
      keyResults: q("[data-pos-key-results-js]"),
      chips: q("[data-pos-building-chips]"),
      confirm: q("[data-pos-confirm]"),
      confirmForm: q("[data-pos-confirm-form]"),
      confirmKey: q("[data-confirm-key]"),
      confirmRoom: q("[data-confirm-room]"),
      confirmBorrower: q("[data-confirm-borrower]"),
      confirmSchedule: q("[data-confirm-schedule]"),
      confirmError: q("[data-confirm-error]"),
      override: q("[data-confirm-override]"),
      overrideMessage: q("[data-override-message]"),
      overrideInput: q("#pos-override"),
      purpose: q("#pos-purpose"),
      due: q("#pos-due"),
      releaseBtn: q("[data-pos-release]"),
      releaseLabel: q("[data-release-label]"),
    };
    if (!el.borrowerInput || !el.keyArea || !ENDPOINTS.borrowerSearch) return; /* stay on the no-JS UI */

    /* Swap the server-rendered release pane for the live one. */
    q("[data-pos-server]").classList.add("hidden");
    var live = q("[data-pos-js]");
    live.classList.remove("hidden");
    live.classList.add("flex");

    bind();
    bindShortcuts();

    var preset = document.getElementById("pos-selected-borrower");
    var selected = preset ? JSON.parse(preset.textContent || "null") : null;
    if (selected) {
      state.borrowers = [selected];
      selectBorrower(selected);
    } else if (el.borrowerInput.value.trim()) {
      searchBorrowers(el.borrowerInput.value.trim());
    }
  }

  KeyTrack.pos = {
    hasActiveShift: hasActiveShift,
    selectBorrower: function (b) { selectBorrower(b); },
    selectKey: function (k) { selectKey(k); },
    release: submitRelease,
    state: state,
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
