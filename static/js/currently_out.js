/* TASK F3.7 — "Currently Out" live panel + return flow.
 *
 * Rows, counters and due states come from GET /api/transactions/active/;
 * a return is POST /api/transactions/<pk>/return/ with remarks + condition.
 * Refreshes every 30s, on window focus, and after any return.
 */
(function () {
  var panel = document.getElementById("currently-out");
  if (!panel) return;

  var MODAL_ID = "return-key-modal";
  var SEARCH_DEBOUNCE_MS = 250;
  var FOCUS_REFRESH_FLOOR_MS = 5000;

  var activeUrl = panel.dataset.activeUrl;
  var returnUrlTemplate = panel.dataset.returnUrl;    // .../api/transactions/0/return/
  var refreshMs = parseInt(panel.dataset.refreshMs, 10) || 30000;

  var list = panel.querySelector("[data-out-list]");
  var searchInput = panel.querySelector("[data-out-search]");
  var searchClear = panel.querySelector("[data-out-search-clear]");
  var refreshBtn = panel.querySelector("[data-out-refresh]");
  var refreshIcon = panel.querySelector("[data-out-spinner]");
  var updatedLabel = panel.querySelector("[data-out-updated]");

  var modal = document.getElementById(MODAL_ID);
  var form = modal.querySelector("[data-return-form]");
  var submitBtn = modal.querySelector("[data-return-submit]");
  var submitSpinner = modal.querySelector("[data-return-spinner]");
  var submitLabel = modal.querySelector("[data-return-submit-label]");
  var errorBox = modal.querySelector("[data-return-error]");
  var remarks = modal.querySelector("[data-return-remarks]");
  var conditionNote = modal.querySelector("[data-return-condition-note]");

  var rows = [];          // last payload, newest first
  var pending = null;     // row the modal is about
  var inFlight = false;
  var clockSkewMs = 0;    // server clock - browser clock
  var lastFetchedAt = null;
  var searchTimer = null;

  /* ---------- helpers ---------- */

  function now() { return new Date(Date.now() + clockSkewMs); }

  function hhmm(iso) {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function dayTime(iso) {
    var d = new Date(iso);
    if (d.toDateString() === now().toDateString()) return hhmm(iso);
    return d.toLocaleDateString([], { month: "short", day: "numeric" }) + " " + hhmm(iso);
  }

  function elapsedText(iso) {
    var secs = Math.max(0, Math.floor((now() - new Date(iso)) / 1000));
    var h = Math.floor(secs / 3600);
    var m = Math.floor((secs % 3600) / 60);
    var s = secs % 60;
    return h + ":" + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  }

  function dueText(row) {
    if (!row.expected_return_at) return row.due_state === "overdue" ? "Overdue" : "";
    var mins = Math.round((new Date(row.expected_return_at) - now()) / 60000);
    if (mins < 0) return "overdue by " + Math.abs(mins) + "m";
    if (row.due_state === "due_soon") return "due in " + mins + "m";
    return "due " + hhmm(row.expected_return_at);
  }

  function esc(value) {
    var node = document.createElement("span");
    node.textContent = value == null ? "" : value;
    return node.innerHTML;
  }

  /* ---------- rendering ---------- */

  var ROW_TONE = {
    overdue: "border-red-300 bg-red-50",
    due_soon: "border-amber-300 bg-amber-50",
    ok: "border-stone-200 bg-white",
  };

  var ROW_PILL = {
    overdue: "bg-red-100 text-red-800 border-red-200",
    due_soon: "bg-amber-100 text-amber-800 border-amber-200",
    ok: "bg-blue-50 text-blue-700 border-blue-100",
  };

  var ROW_PILL_LABEL = { overdue: "OVERDUE", due_soon: "DUE SOON", ok: "ACTIVE" };

  function rowHtml(row) {
    var state = ROW_TONE[row.due_state] ? row.due_state : "ok";
    var due = dueText(row);
    return ''
      + '<article class="out-row border rounded-xl p-2.5 transition-all duration-300 ' + ROW_TONE[state] + '"'
      + ' data-txn-id="' + row.id + '" data-borrowed-at="' + esc(row.borrowed_at) + '">'
      + '<div class="flex items-start justify-between gap-2">'
      + '<div class="min-w-0">'
      + '<p class="text-[13px] leading-5"><b class="font-mono">' + esc(row.key_code) + '</b>'
      + '<span class="text-stone-500"> · ' + esc(row.room) + '</span></p>'
      + '<p class="text-[13px] leading-5 truncate">' + esc(row.borrower.sort_name)
      + ' <span class="text-stone-400 font-mono text-[12px]">' + esc(row.borrower.id_number) + '</span></p>'
      + '<p class="text-[12px] text-stone-500 leading-5">out ' + esc(dayTime(row.borrowed_at))
      + ' · <span class="tabular-nums font-medium" data-elapsed>' + elapsedText(row.borrowed_at) + '</span>'
      + (due ? ' · <span data-due-text>' + esc(due) + '</span>' : '')
      + '</p>'
      + '<p class="text-[11px] text-stone-400 font-mono mt-0.5">' + esc(row.transaction_no) + '</p>'
      + '</div>'
      + '<div class="flex flex-col items-end gap-1.5 shrink-0">'
      + '<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[11px] font-semibold '
      + ROW_PILL[state] + '">● ' + ROW_PILL_LABEL[state] + '</span>'
      + '<button type="button" data-return-open'
      + ' class="bg-green-700 hover:bg-green-800 text-white px-3 py-1.5 rounded-lg text-[13px] font-semibold">'
      + 'Return</button>'
      + '</div></div></article>';
  }

  function emptyState(filtered) {
    var icon = filtered ? "search-x" : "check-circle-2";
    var text = filtered ? "No out key matches that filter." : "No keys out right now.";
    return '<div class="text-center py-10 px-4">'
      + '<i data-lucide="' + icon + '" class="w-6 h-6 mx-auto text-stone-300"></i>'
      + '<p class="text-[13px] text-stone-500 mt-2">' + text + '</p></div>';
  }

  function render() {
    list.innerHTML = rows.length
      ? rows.map(rowHtml).join("")
      : emptyState(Boolean(searchInput.value.trim()));
    list.setAttribute("aria-busy", "false");
    if (window.lucide) lucide.createIcons();
  }

  function setCounters(counts) {
    panel.querySelector("[data-out-count]").textContent = counts.count;
    [["[data-out-overdue-pill]", "[data-out-overdue]", counts.overdue_count],
     ["[data-out-due-soon-pill]", "[data-out-due-soon]", counts.due_soon_count]
    ].forEach(function (pair) {
      var pill = panel.querySelector(pair[0]);
      panel.querySelector(pair[1]).textContent = pair[2];
      pill.classList.toggle("hidden", !pair[2]);
      pill.classList.toggle("inline-flex", Boolean(pair[2]));
    });
    document.dispatchEvent(new CustomEvent("keytrack:out-changed", { detail: counts }));
  }

  function tickTimers() {
    list.querySelectorAll(".out-row").forEach(function (node) {
      var cell = node.querySelector("[data-elapsed]");
      if (cell) cell.textContent = elapsedText(node.dataset.borrowedAt);
    });
    if (!lastFetchedAt) return;
    var secs = Math.round((Date.now() - lastFetchedAt) / 1000);
    updatedLabel.textContent = secs < 10 ? "just now" : "updated " + secs + "s ago";
  }

  /* ---------- fetching ---------- */

  function load(opts) {
    var quiet = opts && opts.quiet;
    if (!quiet) list.setAttribute("aria-busy", "true");
    refreshIcon.classList.add("animate-spin");
    var q = searchInput.value.trim();
    return fetch(activeUrl + (q ? "?q=" + encodeURIComponent(q) : ""), {
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    })
      .then(function (res) {
        if (res.status === 403) throw new Error("permission");
        if (!res.ok) throw new Error("http " + res.status);
        return res.json();
      })
      .then(function (payload) {
        clockSkewMs = new Date(payload.server_time) - new Date();
        rows = payload.results;
        lastFetchedAt = Date.now();
        render();
        setCounters(payload);
      })
      .catch(function (err) {
        list.setAttribute("aria-busy", "false");
        if (err.message === "permission") {
          window.KeyTrackToast.error("You do not have permission to see keys that are out.");
          return;
        }
        if (!quiet) {
          list.innerHTML = '<div class="text-center py-10 px-4">'
            + '<p class="text-[13px] text-red-700">Could not load the out-keys list.</p>'
            + '<button type="button" data-out-retry class="mt-2 text-[13px] font-semibold underline">'
            + 'Try again</button></div>';
        }
      })
      .finally(function () { refreshIcon.classList.remove("animate-spin"); });
  }

  /* ---------- return modal ---------- */

  var CONDITION_NOTES = {
    DAMAGED: ["Recorded as DAMAGED: the key stays out of circulation until an admin clears it.",
              "bg-amber-50 border-amber-200 text-amber-800"],
    LOST: ["Recorded as LOST: the key stays out of circulation and the room may need a re-key.",
           "bg-red-50 border-red-200 text-red-800"],
  };

  function condition() {
    var picked = form.querySelector("input[name=condition]:checked");
    return picked ? picked.value : "OK";
  }

  function paintConditionNote() {
    var note = CONDITION_NOTES[condition()];
    conditionNote.className = (note ? "" : "hidden ")
      + "mt-1.5 text-[12px] rounded-lg border px-2.5 py-1.5 " + (note ? note[1] : "");
    conditionNote.textContent = note ? note[0] : "";
  }

  function showError(message) {
    errorBox.textContent = message;
    errorBox.classList.remove("hidden");
  }

  function setBusy(busy) {
    inFlight = busy;
    submitBtn.disabled = busy;
    submitSpinner.classList.toggle("hidden", !busy);
    submitLabel.textContent = busy ? "Recording..." : "Confirm return";
  }

  function openReturn(row, trigger) {
    pending = row;
    form.reset();
    errorBox.classList.add("hidden");
    paintConditionNote();
    setBusy(false);
    modal.querySelector("[data-return-key]").textContent = row.key_code;
    modal.querySelector("[data-return-room]").textContent =
      row.room + (row.room_name ? " · " + row.room_name : "");
    modal.querySelector("[data-return-borrower]").textContent = row.borrower.full_name;
    modal.querySelector("[data-return-borrower-id]").textContent =
      row.borrower.id_number + (row.borrower.department ? " · " + row.borrower.department : "");
    modal.querySelector("[data-return-since]").textContent = dayTime(row.borrowed_at);
    modal.querySelector("[data-return-elapsed]").textContent = elapsedText(row.borrowed_at) + " elapsed";
    modal.querySelector("[data-return-txn-no]").textContent = row.transaction_no;
    window.KeyTrackModal.open(MODAL_ID, { trigger: trigger });
  }

  function animateOut(txnId) {
    var node = list.querySelector('.out-row[data-txn-id="' + txnId + '"]');
    if (!node) return Promise.resolve();
    node.style.maxHeight = node.offsetHeight + "px";
    node.classList.add("overflow-hidden");
    requestAnimationFrame(function () {
      node.classList.add("opacity-0", "translate-x-4");
      node.style.maxHeight = "0px";
      node.style.paddingTop = "0px";
      node.style.paddingBottom = "0px";
    });
    return new Promise(function (resolve) {
      setTimeout(function () { node.remove(); resolve(); }, 320);
    });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    if (inFlight || !pending) return;
    var row = pending;
    var url = returnUrlTemplate.replace("/0/return/", "/" + row.id + "/return/");
    errorBox.classList.add("hidden");
    setBusy(true);

    postJson(url, { remarks: remarks.value.trim(), condition: condition() })
      .then(function (res) {
        return res.json()
          .catch(function () { return {}; })
          .then(function (body) { return { status: res.status, body: body }; });
      })
      .then(function (out) {
        if (out.status === 403) {
          window.KeyTrackModal.close(MODAL_ID);
          window.KeyTrackToast.error(out.body.error || "You do not have permission.");
          return;
        }
        if (out.status !== 200) {
          showError(out.body.error || "Could not record this return. Try again.");
          return;
        }
        window.KeyTrackModal.close(MODAL_ID);
        rows = rows.filter(function (r) { return r.id !== row.id; });
        if (out.body.counts) setCounters(out.body.counts);
        var verb = out.body.condition === "OK" ? "returned"
          : out.body.condition === "DAMAGED" ? "returned as DAMAGED"
          : "recorded as LOST";
        window.KeyTrackToast[out.body.condition === "OK" ? "success" : "warning"](
          "Key " + out.body.key_code + " " + verb + " (" + out.body.transaction_no + ").");
        animateOut(row.id).then(function () {
          if (!rows.length) render();
          load({ quiet: true });
        });
      })
      .catch(function () {
        showError("Network problem — the return was not recorded.");
      })
      .finally(function () { setBusy(false); });
  });

  form.addEventListener("change", function (e) {
    if (e.target.name === "condition") paintConditionNote();
  });

  /* ---------- panel events ---------- */

  list.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-return-open]");
    if (btn) {
      var id = parseInt(btn.closest(".out-row").dataset.txnId, 10);
      var row = rows.find(function (r) { return r.id === id; });
      if (row) openReturn(row, btn);
      return;
    }
    if (e.target.closest("[data-out-retry]")) load();
  });

  searchInput.addEventListener("input", function () {
    searchClear.classList.toggle("hidden", !searchInput.value);
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () { load(); }, SEARCH_DEBOUNCE_MS);
  });

  searchInput.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && searchInput.value) {
      e.stopPropagation();
      searchInput.value = "";
      searchClear.classList.add("hidden");
      load();
    }
  });

  searchClear.addEventListener("click", function () {
    searchInput.value = "";
    searchClear.classList.add("hidden");
    searchInput.focus();
    load();
  });

  refreshBtn.addEventListener("click", function () { load(); });

  /* Auto-refresh every 30s and whenever the window regains focus — but never
     while the return modal is open or a return is in flight, so the list does
     not move under the guard's hands. */
  function canRefresh() {
    return !inFlight && !window.KeyTrackModal.isOpen(MODAL_ID);
  }

  setInterval(function () { if (canRefresh()) load({ quiet: true }); }, refreshMs);
  setInterval(tickTimers, 1000);

  window.addEventListener("focus", function () {
    if (canRefresh() && (!lastFetchedAt || Date.now() - lastFetchedAt > FOCUS_REFRESH_FLOOR_MS)) {
      load({ quiet: true });
    }
  });

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && canRefresh()) load({ quiet: true });
  });

  /* So the borrow flow (TASK F3.5) can refresh this panel after a release. */
  window.KeyTrackCurrentlyOut = { refresh: function () { return load({ quiet: true }); } };

  load();
})();
