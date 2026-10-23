/* schedule-grid.js — weekly room timetable grid (Section 5).
 *
 * SCOPE NOTE (F6.9): the scheduling UI is F5.1–F5.6 and there is no
 * `scheduling` app, no template and no /api/scheduling/* endpoint in this repo
 * yet (backend §5). This file provides the grid geometry and the live
 * conflict check that F5.1/F5.2 build on, so the boundary rule below lives in
 * exactly one place and cannot drift between the two halves of the team.
 *
 * Loaded only by scheduling/room_timetable.html via {% block extra_js %}.
 *
 * Requires: csrf.js, toast.js, search.js (modal.js for the block form).
 */
(function () {
  "use strict";

  var KeyTrack = (window.KeyTrack = window.KeyTrack || {});

  var ENDPOINTS = {
    checkConflict: "/api/scheduling/check-conflict/",
    freeRooms: "/api/scheduling/free-rooms/",
  };

  /* Grid window — 07:00 to 21:00 in 30-minute rows (F5.1). */
  var START_MINUTE = 7 * 60;
  var END_MINUTE = 21 * 60;
  var SLOT_MINUTES = 30;

  /* ---- time helpers -------------------------------------------------- */

  /* "09:30" or "09:30:00" -> 570 */
  function toMinutes(hhmm) {
    if (typeof hhmm === "number") return hhmm;
    var parts = String(hhmm).split(":");
    return parseInt(parts[0], 10) * 60 + parseInt(parts[1] || "0", 10);
  }

  function toLabel(minutes) {
    var h = Math.floor(minutes / 60);
    var m = minutes % 60;
    return (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m;
  }

  function slotCount() {
    return (END_MINUTE - START_MINUTE) / SLOT_MINUTES;
  }

  /* Two blocks conflict when they OVERLAP, not when they TOUCH.
   * 09:00–10:00 and 10:00–11:00 are BOTH valid — see GUIDE.md §4 and F5.2.
   * Strict inequalities on both sides are the whole point; using >= here is
   * the off-by-one this rule exists to prevent. */
  function overlaps(aStart, aEnd, bStart, bEnd) {
    return toMinutes(aStart) < toMinutes(bEnd) && toMinutes(aEnd) > toMinutes(bStart);
  }

  /* ---- geometry ------------------------------------------------------- */

  /* CSS-grid row span for a block, 1-indexed to match grid-row-start.
   * A block that starts or ends off-window is clamped into it. */
  function position(startTime, endTime) {
    var start = Math.max(toMinutes(startTime), START_MINUTE);
    var end = Math.min(toMinutes(endTime), END_MINUTE);
    var rowStart = Math.floor((start - START_MINUTE) / SLOT_MINUTES) + 1;
    var rowEnd = Math.ceil((end - START_MINUTE) / SLOT_MINUTES) + 1;
    return {
      rowStart: rowStart,
      rowEnd: rowEnd,
      span: Math.max(1, rowEnd - rowStart),
    };
  }

  /* The time-axis labels for the left gutter. */
  function timeAxis() {
    var rows = [];
    for (var i = 0; i < slotCount(); i++) {
      var minute = START_MINUTE + i * SLOT_MINUTES;
      rows.push({ minute: minute, label: toLabel(minute), isHour: minute % 60 === 0 });
    }
    return rows;
  }

  /* ---- F5.2 live conflict check -------------------------------------- */

  /* Debounced so typing a time does not fire a request per keystroke.
   * Payload: {room, day_of_week, start_time, end_time, term,
   *           exclude_block_id} — exclude_block_id keeps an edited block from
   * conflicting with itself.
   * Response (backend §5.6): {available: bool, conflicts: [{title, instructor,
   *           start_time, end_time, day_of_week}]} */
  function conflictChecker(onResult) {
    var controller = null;

    var run = KeyTrack.debounce(function (payload) {
      /* Client-side guard first — no point asking the server about a block
       * that ends before it starts (F5.2). */
      if (toMinutes(payload.end_time) <= toMinutes(payload.start_time)) {
        onResult({
          available: false,
          conflicts: [],
          clientError: "End time must be after start time.",
        });
        return;
      }

      if (controller) controller.abort();
      controller = new AbortController();

      fetch(ENDPOINTS.checkConflict, {
        method: "POST",
        headers: KeyTrack.jsonHeaders(),
        body: JSON.stringify(payload),
        credentials: "same-origin",
        signal: controller.signal,
      })
        .then(function (res) {
          if (res.status === 403) {
            KeyTrack.toast("You do not have permission.", "error");
            throw new Error("forbidden");
          }
          return res.json();
        })
        .then(onResult)
        .catch(function (err) {
          if (err.name === "AbortError" || err.message === "forbidden") return;
          KeyTrack.toast("Could not check for conflicts.", "error");
        });
    }, 300);

    return { run: run, cancel: run.cancel };
  }

  /* Render the inline conflict verdict and gate the Save button. */
  function renderConflictState(container, saveButton, result) {
    if (!container) return;

    if (result.clientError) {
      container.className =
        "mt-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[13px] text-red-800";
      container.textContent = result.clientError;
    } else if (result.available) {
      container.className =
        "mt-2 rounded-lg border border-green-200 bg-green-50 px-3 py-2 text-[13px] text-green-800";
      container.textContent = "Slot available";
    } else {
      container.className =
        "mt-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[13px] text-red-800";
      var lines = (result.conflicts || []).map(function (c) {
        return (
          "Conflicts with: " +
          c.title +
          " (" +
          String(c.start_time).slice(0, 5) +
          "–" +
          String(c.end_time).slice(0, 5) +
          (c.day_of_week ? ", " + c.day_of_week : "") +
          ")"
        );
      });
      container.textContent = lines.length
        ? lines.join(" · ")
        : "This slot conflicts with an existing block.";
    }

    if (saveButton) {
      var blocked = !!result.clientError || !result.available;
      saveButton.disabled = blocked;
      saveButton.classList.toggle("opacity-50", blocked);
      saveButton.classList.toggle("cursor-not-allowed", blocked);
    }
  }

  /* ---- grid wiring --------------------------------------------------- */

  /* Click an empty cell -> create modal pre-filled with that day/time;
   * click a block -> edit modal (F5.1). The grid repaints on every room or
   * term change, so icons must be re-initialised after each paint. */
  function bindGrid(grid) {
    if (!grid || grid.dataset.gridBound === "1") return;
    grid.dataset.gridBound = "1";

    grid.addEventListener("click", function (e) {
      var block = e.target.closest("[data-block-id]");
      if (block) {
        grid.dispatchEvent(
          new CustomEvent("schedule:edit", {
            bubbles: true,
            detail: { blockId: block.getAttribute("data-block-id") },
          })
        );
        return;
      }

      var cell = e.target.closest("[data-slot-day]");
      if (cell) {
        grid.dispatchEvent(
          new CustomEvent("schedule:create", {
            bubbles: true,
            detail: {
              day: cell.getAttribute("data-slot-day"),
              startTime: cell.getAttribute("data-slot-time"),
              endTime: toLabel(toMinutes(cell.getAttribute("data-slot-time")) + SLOT_MINUTES),
            },
          })
        );
      }
    });
  }

  /* Call after ANY grid repaint — blocks injected from JSON carry bare
   * <i data-lucide> elements that render blank until this runs. */
  function repaint(grid, html) {
    grid.innerHTML = html;
    KeyTrack.refreshIcons(grid);
  }

  KeyTrack.scheduleGrid = {
    endpoints: ENDPOINTS,
    START_MINUTE: START_MINUTE,
    END_MINUTE: END_MINUTE,
    SLOT_MINUTES: SLOT_MINUTES,
    toMinutes: toMinutes,
    toLabel: toLabel,
    slotCount: slotCount,
    timeAxis: timeAxis,
    overlaps: overlaps,
    position: position,
    conflictChecker: conflictChecker,
    renderConflictState: renderConflictState,
    bindGrid: bindGrid,
    repaint: repaint,
  };

  document.addEventListener("DOMContentLoaded", function () {
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-schedule-grid]"),
      bindGrid
    );
  });
})();
