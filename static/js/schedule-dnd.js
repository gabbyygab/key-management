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
 * While a class is dragged, every half hour where its room, its professor or
 * its section is already booked turns grey (GET /api/scheduling/busy/?block=,
 * fetched on mousedown so it is ready by the time the drag starts). Hovering
 * a grey slot shows a red outline with the reason, and dropping there is
 * refused without a request. The server still re-checks all three; a clash
 * answers 422 with the reason and nothing changes. The grid is read from the
 * DOM rather than assumed, so the room and section pages share this file.
 *
 * Dragging near an edge of the timetable's scroll box scrolls it, so a class
 * can be moved to an hour that is off-screen. Auto-scroll works both before
 * the pointer reaches the edge (72px hot zone) and while it is outside the
 * box, with the speed ramping up over that distance.
 *
 * Requires: csrf.js, toast.js, confirm.js. Dragging is mouse-only; keyboard
 * users open the card and change the room, day and time in the dialog.
 */
(function () {
  "use strict";

  var KeyTrack = (window.KeyTrack = window.KeyTrack || {});
  var START = 7 * 60, END = 21 * 60, STEP = 30;
  var SCROLL_EDGE = 72;   // px inside the scroll box where auto-scroll starts
  var SCROLL_MAX = 10;    // px per 60 fps frame at full speed (~600 px/s)

  // Nearest ancestor that can scroll; the grid itself sits inside
  // .timetable-scroll, which is what has the max-height and scrollbar.
  function scrollParentOf(el) {
    for (var p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      var s = window.getComputedStyle(p);
      if (/(auto|scroll|overlay)/.test(s.overflowX) ||
          /(auto|scroll|overlay)/.test(s.overflowY)) return p;
    }
    return null;
  }

  function minutesOf(v) {
    var p = String(v).split(":");
    return parseInt(p[0], 10) * 60 + parseInt(p[1] || "0", 10);
  }
  function hhmm(m) {
    var h = Math.floor(m / 60), mm = m % 60;
    return (h < 10 ? "0" : "") + h + ":" + (mm < 10 ? "0" : "") + mm;
  }

  var KIND_LABEL = { room: "Room taken", instructor: "Professor busy", section: "Section busy" };

  function shortReasonOf(row) {
    return (row.kinds || []).map(function (k) { return KIND_LABEL[k] || k; }).join(" + ");
  }
  /* "Professor busy - CHEM1 · BSBIO-1C, CCS-201 (8:30 - 10:00 AM)" */
  function reasonOf(row) {
    return shortReasonOf(row) + " - " + row.title + (row.room ? ", " + row.room : "") +
           " (" + row.time_display + ")";
  }

  /* Rows of ``busy`` that overlap ``start``..``end`` (minutes) on ``day``. */
  function clashes(busy, day, start, end) {
    return (busy || []).filter(function (r) {
      return r.day === day && minutesOf(r.start) < end && minutesOf(r.end) > start;
    });
  }

  function bind(grid) {
    if (grid.dataset.dndBound === "1") return;
    grid.dataset.dndBound = "1";
    var drag = null;   // {id, data, card, grabRows, duration, busy}
    var preview = null;
    var busyCache = {};   // block id -> Promise of busy rows (a page reload follows every change)
    var cells = null;     // "day|HH:MM" -> slot cell
    var shaded = [];
    var scroller = scrollParentOf(grid);   // the box with max-height/scrollbar
    var auto = { raf: 0, last: 0, x: 0, y: 0 };   // last pointer + loop state

    function loadBusy(id) {
      var url = grid.dataset.busyUrl;
      if (!url || !id) return Promise.resolve(null);
      if (!busyCache[id]) {
        busyCache[id] = fetch(url + "?block=" + encodeURIComponent(id), {
          credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" },
        }).then(function (res) {
          return res.ok ? res.json() : null;
        }).then(function (data) {
          return data ? data.busy || [] : null;
        }).catch(function () { return null; }).then(function (rows) {
          if (rows === null) delete busyCache[id];   // try again on the next drag
          return rows;
        });
      }
      return busyCache[id];
    }

    function cellMap() {
      if (!cells) {
        cells = {};
        Array.prototype.forEach.call(grid.querySelectorAll("[data-slot-day][data-slot-time]"), function (c) {
          cells[c.getAttribute("data-slot-day") + "|" + c.getAttribute("data-slot-time")] = c;
        });
      }
      return cells;
    }

    // Grey every half hour a busy row touches; the cell's tooltip says why.
    function shade(rows) {
      unshade();
      var map = cellMap(), reasons = {};
      rows.forEach(function (r) {
        var s = Math.max(START, minutesOf(r.start)), e = Math.min(END, minutesOf(r.end));
        for (var m = s - ((s - START) % STEP); m < e; m += STEP) {
          var key = r.day + "|" + hhmm(m);
          if (!map[key]) continue;
          (reasons[key] = reasons[key] || []).push(reasonOf(r));
        }
      });
      Object.keys(reasons).forEach(function (key) {
        var c = map[key];
        c.setAttribute("data-title-was", c.getAttribute("title") || "");
        c.setAttribute("title", "Taken: " + reasons[key].join("; "));
        c.classList.add("kt-slot-busy");
        shaded.push(c);
      });
    }
    function unshade() {
      shaded.forEach(function (c) {
        c.classList.remove("kt-slot-busy");
        c.setAttribute("title", c.getAttribute("data-title-was") || "");
        c.removeAttribute("data-title-was");
      });
      shaded = [];
    }

    // Fetch before the drag starts, so the grey slots show at once.
    grid.addEventListener("mousedown", function (e) {
      var card = e.target.closest && e.target.closest("[data-block]");
      if (!card || !grid.contains(card)) return;
      try { loadBusy(JSON.parse(card.getAttribute("data-block")).id); } catch (err) { /* ignore */ }
    });

    function geometry() {
      // Cell of Monday 07:00 anchors the grid; one cell gives the step sizes.
      var origin = grid.querySelector('[data-slot-day="0"][data-slot-time="07:00"]');
      var right = grid.querySelector('[data-slot-day="5"][data-slot-time="07:00"]');
      if (!origin || !right) return null;
      var a = origin.getBoundingClientRect(), b = right.getBoundingClientRect();
      return { left: a.left, top: a.top, w: a.width, h: a.height, right: b.right };
    }

    // Where the pointer is on the grid, as {day, row}; null when outside it.
    function locate(x, y, geo) {
      var col = Math.floor((x - geo.left) / geo.w);
      var row = Math.floor((y - geo.top) / geo.h);
      if (col < 0 || col > 5) return null;
      return { day: col, row: row };
    }

    function targetAt(x, y) {
      var geo = geometry();
      if (!geo || !drag) return null;
      var at = locate(x, y, geo);
      if (!at) return null;
      var startRow = at.row - drag.grabRows;
      var rows = drag.duration / STEP;
      startRow = Math.max(0, Math.min(startRow, (END - START) / STEP - rows));
      return { day: at.day, row: startRow, rows: rows, geo: geo,
               start: START + startRow * STEP };
    }

    function showPreview(t, ok, label) {
      if (!preview) {
        preview = document.createElement("div");
        preview.setAttribute("aria-hidden", "true");
        preview.className = "pointer-events-none absolute z-30 overflow-hidden rounded-lg border-2 border-dashed p-1 text-[11px] font-semibold leading-tight text-red-700";
        grid.appendChild(preview);
      }
      preview.textContent = ok ? "" : (label || "Taken");
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

    // Draw (or hide) the drop outline for a pointer position. Called from
    // dragover and from the auto-scroll loop, so the outline keeps following
    // the grid while it scrolls under a still pointer.
    function updatePreviewAt(x, y) {
      if (!drag) return;
      if (cardAtPoint(x, y)) { hidePreview(); return; }
      var t = targetAt(x, y);
      if (!t) { hidePreview(); return; }
      var hit = clashes(drag.busy, t.day, t.start, t.start + drag.duration);
      showPreview(t, !hit.length, hit.length ? shortReasonOf(hit[0]) : "");
    }

    function cardAtPoint(x, y) {
      var els = document.elementsFromPoint(x, y);
      for (var i = 0; i < els.length; i++) {
        var c = els[i].closest && els[i].closest("[data-block]");
        if (c && grid.contains(c) && c !== drag.card) return c;
      }
      return null;
    }

    /* ------------------------------------------------------------------
     * Edge auto-scroll while dragging.
     *
     * The scroll container is a separate ancestor of the grid, and a
     * dragover bound to the grid stops firing the moment the pointer leaves
     * the grid. So the pointer is tracked at document level (capture) for as
     * long as a drag is live, and a requestAnimationFrame loop nudges the
     * scroll box while the pointer is in an edge hot zone (or outside it).
     *
     * Speed ramps quadratically from 0 at the inner edge of the hot zone to
     * SCROLL_MAX at the box edge; outside the box it stays at SCROLL_MAX.
     * The ramp is what makes the start gentle instead of a jump. Steps are
     * scaled by frame time so 60 Hz and 120 Hz screens scroll at the same
     * px/second, and the loop stops once the pointer leaves the hot zones or
     * the box cannot scroll any further. The next dragover restarts it.
     * ------------------------------------------------------------------ */
    function speedToward(distance) {
      var t = 1 - Math.max(0, distance) / SCROLL_EDGE;
      if (t <= 0) return 0;
      t = Math.min(1, t);
      return SCROLL_MAX * t * t;
    }

    function autoScrollStep(ts) {
      auto.raf = 0;
      if (!drag || !scroller) return;
      var rect = scroller.getBoundingClientRect();
      var dx = 0, dy = 0;

      if (scroller.scrollWidth > scroller.clientWidth + 1) {
        var left = auto.x - rect.left, right = rect.right - auto.x;
        if (left < SCROLL_EDGE) dx = -speedToward(left);
        else if (right < SCROLL_EDGE) dx = speedToward(right);
      }
      if (scroller.scrollHeight > scroller.clientHeight + 1) {
        var top = auto.y - rect.top, bottom = rect.bottom - auto.y;
        if (top < SCROLL_EDGE) dy = -speedToward(top);
        else if (bottom < SCROLL_EDGE) dy = speedToward(bottom);
      }
      if (!dx && !dy) return;

      var dt = auto.last ? Math.min(ts - auto.last, 50) : 16.7;
      auto.last = ts;
      var scale = dt / 16.7;
      var beforeX = scroller.scrollLeft, beforeY = scroller.scrollTop;
      scroller.scrollLeft = beforeX + dx * scale;
      scroller.scrollTop = beforeY + dy * scale;
      if (scroller.scrollLeft === beforeX && scroller.scrollTop === beforeY) return;

      updatePreviewAt(auto.x, auto.y);
      auto.raf = requestAnimationFrame(autoScrollStep);
    }

    function stopAutoScroll() {
      if (auto.raf) cancelAnimationFrame(auto.raf);
      auto.raf = 0;
    }

    document.addEventListener("dragover", function (e) {
      if (!drag) return;
      auto.x = e.clientX;
      auto.y = e.clientY;
      updatePreviewAt(auto.x, auto.y);
      if (!auto.raf) {
        auto.last = 0;
        auto.raf = requestAnimationFrame(autoScrollStep);
      }
    }, true);

    grid.addEventListener("dragstart", function (e) {
      var card = e.target.closest && e.target.closest("[data-block]");
      if (!card || !grid.contains(card)) return;
      var data;
      try { data = JSON.parse(card.getAttribute("data-block")); } catch (err) { return; }
      var geo = geometry();
      if (!geo || !data.id) { e.preventDefault(); return; }
      stopAutoScroll();
      auto.x = e.clientX;
      auto.y = e.clientY;
      auto.last = 0;
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
      var dragging = drag;
      loadBusy(data.id).then(function (rows) {
        if (drag !== dragging || !rows) return;   // dropped already, or no data
        dragging.busy = rows;
        shade(rows);
      });
    });

    grid.addEventListener("dragover", function (e) {
      if (!drag) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
    });

    grid.addEventListener("dragend", function () {
      stopAutoScroll();
      if (drag) drag.card.classList.remove("opacity-50");
      hidePreview();
      unshade();
      drag = null;
    });

    grid.addEventListener("drop", function (e) {
      if (!drag) return;
      e.preventDefault();
      stopAutoScroll();
      var moving = drag;
      var other = cardAtPoint(e.clientX, e.clientY);
      var t = targetAt(e.clientX, e.clientY);
      hidePreview();
      unshade();
      moving.card.classList.remove("opacity-50");
      drag = null;
      if (other) return swap(moving, other);
      if (!t) return;
      var start = hhmm(t.start), end = hhmm(t.start + moving.duration);
      if (t.day === moving.data.day_of_week && start === moving.data.start_time) return;
      var hit = clashes(moving.busy, t.day, t.start, t.start + moving.duration);
      if (hit.length) {
        KeyTrack.toast("Cannot move " + moving.data.title + " there. " + reasonOf(hit[0]) +
                       (hit.length > 1 ? " and " + (hit.length - 1) + " more." : "."), "error");
        return;
      }
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
  KeyTrack.scheduleDnd = { bind: bind, clashes: clashes, reasonOf: reasonOf,
                          shortReasonOf: shortReasonOf };
})();
