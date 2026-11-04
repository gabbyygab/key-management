/* schedule-dnd.js - drag a class to another day / time on a timetable grid.
 *
 * Works on any [data-schedule-grid] (room schedule and section timetable):
 *   - class cards are [data-block] with draggable="true";
 *   - the half-hour cells are [data-slot-day][data-slot-time="HH:MM"].
 *
 * Drop on free space  -> POST /api/scheduling/blocks/<id>/move/ (same room,
 *                        same length, new day and start).
 * Drop on another card -> ask, then POST block_swap (they trade day and time).
 *
 * The server re-checks the room, the professor and the section; a clash
 * answers 422 with the reason and nothing changes. The grid is read from the
 * DOM rather than assumed, so the room and section pages share this file.
 *
 * Requires: csrf.js, toast.js, confirm.js. Dragging is mouse-only; keyboard
 * users open the card and change the room, day and time in the dialog.
 */
(function () {
  "use strict";

  var KeyTrack = (window.KeyTrack = window.KeyTrack || {});
  var START = 7 * 60, END = 21 * 60, STEP = 30;

  function minutesOf(v) {
    var p = String(v).split(":");
    return parseInt(p[0], 10) * 60 + parseInt(p[1] || "0", 10);
  }
  function hhmm(m) {
    var h = Math.floor(m / 60), mm = m % 60;
    return (h < 10 ? "0" : "") + h + ":" + (mm < 10 ? "0" : "") + mm;
  }

  function bind(grid) {
    if (grid.dataset.dndBound === "1") return;
    grid.dataset.dndBound = "1";
    var drag = null;   // {id, data, card, grabRows, duration}
    var preview = null;

    function geometry() {
      // Cell of Monday 07:00 anchors the grid; one cell gives the step sizes.
      var origin = grid.querySelector('[data-slot-day="0"][data-slot-time="07:00"]');
      var right = grid.querySelector('[data-slot-day="5"][data-slot-time="07:00"]');
      if (!origin || !right) return null;
      var a = origin.getBoundingClientRect(), b = right.getBoundingClientRect();
      return { left: a.left, top: a.top, w: a.width, h: a.height, right: b.right };
    }

    // Where the pointer is on the grid, as {day, row}; null when outside it.
    function locate(e, geo) {
      var col = Math.floor((e.clientX - geo.left) / geo.w);
      var row = Math.floor((e.clientY - geo.top) / geo.h);
      if (col < 0 || col > 5) return null;
      return { day: col, row: row };
    }

    function target(e) {
      var geo = geometry();
      if (!geo || !drag) return null;
      var at = locate(e, geo);
      if (!at) return null;
      var startRow = at.row - drag.grabRows;
      var rows = drag.duration / STEP;
      startRow = Math.max(0, Math.min(startRow, (END - START) / STEP - rows));
      return { day: at.day, row: startRow, rows: rows, geo: geo,
               start: START + startRow * STEP };
    }

    function showPreview(t, ok) {
      if (!preview) {
        preview = document.createElement("div");
        preview.setAttribute("aria-hidden", "true");
        preview.className = "pointer-events-none absolute z-30 rounded-lg border-2 border-dashed";
        grid.appendChild(preview);
      }
      var g = grid.getBoundingClientRect();
      preview.style.left = (t.geo.left - g.left + t.day * t.geo.w) + "px";
      preview.style.top = (t.geo.top - g.top + t.row * t.geo.h) + "px";
      preview.style.width = t.geo.w + "px";
      preview.style.height = (t.rows * t.geo.h) + "px";
      preview.style.borderColor = ok ? "#1E6B4A" : "#dc2626";
      preview.style.background = ok ? "rgba(30,107,74,.12)" : "rgba(220,38,38,.10)";
    }
    function hidePreview() {
      if (preview && preview.parentNode) preview.parentNode.removeChild(preview);
      preview = null;
    }

    function cardAt(e) {
      var els = document.elementsFromPoint(e.clientX, e.clientY);
      for (var i = 0; i < els.length; i++) {
        var c = els[i].closest && els[i].closest("[data-block]");
        if (c && grid.contains(c) && c !== drag.card) return c;
      }
      return null;
    }

    grid.addEventListener("dragstart", function (e) {
      var card = e.target.closest && e.target.closest("[data-block]");
      if (!card || !grid.contains(card)) return;
      var data;
      try { data = JSON.parse(card.getAttribute("data-block")); } catch (err) { return; }
      var geo = geometry();
      if (!geo || !data.id) { e.preventDefault(); return; }
      var rect = card.getBoundingClientRect();
      drag = {
        id: data.id, data: data, card: card,
        // Rows between the card's start and the pointer, measured on the grid
        // (not from the card's own edge, which sits a few px inside its cell).
        grabRows: Math.max(0, Math.floor((e.clientY - geo.top) / geo.h) -
                               (minutesOf(data.start_time) - START) / STEP),
        duration: minutesOf(data.end_time) - minutesOf(data.start_time),
      };
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", String(data.id));
      card.classList.add("opacity-50");
    });

    grid.addEventListener("dragover", function (e) {
      if (!drag) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      var over = cardAt(e);
      if (over) { hidePreview(); return; }
      var t = target(e);
      if (t) showPreview(t, true); else hidePreview();
    });

    grid.addEventListener("dragend", function () {
      if (drag) drag.card.classList.remove("opacity-50");
      hidePreview();
      drag = null;
    });

    grid.addEventListener("drop", function (e) {
      if (!drag) return;
      e.preventDefault();
      var moving = drag;
      var other = cardAt(e);
      var t = target(e);
      hidePreview();
      moving.card.classList.remove("opacity-50");
      drag = null;
      if (other) return swap(moving, other);
      if (!t) return;
      var start = hhmm(t.start), end = hhmm(t.start + moving.duration);
      if (t.day === moving.data.day_of_week && start === moving.data.start_time) return;
      post(grid.dataset.moveUrl.replace("/0/", "/" + moving.id + "/"), {
        day_of_week: t.day, start_time: start, end_time: end, room: moving.data.room_id,
      }, "Class moved.");
    });

    function post(url, body, okMessage) {
      fetch(url, {
        method: "POST", credentials: "same-origin",
        headers: KeyTrack.jsonHeaders(), body: JSON.stringify(body),
      }).then(function (res) {
        if (res.status === 403) { KeyTrack.toast("You do not have permission.", "error"); return null; }
        return res.json().catch(function () { return {}; }).then(function (data) {
          return { ok: res.ok, data: data };
        });
      }).then(function (r) {
        if (!r) return;
        if (r.ok) KeyTrack.reloadWithToast(okMessage, "success");
        else KeyTrack.toast(r.data.error || "That move is not possible.", "error");
      }).catch(function () {
        KeyTrack.toast("Could not move the class. Check your connection.", "error");
      });
    }

    function swap(moving, otherCard) {
      var other;
      try { other = JSON.parse(otherCard.getAttribute("data-block")); } catch (err) { return; }
      KeyTrack.confirm({
        title: "Swap these two classes?",
        message: moving.data.title + " and " + other.title +
                 " trade day and time. Both are re-checked first.",
        label: "Swap", subject: moving.data.title,
      }).then(function () {
        var body = new URLSearchParams();
        body.set("block_a", moving.id); body.set("block_b", other.id);
        return fetch(grid.dataset.swapUrl, {
          method: "POST", credentials: "same-origin",
          headers: { "X-CSRFToken": KeyTrack.getCsrfToken(),
                     "X-Requested-With": "XMLHttpRequest" },
          body: body,
        }).then(function (res) {
          return res.json().catch(function () { return {}; }).then(function (data) {
            if (res.ok) KeyTrack.reloadWithToast("Classes swapped.", "success");
            else KeyTrack.toast(data.error || "Those classes cannot be swapped.", "error");
          });
        });
      }, function () { /* cancelled */ });
    }
  }

  function init() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-schedule-grid]"), bind);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
  KeyTrack.scheduleDnd = { bind: bind };
})();
