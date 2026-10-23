// Room finder (F5.3, F6.8): live search against GET /api/scheduling/free-rooms/
// and a "Schedule this" hand-off to the schedule editor (F5.2).
// Upgraded with loading skeletons, network retry toast & session expired handling (F6.8).
(function () {
  var form = document.getElementById("free-room-form");
  if (!form) return;

  var apiUrl = form.dataset.apiUrl;
  var results = document.getElementById("free-room-results");
  var cardTemplate = document.getElementById("free-room-card-template");
  var summary = document.querySelector("[data-summary]");
  var empty = document.querySelector("[data-empty]");
  var submitBtn = form.querySelector("[data-submit]");
  var spinner = form.querySelector("[data-spinner]");
  var searchIcon = form.querySelector("[data-search-icon]");
  var startInput = form.elements.start;
  var endInput = form.elements.end;
  var dialog = document.getElementById("schedule-stub-dialog");

  var TYPE_ICONS = {
    CLASSROOM: "presentation", LABORATORY: "flask-conical", OFFICE: "briefcase",
    FACILITY: "warehouse", OTHER: "door-open"
  };
  var DEBOUNCE_MS = 250; /* within 250-300ms band */

  var debounceTimer = null;
  var inFlight = null;
  var lastQuery = null;   // the query the cards on screen belong to
  var lastTrigger = null;

  // --- time helpers -------------------------------------------------------
  function toMinutes(hhmm) {
    if (!/^\d{2}:\d{2}/.test(hhmm || "")) return null;
    return parseInt(hhmm.slice(0, 2), 10) * 60 + parseInt(hhmm.slice(3, 5), 10);
  }
  function toHHMM(minutes) {
    minutes = Math.min(minutes, 23 * 60 + 59);
    var h = Math.floor(minutes / 60), m = minutes % 60;
    return (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m;
  }
  function dayLabel(value) {
    var input = form.querySelector('input[name="day"][value="' + value + '"]');
    return input ? input.dataset.label : "";
  }

  // --- errors -------------------------------------------------------------
  function clearErrors() {
    form.querySelectorAll("[data-error-for]").forEach(function (el) {
      el.textContent = "";
      el.classList.add("hidden");
    });
    form.querySelectorAll("[aria-invalid]").forEach(function (el) {
      el.removeAttribute("aria-invalid");
    });
  }
  function showErrors(errors) {
    Object.keys(errors).forEach(function (field) {
      var el = form.querySelector('[data-error-for="' + field + '"]')
        || form.querySelector('[data-error-for="__all__"]');
      var messages = Array.isArray(errors[field]) ? errors[field] : [errors[field]];
      if (el) {
        el.textContent = messages.join(" ");
        el.classList.remove("hidden");
      }
      if (form.elements[field] && form.elements[field].setAttribute) {
        form.elements[field].setAttribute("aria-invalid", "true");
      }
    });
  }

  // --- state --------------------------------------------------------------
  function setLoading(on) {
    submitBtn.disabled = on;
    spinner.classList.toggle("hidden", !on);
    searchIcon.classList.toggle("hidden", on);
    form.setAttribute("aria-busy", on ? "true" : "false");
    if (on) {
      if (window.KeyTrack && window.KeyTrack.skeletons) {
        window.KeyTrack.skeletons.roomCards(results, 6);
      } else {
        results.classList.add("opacity-50");
      }
    } else {
      results.classList.remove("opacity-50");
    }
  }

  function syncDurationChips() {
    var span = toMinutes(endInput.value) - toMinutes(startInput.value);
    form.querySelectorAll("[data-duration]").forEach(function (chip) {
      chip.setAttribute("aria-pressed", String(parseInt(chip.dataset.duration, 10) === span));
    });
  }

  function currentParams() {
    var params = new URLSearchParams();
    new FormData(form).forEach(function (value, key) {
      if (String(value).trim() !== "") params.append(key, value);
    });
    return params;
  }

  // --- rendering ----------------------------------------------------------
  function renderCard(room) {
    var card = cardTemplate.content.firstElementChild.cloneNode(true);
    var fill = function (name, text) {
      var el = card.querySelector('[data-field="' + name + '"]');
      if (el) el.textContent = text;
    };
    fill("room_code", room.room_code);
    fill("building_code", room.building.code);
    fill("building_name", room.building.name);
    fill("room_type_display", room.room_type_display);
    fill("capacity", room.capacity ? room.capacity + " seats" : "Capacity n/a");
    card.querySelector('[data-field="icon"]')
      .setAttribute("data-lucide", TYPE_ICONS[room.room_type] || "door-open");
    var btn = card.querySelector("[data-schedule-room]");
    btn.dataset.roomId = room.id;
    btn.dataset.roomLabel = room.building.code + "-" + room.room_code;
    return card;
  }

  function render(data, params) {
    var fragment = document.createDocumentFragment();
    data.results.forEach(function (room) { fragment.appendChild(renderCard(room)); });
    results.replaceChildren(fragment);
    empty.classList.toggle("hidden", data.count > 0);
    summary.textContent = data.count + " room" + (data.count === 1 ? "" : "s") + " free · "
      + dayLabel(params.get("day")) + " " + params.get("start") + "–" + params.get("end");
    if (window.lucide) lucide.createIcons();
  }

  // --- search -------------------------------------------------------------
  function search() {
    clearTimeout(debounceTimer);
    clearErrors();

    var params = currentParams();
    // Client-side guard so the obvious mistake never costs a round trip.
    var start = toMinutes(params.get("start")), end = toMinutes(params.get("end"));
    if (start !== null && end !== null && end <= start) {
      showErrors({ end: ["End time must be after start time."] });
      return;
    }

    if (inFlight) inFlight.abort();
    var controller = new AbortController();
    inFlight = controller;
    setLoading(true);

    fetch(apiUrl + "?" + params.toString(), {
      headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
      signal: controller.signal
    })
      .then(function (res) {
        if (window.KeyTrack && window.KeyTrack.isSessionExpired && window.KeyTrack.isSessionExpired(res)) {
          window.KeyTrack.handleSessionExpired();
          throw new Error("session_expired");
        }
        return res.json().catch(function () { return {}; }).then(function (body) {
          if (window.KeyTrack && window.KeyTrack.isSessionExpired && window.KeyTrack.isSessionExpired(res, body)) {
            window.KeyTrack.handleSessionExpired();
            throw new Error("session_expired");
          }
          if (res.status === 200) {
            lastQuery = params;
            render(body, params);
            history.replaceState(null, "", form.action + "?" + params.toString());
          } else if (res.status === 422) {
            showErrors(body);
          } else if (res.status === 403) {
            showErrors({ __all__: ["You do not have permission to search rooms."] });
          } else {
            showErrors({ __all__: ["Something went wrong (" + res.status + "). Try again."] });
          }
        });
      })
      .catch(function (err) {
        if (err.name === "AbortError" || err.message === "session_expired") return;
        showErrors({ __all__: ["Could not reach the server. Check your connection and try again."] });
        /* F6.8 network failure toast with retry */
        if (window.KeyTrack && window.KeyTrack.networkErrorToast) {
          window.KeyTrack.networkErrorToast(function () {
            search();
          }, "Room search failed due to network error.");
        }
      })
      .finally(function () {
        if (inFlight === controller) {
          inFlight = null;
          setLoading(false);
        }
      });
  }

  function scheduleSearch() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(search, DEBOUNCE_MS);
  }

  // --- "Schedule this" ----------------------------------------------------
  function openScheduler(btn) {
    var q = lastQuery || currentParams();
    var prefill = {
      room_id: parseInt(btn.dataset.roomId, 10),
      room_label: btn.dataset.roomLabel,
      day_of_week: parseInt(q.get("day"), 10),
      start_time: q.get("start"),
      end_time: q.get("end")
    };

    // Contract for F5.2: listen for this event on document, then fill the form.
    if (window.KeyTrackModal && document.getElementById("schedule-form-modal")) {
      document.dispatchEvent(new CustomEvent("keytrack:schedule-prefill", { detail: prefill }));
      window.KeyTrackModal.open("schedule-form-modal");
      return;
    }

    lastTrigger = btn;
    dialog.querySelector('[data-prefill="room"]').textContent = prefill.room_label;
    dialog.querySelector('[data-prefill="day"]').textContent = dayLabel(prefill.day_of_week);
    dialog.querySelector('[data-prefill="time"]').textContent = prefill.start_time + "–" + prefill.end_time;
    document.body.classList.add("overflow-hidden");
    dialog.showModal();
  }

  // --- wiring -------------------------------------------------------------
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    search();
  });

  // Moving the start time keeps the chosen duration.
  var spanBeforeEdit = null;
  startInput.addEventListener("focus", function () {
    spanBeforeEdit = toMinutes(endInput.value) - toMinutes(startInput.value);
  });
  startInput.addEventListener("change", function () {
    var start = toMinutes(startInput.value);
    if (start !== null && spanBeforeEdit > 0) endInput.value = toHHMM(start + spanBeforeEdit);
    spanBeforeEdit = toMinutes(endInput.value) - toMinutes(startInput.value);
  });

  form.addEventListener("change", function () {
    syncDurationChips();
    scheduleSearch();
  });
  form.elements.min_capacity.addEventListener("input", scheduleSearch);

  form.addEventListener("click", function (e) {
    var chip = e.target.closest("[data-duration]");
    if (!chip) return;
    var start = toMinutes(startInput.value);
    if (start === null) { startInput.focus(); return; }
    endInput.value = toHHMM(start + parseInt(chip.dataset.duration, 10));
    syncDurationChips();
    search();
  });

  results.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-schedule-room]");
    if (btn) openScheduler(btn);
  });

  dialog.addEventListener("click", function (e) {
    // A click on the <dialog> itself (not its card) is a backdrop click.
    if (e.target === dialog || e.target.closest("[data-dialog-close]")) dialog.close();
  });
  dialog.addEventListener("close", function () {
    document.body.classList.remove("overflow-hidden");
    if (lastTrigger && lastTrigger.isConnected) lastTrigger.focus();
  });

  syncDurationChips();
  search();
})();
