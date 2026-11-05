/* pos.js — guard POS borrow screen (Section 3, F3.2–F3.9, F6.8).
 *
 * Progressive enhancement over transactions/borrow_dashboard.html. Without JS
 * the page is a set of GET searches and a POST to borrow_confirm. With JS:
 *
 *   0. Snapshot         — #pos-snapshot (embedded at render), refreshed from
 *                         GET /api/transactions/pos-snapshot/. Steps 1–3 read
 *                         it locally; the APIs below are the fallback.
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

  /* ---- cache ----------------------------------------------------------
   * Key lists and per-key schedule checks are cached in memory so picking a
   * borrower or a key is instant. Lists are shown from cache first and then
   * revalidated in the background (stale-while-revalidate); anything that
   * changes who holds a key clears the cache. */
  var LIST_TTL_MS = 60 * 1000;
  var DETAIL_TTL_MS = 60 * 1000;
  var keyListCache = {};
  var keyDetailCache = {};

  function cacheGet(store, key, ttl) {
    var hit = store[key];
    return hit && Date.now() - hit.t < ttl ? hit : null;
  }

  function clearCaches() {
    keyListCache = {};
    keyDetailCache = {};
  }

  /* ---- snapshot -------------------------------------------------------
   * The page embeds every available key, every active borrower and today's
   * blocks per room (transactions/pos_snapshot.py), so borrower search, the
   * key list and the schedule verdict are computed here with no round trip.
   * It is refreshed in the background when it is older than SNAPSHOT_TTL_MS,
   * when the Currently Out count changes and after a release. The server
   * re-checks everything on Release, so a slightly stale snapshot is safe. */
  var SNAPSHOT_TTL_MS = 60 * 1000;
  var BORROWER_LIMIT = 20;
  var KEY_LIMIT = 30;
  var snap = null;
  var snapAt = 0;
  var snapRequest = null;
  var lastOutCount = null;

  function setSnapshot(data) {
    if (!data || !Array.isArray(data.keys)) return false;
    snap = data;
    snapAt = Date.now();
    return true;
  }

  /* Server wall clock in minutes past midnight, advanced by the local clock
   * so the browser's own time zone never matters. */
  function nowMinutes() {
    return snap.now_minutes + (Date.now() - snapAt) / 60000;
  }

  /* The day's blocks are only valid until midnight. */
  function scheduleUsable() {
    return !!(snap && snap.rooms && nowMinutes() < 24 * 60);
  }

  function refreshSnapshot() {
    if (!ENDPOINTS.snapshot) return Promise.resolve(false);
    if (snapRequest) return snapRequest;
    snapRequest = getJSON(ENDPOINTS.snapshot)
      .then(function (r) { return r.status === 200 && setSnapshot(r.body); })
      .catch(function () { return false; /* keep the old snapshot */ })
      .then(function (ok) {
        snapRequest = null;
        if (ok) repaintFromSnapshot();
        return ok;
      });
    return snapRequest;
  }

  function refreshIfStale() {
    if (!snap || Date.now() - snapAt > SNAPSHOT_TTL_MS || !scheduleUsable()) refreshSnapshot();
  }

  /* After a refresh, redraw the lists the guard is looking at, but only if
   * they changed, so focus and scroll stay put. */
  function repaintFromSnapshot() {
    if (state.borrower && el.keyArea && !el.keyArea.classList.contains("hidden") && !state.key) {
      var before = JSON.stringify(state.keys);
      localKeySearch(el.keyInput.value.trim());
      if (JSON.stringify(state.keys) !== before) renderKeys(el.keyInput.value.trim());
    } else if (!state.borrower && snap.borrowers && el.borrowerInput) {
      var prev = JSON.stringify(state.borrowers);
      localBorrowerSearch(el.borrowerInput.value.trim());
      if (JSON.stringify(state.borrowers) !== prev) renderBorrowers(el.borrowerInput.value.trim());
    }
  }

  function contains(hay, needle) {
    return String(hay == null ? "" : hay).toLowerCase().indexOf(needle) !== -1;
  }

  /* Every word must appear in the name or the ID number. */
  function localBorrowerSearch(q) {
    var words = q.toLowerCase().split(/\s+/).filter(Boolean);
    state.borrowers = snap.borrowers.filter(function (b) {
      var hay = (b.first_name + " " + b.last_name + " " + b.id_number).toLowerCase();
      return words.every(function (w) { return hay.indexOf(w) !== -1; });
    }).slice(0, BORROWER_LIMIT);
  }

  /* Same match as /api/keys/search/: code, room, building or room name. */
  function localKeySearch(q) {
    var needle = q.toLowerCase();
    var building = state.building ? parseInt(state.building, 10) : null;
    var exact = null;
    var matches = snap.keys.filter(function (k) {
      if (building !== null && k.building_id !== building) return false;
      if (!needle) return true;
      var hit = contains(k.key_code, needle) || contains(k.room_code, needle) ||
                contains(k.building, needle) || contains(k.room_name, needle);
      if (hit && exact === null && k.key_code.toLowerCase() === needle) exact = k.id;
      return hit;
    });
    state.keys = matches.slice(0, KEY_LIMIT);
    return { results: state.keys, exact: exact };
  }

  /* The /api/keys/<id>/ schedule check, computed from today's blocks. */
  function localSchedule(k, borrowerId) {
    var blocks = snap.rooms[String(k.room_id)] || [];
    var now = nowMinutes();
    var current = null;
    var next = null;
    blocks.forEach(function (b) {
      if (!current && b.start <= now && now < b.end) current = b;
      else if (!next && b.start > now) next = b;
    });
    var s = {
      state: current ? (current.instructor_id === borrowerId ? "holder" : "other") : "free",
      no_timetable: !!snap.no_timetable,
      term: snap.term || "",
      current: current,
      next: next,
      enforcement: snap.enforcement,
      expected_return_at: null,
    };
    if (s.state === "holder") s.expected_return_at = current.end_iso;
    return { room_holder: k.room_holder || null, schedule: s };
  }

  /* Reflect a release at once instead of waiting for the refresh. */
  function applyReleaseToSnapshot(key, borrower, body) {
    if (!snap) return;
    snap.keys = snap.keys.filter(function (k) { return k.id !== key.id; });
    snap.keys.forEach(function (k) {
      if (k.room_id === key.room_id && !k.room_holder) {
        k.room_holder = { key_code: key.key_code, borrower: borrower.full_name, transaction_no: body.transaction_no };
      }
    });
    (snap.borrowers || []).forEach(function (b) {
      if (b.id !== borrower.id) return;
      b.active_transaction = {
        id: null, transaction_no: body.transaction_no, key_code: key.key_code,
        room: key.room, borrowed_at: body.borrowed_at, status: "ACTIVE",
      };
    });
  }

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
    if (snap && snap.borrowers) {
      if (borrowerController) borrowerController.abort();
      localBorrowerSearch(q);
      renderBorrowers(q);
      refreshIfStale();
      return Promise.resolve();
    }
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
      var clearBtn = current
        ? '<button type="button" data-pos-clear-borrower aria-label="Clear selected borrower" class="flex shrink-0 items-center gap-1 self-center rounded-lg border border-stone-200 bg-white px-2 py-1 text-[12px] text-stone-600 hover:bg-stone-50"><i data-lucide="x" aria-hidden="true" class="h-3.5 w-3.5"></i>Clear</button>'
        : "";
      return '<div class="flex items-center gap-1' + (current ? " rounded-lg bg-emerald-50/60 pr-2" : "") + '">' + '<button type="button" data-pos-pick-borrower="' + i + '"' + (current ? ' aria-current="true"' : "") +
        ' class="flex min-w-0 flex-1 items-center justify-between gap-2 rounded-lg px-2 py-3 text-left hover:bg-stone-50 focus:bg-stone-50 focus:outline-none focus:ring-2 focus:ring-[#1E6B4A]' +
        (current ? "" : "") + '">' +
        '<span class="min-w-0 text-[13px]"><b class="block truncate">' + esc(b.last_name) + ", " + esc(b.first_name) + "</b>" +
        '<span class="font-mono text-[12px] text-stone-500">' + esc(b.id_number) + "</span>" +
        '<span class="text-[12px] text-stone-400"> · ' + esc(b.borrower_type_display) + (b.department ? " · " + esc(b.department) : "") + "</span></span>" +
        '<span class="flex shrink-0 items-center gap-1.5">' + badge +
        '<i data-lucide="chevron-right" aria-hidden="true" class="h-4 w-4 text-stone-400"></i></span></button>' + clearBtn + "</div>";
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
      '<button type="button" data-pos-clear-borrower class="shrink-0 rounded-lg border border-stone-200 bg-white px-2 py-1 text-[12px] text-stone-600 hover:bg-stone-50">Clear</button></div>';

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
    /* The server pre-selects from ?borrower_id / ?q_borrower; drop them so a
       reload doesn't bring the old borrower and list back. */
    var url = new URL(window.location.href);
    if (url.searchParams.has("borrower_id") || url.searchParams.has("q_borrower")) {
      url.searchParams.delete("borrower_id");
      url.searchParams.delete("q_borrower");
      window.history.replaceState(null, "", url.pathname + url.search + url.hash);
      el.borrowerInput.value = "";
    }
    searchBorrowers(el.borrowerInput.value.trim());
    el.borrowerCard.innerHTML = '<p class="text-[13px] text-stone-500">Choose a borrower in the first pane to begin.</p>';
    el.keyArea.classList.add("hidden");
    el.keyArea.classList.remove("flex");
    activateTab("borrowers", false);
    el.borrowerInput.focus();
    el.borrowerInput.select();
  }

  var keyController = null;

  function searchKeys(q, opts) {
    opts = opts || {};
    if (keyController) keyController.abort();
    if (snap) {
      var local = localKeySearch(q);
      el.keyResults.removeAttribute("aria-busy");
      renderKeys(q);
      if (opts.pick) pickFrom(local, q);
      refreshIfStale();
      return Promise.resolve();
    }
    keyController = new AbortController();

    var cacheKey = (state.building || "") + "|" + q.toLowerCase();
    var cached = cacheGet(keyListCache, cacheKey, LIST_TTL_MS);
    if (cached) {
      /* Instant paint from cache; the fetch below only re-renders on change. */
      state.keys = cached.results;
      renderKeys(q);
      if (opts.pick) pickFrom(cached, q);
    } else {
      if (KeyTrack.skeletons) KeyTrack.skeletons.posKey(el.keyResults, 3);
      el.keyResults.setAttribute("aria-busy", "true");
    }

    var url = ENDPOINTS.keySearch + "?q=" + encodeURIComponent(q) +
      (state.building ? "&building=" + encodeURIComponent(state.building) : "");

    return getJSON(url, keyController.signal)
      .then(function (r) {
        if (r.status === 403) { KeyTrack.toast("You do not have permission.", "error"); return; }
        var fresh = { t: Date.now(), results: r.body.results || [], exact: r.body.exact };
        keyListCache[cacheKey] = fresh;
        var changed = !cached || JSON.stringify(cached.results) !== JSON.stringify(fresh.results);
        if (changed) {
          state.keys = fresh.results;
          renderKeys(q);
        }
        if (opts.pick && (!cached || changed)) pickFrom(fresh, q);
      })
      .catch(function (err) { handleFetchError(err, function () { searchKeys(q, opts); }, "Key search"); })
      .then(function () { el.keyResults.removeAttribute("aria-busy"); });
  }

  /* Scanner / Enter: the exact code match wins, else a lone result. */
  function pickFrom(data, q) {
    var pick = null;
    if (data.exact) pick = state.keys.filter(function (k) { return k.id === data.exact; })[0];
    else if (state.keys.length === 1) pick = state.keys[0];
    if (pick) selectKey(pick);
    else if (q && !state.keys.length) KeyTrack.toast("No available key matches \u201c" + q + "\u201d.", "error");
  }

  /* Warm the default list while the guard is still choosing a borrower. */
  function prefetchKeys() {
    getJSON(ENDPOINTS.keySearch + "?q=").then(function (r) {
      if (r.status === 200 && !keyListCache["|"]) {
        keyListCache["|"] = { t: Date.now(), results: r.body.results || [], exact: r.body.exact };
      }
    }).catch(function () { /* best effort */ });
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
        (k.room_holder ? '<span class="shrink-0 rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[11px] font-semibold text-amber-800">Room occupied</span>' : "") +
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
    el.confirmSchedule.innerHTML = occupiedHtml() + html;
    el.confirmSchedule.classList.remove("hidden");
  }

  /* "Room occupied" line shown above the schedule verdict (another copy is out). */
  function occupiedHtml() {
    var h = state.key && state.key.room_holder;
    if (!h) return "";
    return '<span class="mb-1 block font-semibold">Room is occupied right now: <span class="font-mono">' +
      esc(h.key_code) + "</span> is with " + esc(h.borrower) + ".</span>";
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
    if (state.key && state.key.room_holder) {
      scheduleBox("warn", "No class is scheduled in this room right now." + nextLine);
      return "occupied";
    }
    scheduleBox("ok", "Room is free right now." + nextLine);
    return "ok";
  }

  var detailController = null;

  var RELEASE_MODAL = "pos-release-modal";

  function selectKey(k) {
    state.key = k;
    renderKeys(el.keyInput.value.trim());
    showError("");
    setOverride(false);
    el.confirmKey.textContent = k.key_code;
    el.confirmRoom.textContent = "\u00b7 " + k.room + (k.room_name ? " " + k.room_name : "");
    el.confirmBorrower.textContent = state.borrower.full_name;
    el.purpose.value = "";
    el.due.value = "";
    el.confirmSchedule.classList.add("hidden");
    setReleaseEnabled(hasActiveShift(), "You must start a shift before releasing keys.");

    /* The search result already says whether another copy is out, so the
       occupied warning shows the moment the modal opens. */
    if (k.room_holder) {
      scheduleBox("warn", "");
      setOverride(true, "This room is occupied. Give a reason to release another key.");
    }
    if (window.KeyTrackModal) window.KeyTrackModal.open(RELEASE_MODAL);
    el.purpose.focus();

    if (detailController) detailController.abort();
    if (scheduleUsable()) { applyDetail(k, localSchedule(k, state.borrower.id)); return; }
    var cacheKey = k.id + "|" + state.borrower.id;
    var hit = cacheGet(keyDetailCache, cacheKey, DETAIL_TTL_MS);
    if (hit) { applyDetail(k, hit.body); return; }

    detailController = new AbortController();
    var url = ENDPOINTS.keyDetail.replace("/0/", "/" + encodeURIComponent(k.id) + "/") +
      "?borrower=" + encodeURIComponent(state.borrower.id);
    getJSON(url, detailController.signal)
      .then(function (r) {
        if (r.status !== 200 || state.key !== k) return;
        keyDetailCache[cacheKey] = { t: Date.now(), body: r.body };
        applyDetail(k, r.body);
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") return;
        /* Not fatal: the server re-checks the schedule on release. */
        scheduleBox("info", "Couldn't load today's schedule for this room. The release will still be checked.");
      });
  }

  function applyDetail(k, body) {
    if (state.key !== k) return;
    /* Detail is fresher than the list: take its view of who holds the room. */
    k.room_holder = body.room_holder || null;
    var s = body.schedule || {};
    var verdict = describeSchedule(s, state.borrower.full_name);
    if (s.expected_return_at && !el.due.value) el.due.value = toLocalInput(s.expected_return_at);
    if (verdict === "warn" || verdict === "occupied") {
      setOverride(true, k.room_holder
        ? "This room is occupied. Give a reason to release another key."
        : "This room is scheduled to someone else. Give a reason to release it anyway.");
    } else if (verdict === "block") {
      setReleaseEnabled(false, "Blocked by the room schedule.");
    } else if (!k.room_holder) {
      setOverride(false);
    }
  }

  function clearKey() {
    state.key = null;
    if (detailController) detailController.abort();
    if (window.KeyTrackModal && window.KeyTrackModal.isOpen(RELEASE_MODAL)) window.KeyTrackModal.close(RELEASE_MODAL);
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
      showError("Type a reason (at least 5 characters) to continue.");
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
                         (b.overridden ? " — override recorded." : "."), "success");
          clearCaches();
          applyReleaseToSnapshot(state.key, state.borrower, b);
          refreshSnapshot();
          if (window.KeyTrackModal) window.KeyTrackModal.close(RELEASE_MODAL);
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
        clearCaches();
        if (snap) refreshSnapshot();
        else searchKeys(el.keyInput.value.trim());
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
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && state.key &&
          window.KeyTrackModal && window.KeyTrackModal.isOpen(RELEASE_MODAL)) {
        e.preventDefault();
        submitRelease();
        return;
      }
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

    /* Local filtering is instant, so only the API fallback is debounced. */
    el.borrowerInput.addEventListener("input", function () {
      if (snap && snap.borrowers) searchBorrowers(el.borrowerInput.value.trim());
      else runBorrowerSearch();
    });
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

    el.keyInput.addEventListener("input", function () {
      if (snap) searchKeys(el.keyInput.value.trim());
      else runKeySearch();
    });
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

    /* Escape, overlay click or "Change key" closes the modal: drop the pick. */
    document.addEventListener("modal:close", function (e) {
      if (!e.detail || e.detail.id !== RELEASE_MODAL) return;
      state.key = null;
      if (detailController) detailController.abort();
      if (state.keys.length) renderKeys(el.keyInput.value.trim());
      if (state.borrower && !el.keyArea.classList.contains("hidden")) el.keyInput.focus();
    });

    /* A return in the right pane may free the selected borrower. */
    document.addEventListener("keytrack:out-changed", function (e) {
      /* The panel fires this on every poll; only a changed count means a key
         went out or came back somewhere. */
      var count = e.detail ? e.detail.count : null;
      if (count === lastOutCount && count !== null) return;
      var first = lastOutCount === null;
      lastOutCount = count;
      if (first) return;
      clearCaches();
      var refreshed = snap ? refreshSnapshot() : Promise.resolve();
      refreshed.then(function () {
        if (state.borrower && state.borrower.active_transaction) {
          searchBorrowers(el.borrowerInput.value.trim() || state.borrower.id_number);
        }
      });
    });

    /* Back to the tab after a while: fetch a fresh snapshot quietly. */
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") refreshIfStale();
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
      snapshot: root.dataset.snapshotUrl,
    };

    var q = function (s) { return root.querySelector(s); };
    /* The release modal lives outside the POS root. */
    var qd = function (s) { return document.querySelector(s); };
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
      confirm: qd("[data-pos-confirm]"),
      confirmForm: qd("[data-pos-confirm-form]"),
      confirmKey: qd("[data-confirm-key]"),
      confirmRoom: qd("[data-confirm-room]"),
      confirmBorrower: qd("[data-confirm-borrower]"),
      confirmSchedule: qd("[data-confirm-schedule]"),
      confirmError: qd("[data-confirm-error]"),
      override: qd("[data-confirm-override]"),
      overrideMessage: qd("[data-override-message]"),
      overrideInput: qd("#pos-override"),
      purpose: qd("#pos-purpose"),
      due: qd("#pos-due"),
      releaseBtn: qd("[data-pos-release]"),
      releaseLabel: qd("[data-release-label]"),
    };
    if (!el.borrowerInput || !el.keyArea || !ENDPOINTS.borrowerSearch) return; /* stay on the no-JS UI */

    /* Swap the server-rendered release pane for the live one. */
    q("[data-pos-server]").classList.add("hidden");
    var live = q("[data-pos-js]");
    live.classList.remove("hidden");
    live.classList.add("flex");

    var embedded = document.getElementById("pos-snapshot");
    try { setSnapshot(embedded ? JSON.parse(embedded.textContent || "null") : null); } catch (err) { snap = null; }

    bind();
    bindShortcuts();
    if (!snap) prefetchKeys();

    var preset = document.getElementById("pos-selected-borrower");
    var selected = preset ? JSON.parse(preset.textContent || "null") : null;
    if (selected) {
      state.borrowers = [selected];
      selectBorrower(selected);
    } else if (el.borrowerInput.value.trim() || (snap && snap.borrowers)) {
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
