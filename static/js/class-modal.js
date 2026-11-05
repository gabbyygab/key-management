/* class-modal.js - add / edit one class on the timetable.
 *
 * Drives #class-modal (templates/scheduling/_class_modal.html), shared by the
 * room schedule and the section timetable.
 *
 *   Add:  pick a section, then one of ITS curriculum subjects (loaded from
 *         /api/scheduling/class-options/), a room, a day and a time. The
 *         server files the subject offering when the section has none, so the
 *         Subject Offerings page stays in step.
 *   Edit: only the room, day and time move; the section, subject and
 *         professor belong to the offering.
 *
 * Entry points: [data-class-new] buttons (data-section, data-room, data-day,
 * data-start, data-end), a click on an empty grid cell, a click on a class
 * card, and KeyTrack.classModal.openCreate / openEdit.
 *
 * Availability: #cm-availability shows the chosen day in half hours, greyed
 * where the room, the offering's professor or the section is already booked
 * (GET /api/scheduling/busy/, refetched when the section, subject or room
 * changes; day and time changes only redraw). A clashing time disables Save
 * and names the class in the way; repeat days that clash are greyed too.
 * The server re-checks everything on save regardless.
 *
 * Requires: csrf.js, toast.js, modal.js, confirm.js.
 */
(function () {
  "use strict";

  var KeyTrack = (window.KeyTrack = window.KeyTrack || {});
  var MODAL_ID = "class-modal";

  function hhmm(minutes) {
    var h = Math.floor(minutes / 60), m = minutes % 60;
    return (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m;
  }
  function minutesOf(value) {
    var p = String(value || "").split(":");
    return parseInt(p[0], 10) * 60 + parseInt(p[1] || "0", 10);
  }

  var DAY_START = 7 * 60, DAY_END = 21 * 60, STEP = 30;
  var KIND_LABEL = { room: "Room taken", instructor: "Professor busy", section: "Section busy" };

  function kindsOf(row) {
    return (row.kinds || []).map(function (k) { return KIND_LABEL[k] || k; }).join(" + ");
  }
  function reasonOf(row) {
    return kindsOf(row) + " - " + row.title + (row.room ? ", " + row.room : "") +
           " (" + row.time_display + ")";
  }
  function clashes(busy, day, start, end) {
    return (busy || []).filter(function (r) {
      return r.day === day && minutesOf(r.start) < end && minutesOf(r.end) > start;
    });
  }

  function init() {
    var modal = document.getElementById(MODAL_ID);
    if (!modal) return;
    var $ = function (id) { return document.getElementById(id); };
    var el = {
      blockId: $("cm-block-id"), term: $("cm-term"), section: $("cm-section"),
      subject: $("cm-subject"), subjectHint: $("cm-subject-hint"),
      instructor: $("cm-instructor"), instructorHint: $("cm-instructor-hint"),
      room: $("cm-room"), day: $("cm-day"), start: $("cm-start"), end: $("cm-end"),
      repeat: modal.querySelector("[data-repeat-days]"), note: $("cm-edit-note"),
      error: $("cm-error"), save: $("cm-save"), saveLabel: $("cm-save-label"),
      del: $("cm-delete"), title: $("class-modal-title"), desc: $("class-modal-desc"),
      avail: $("cm-availability"), availTitle: $("cm-avail-title"),
      availStrip: $("cm-avail-strip"), availStatus: $("cm-avail-status"),
      availList: $("cm-avail-list"),
    };
    var urls = {
      options: modal.dataset.optionsUrl, place: modal.dataset.placeUrl,
      move: modal.dataset.moveUrl, del: modal.dataset.deleteUrl,
      busy: modal.dataset.busyUrl,
    };
    var subjects = [];        // rows from class-options for the chosen section
    var durationTouched = false;
    var busy = null;          // rows from /api/scheduling/busy/, null while unknown
    var busyWho = {};         // {room, instructor, section} the rows were checked for
    var busySeq = 0, busyTimer = null;
    var saving = false, clashBlocked = false;

    function showError(msg) {
      if (KeyTrack.toast) {
        KeyTrack.toast(msg, "error");
        el.error.textContent = "";
        el.error.classList.add("hidden");
      } else {
        el.error.textContent = msg;
        el.error.classList.remove("hidden");
      }
    }
    function clearError() { el.error.textContent = ""; el.error.classList.add("hidden"); }
    function setBusy(on) {
      saving = on;
      el.save.disabled = saving || clashBlocked;
    }

    /* ---- availability of the chosen room / professor / section ---------- */

    function busyQuery() {
      if (!urls.busy) return null;
      if (el.blockId.value) {
        return "?block=" + encodeURIComponent(el.blockId.value) +
               (el.room.value ? "&room=" + encodeURIComponent(el.room.value) : "");
      }
      if (!el.term.value) return null;
      var q = "?term=" + encodeURIComponent(el.term.value);
      if (el.section.value) q += "&section=" + encodeURIComponent(el.section.value);
      if (el.subject.value && !el.subject.disabled) q += "&subject=" + encodeURIComponent(el.subject.value);
      if (el.room.value) q += "&room=" + encodeURIComponent(el.room.value);
      return q;
    }

    // Several fields change at once on open; one fetch after they settle.
    function loadBusy() {
      clearTimeout(busyTimer);
      busyTimer = setTimeout(fetchBusy, 60);
    }
    function fetchBusy() {
      var q = busyQuery();
      var seq = ++busySeq;
      if (!q) { busy = null; renderAvail(); return; }
      fetch(urls.busy + q, { credentials: "same-origin",
                             headers: { "X-Requested-With": "XMLHttpRequest" } })
        .then(function (res) { return res.ok ? res.json() : null; })
        .catch(function () { return null; })
        .then(function (data) {
          if (seq !== busySeq) return;            // a newer question is on its way
          busy = data ? data.busy || [] : null;   // unknown: let the server decide
          busyWho = data || {};
          renderAvail();
        });
    }

    function chosenSlot() {
      var day = parseInt(el.day.value, 10);
      var start = el.start.value ? minutesOf(el.start.value) : NaN;
      var end = el.end.value ? minutesOf(el.end.value) : NaN;
      return { day: day, start: start, end: end, valid: !isNaN(start) && !isNaN(end) && end > start };
    }

    function renderAvail() {
      syncRepeat();
      if (!el.avail) return;
      if (busy === null) {
        el.avail.classList.add("hidden");
        clashBlocked = false;
        el.save.disabled = saving;
        el.save.removeAttribute("title");
        return;
      }
      el.avail.classList.remove("hidden");
      var slot = chosenSlot();
      var dayLabel = el.day.options[el.day.selectedIndex]
        ? el.day.options[el.day.selectedIndex].textContent : "";
      el.availTitle.textContent = "Availability · " + dayLabel;

      el.availStrip.innerHTML = "";
      for (var m = DAY_START; m < DAY_END; m += STEP) {
        var cell = document.createElement("div");
        var here = clashes(busy, slot.day, m, m + STEP);
        var chosen = slot.valid && m >= slot.start && m < slot.end;
        cell.dataset.minute = String(m);
        if (here.length && chosen) {
          cell.className = "bg-red-500";
        } else if (here.length) {
          cell.className = "kt-slot-busy";
        } else if (chosen) {
          cell.className = "bg-[#1E6B4A] cursor-pointer";
        } else {
          cell.className = "bg-white cursor-pointer hover:bg-emerald-100";
        }
        cell.title = hhmm(m) + (here.length ? " - taken: " + here.map(reasonOf).join("; ")
                                            : " - free, click to start here");
        el.availStrip.appendChild(cell);
      }

      var hits = slot.valid ? clashes(busy, slot.day, slot.start, slot.end) : [];
      el.availList.innerHTML = "";
      if (hits.length) {
        el.availStatus.className = "mt-2 text-[12px] font-semibold text-red-700";
        el.availStatus.textContent = "This time is taken. Pick a free (white) slot:";
        hits.slice(0, 4).forEach(function (r) {
          var li = document.createElement("li");
          li.textContent = reasonOf(r);
          el.availList.appendChild(li);
        });
        if (hits.length > 4) {
          var more = document.createElement("li");
          more.textContent = "…and " + (hits.length - 4) + " more.";
          el.availList.appendChild(more);
        }
      } else if (slot.valid) {
        var who = [busyWho.room, busyWho.instructor, busyWho.section].filter(Boolean);
        el.availStatus.className = "mt-2 text-[12px] font-medium text-[#1E6B4A]";
        el.availStatus.textContent = who.length
          ? "Free: " + who.join(", ") + (who.length > 1 ? " are" : " is") + " all clear then."
          : "Free at this time.";
      } else {
        el.availStatus.className = "mt-2 text-[12px] text-stone-500";
        el.availStatus.textContent = "Set a start and an end time.";
      }
      clashBlocked = hits.length > 0;
      el.save.disabled = saving || clashBlocked;
      if (clashBlocked) el.save.title = "This time clashes - see Availability above.";
      else el.save.removeAttribute("title");
    }

    // A click on a free half hour moves the start there, keeping the length.
    if (el.availStrip) {
      el.availStrip.addEventListener("click", function (e) {
        var cell = e.target.closest("[data-minute]");
        if (!cell || cell.classList.contains("kt-slot-busy")) return;
        var slot = chosenSlot();
        var length = slot.valid ? slot.end - slot.start : 90;
        var start = parseInt(cell.dataset.minute, 10);
        if (start + length > DAY_END) start = DAY_END - length;
        el.start.value = hhmm(start);
        el.end.value = hhmm(start + length);
        clearError();
        renderAvail();
      });
    }

    /* ---- subjects of the chosen section -------------------------------- */

    function fillSubjects(rows, selectedId) {
      /* A subject whose meetings are all placed is not offered again: move its
         existing class on the timetable instead. Editing keeps its own subject. */
      rows = rows.filter(function (r) {
        return !(r.offering_id && r.meetings > 0 && r.scheduled >= r.meetings)
          || (selectedId && r.id === parseInt(selectedId, 10));
      });
      subjects = rows;
      el.subject.innerHTML = "";
      var first = document.createElement("option");
      first.value = "";
      first.textContent = rows.length ? "Choose a subject…" : "All subjects are placed - move a class instead";
      el.subject.appendChild(first);
      rows.forEach(function (r) {
        var o = document.createElement("option");
        o.value = r.id;
        var progress = r.offering_id
          ? " - " + r.scheduled + " of " + r.meetings + " placed" : "";
        o.textContent = r.label + progress;
        el.subject.appendChild(o);
      });
      el.subject.disabled = !rows.length;
      if (selectedId) el.subject.value = String(selectedId);
      /* Tell the searchable-select widget to sync its trigger button after
         options have been rebuilt and disabled state may have changed. */
      el.subject.dispatchEvent(new Event("change", { bubbles: true }));
      onSubjectChange();
    }

    function loadSubjects(selectedId) {
      if (!el.section.value) {
        fillSubjects([]);
        el.subject.options[0].textContent = "Choose a section first";
        el.subject.dispatchEvent(new Event("change", { bubbles: true }));
        el.subjectHint.textContent = "Only subjects in the section's Subject Offerings for this term are listed.";
        return;
      }
      var q = "?section=" + encodeURIComponent(el.section.value) +
              "&term=" + encodeURIComponent(el.term.value);
      el.subject.disabled = true;
      el.subject.dispatchEvent(new Event("change", { bubbles: true }));
      fetch(urls.options + q, { credentials: "same-origin",
                                headers: { "X-Requested-With": "XMLHttpRequest" } })
        .then(function (res) {
          if (res.status === 403) { KeyTrack.toast("You do not have permission.", "error"); throw new Error("forbidden"); }
          return res.json();
        })
        .then(function (data) {
          fillSubjects(data.subjects || [], selectedId);
          el.subjectHint.textContent = data.message || "";
        })
        .catch(function (err) {
          if (err.message !== "forbidden") showError("Could not load the section's subjects.");
        });
    }

    function currentSubject() {
      var id = parseInt(el.subject.value, 10);
      for (var i = 0; i < subjects.length; i++) if (subjects[i].id === id) return subjects[i];
      return null;
    }

    function onSubjectChange() {
      var s = currentSubject();
      var editing = !!el.blockId.value;
      if (editing) return;
      // The professor always comes from the subject offering; only the room
      // and time are chosen here.
      el.instructor.value = s && s.instructor_id ? String(s.instructor_id) : "";
      el.instructor.disabled = true;
      if (s) {
        el.instructorHint.textContent = s.instructor_id
          ? "Set on the subject offering; change it under Subject Offerings."
          : "No professor on this offering yet; set one under Subject Offerings.";
        el.instructorHint.classList.remove("hidden");
      } else {
        el.instructorHint.classList.add("hidden");
      }
      // Notify searchable-select to sync its trigger button's disabled/value state.
      el.instructor.dispatchEvent(new Event("change", { bubbles: true }));
      // A subject that already has meetings keeps their length by default.
      if (s && el.start.value && !durationTouched) {
        var end = minutesOf(el.start.value) + (s.minutes || 90);
        if (end <= 21 * 60) el.end.value = hhmm(end);
      }
    }

    var syncingSection = false;
    el.section.addEventListener("change", function () {
      if (syncingSection) return;
      clearError(); loadSubjects(); loadBusy();
    });
    el.subject.addEventListener("change", function () { clearError(); onSubjectChange(); loadBusy(); });
    el.room.addEventListener("change", function () { clearError(); loadBusy(); });
    el.end.addEventListener("input", function () { durationTouched = true; renderAvail(); });
    el.end.addEventListener("change", renderAvail);
    el.start.addEventListener("input", function () {
      // Moving the start slides the end with it until the end is set by hand.
      if (!durationTouched && el.start.value) {
        var s = currentSubject();
        var end = minutesOf(el.start.value) + ((s && s.minutes) || 90);
        if (end <= 21 * 60) el.end.value = hhmm(end);
      }
      renderAvail();
    });
    el.start.addEventListener("change", renderAvail);

    // The class's own day cannot repeat; neither can a day where it clashes.
    function syncRepeat() {
      var slot = chosenSlot();
      Array.prototype.forEach.call(el.repeat.querySelectorAll("input[type=checkbox]"), function (c) {
        var same = c.value === el.day.value;
        var hits = (!same && busy && slot.valid)
          ? clashes(busy, parseInt(c.value, 10), slot.start, slot.end) : [];
        var off = same || hits.length > 0;
        c.disabled = off;
        if (off) c.checked = false;
        var label = c.closest("label");
        label.classList.toggle("opacity-40", off);
        label.classList.toggle("cursor-not-allowed", hits.length > 0);
        if (hits.length) label.title = "Taken then: " + hits.map(reasonOf).join("; ");
        else label.removeAttribute("title");
      });
    }
    el.day.addEventListener("change", function () { clearError(); renderAvail(); });

    /* ---- open ---------------------------------------------------------- */

    function setIf(select, value) {
      if (value === undefined || value === null || value === "") return;
      select.value = String(value);
    }

    function openCreate(opts) {
      opts = opts || {};
      clearError();
      // keepDuration: the caller already chose the length (the room finder knows
      // whether this is the lecture or the lab), so loading the subject must not
      // reset the end time to the offering's default meeting length.
      durationTouched = !!opts.keepDuration;
      busy = null; busySeq++;
      el.title.textContent = "Add a class";
      el.desc.textContent = "Pick a section and one of its offered subjects.";
      el.saveLabel.textContent = "Add class";
      el.blockId.value = "";
      el.instructor.disabled = true;
      el.instructor.dispatchEvent(new Event("change", { bubbles: true }));
      el.instructorHint.classList.add("hidden");
      el.note.classList.add("hidden");
      el.del.classList.add("hidden");
      el.repeat.classList.remove("hidden");
      Array.prototype.forEach.call(el.repeat.querySelectorAll("input"), function (c) { c.checked = false; });
      el.section.value = opts.section ? String(opts.section) : "";
      // Opened from a section's own timetable: the section is already chosen
      // there, so it is fixed. From the room timetable it stays pickable.
      el.section.disabled = !!opts.section;
      syncingSection = true;   // only refresh the searchable-select trigger
      el.section.dispatchEvent(new Event("change", { bubbles: true }));
      syncingSection = false;
      el.instructor.value = "";
      setIf(el.room, opts.room);
      // Refresh the searchable room picker's label (it only shows what it was
      // told); ss-sync updates the trigger without firing the change handlers.
      el.room.dispatchEvent(new Event("ss-sync"));
      setIf(el.day, opts.day !== undefined ? opts.day : 0);
      el.start.value = opts.start || "09:00";
      el.end.value = opts.end || hhmm(minutesOf(el.start.value) + 90);
      renderAvail();
      loadSubjects(opts.subject);
      loadBusy();
      window.KeyTrackModal.open(MODAL_ID, { trigger: opts.trigger });
      (el.section.value ? el.subject : el.section).focus();
    }

    function openEdit(data, trigger) {
      data = data || {};
      clearError();
      durationTouched = true;
      busy = null; busySeq++;
      el.title.textContent = data.title || "Edit class";
      el.desc.textContent = "Move it to another room, day or time.";
      el.saveLabel.textContent = "Save changes";
      el.blockId.value = data.id || "";
      // Show what the class is, read only.
      el.section.value = data.section_id ? String(data.section_id) : "";
      el.section.disabled = true;
      el.subject.innerHTML = "";
      var o = document.createElement("option");
      o.textContent = data.subject_code || data.title || "";
      el.subject.appendChild(o);
      el.subject.disabled = true;
      el.subject.dispatchEvent(new Event("change", { bubbles: true }));
      el.subjectHint.textContent = "";
      el.instructor.value = data.instructor_id ? String(data.instructor_id) : "";
      el.instructor.disabled = true;
      el.instructor.dispatchEvent(new Event("change", { bubbles: true }));
      el.instructorHint.classList.add("hidden");
      el.note.classList.remove("hidden");
      el.repeat.classList.add("hidden");
      el.del.classList.remove("hidden");
      setIf(el.room, data.room_id);
      setIf(el.day, data.day_of_week);
      el.start.value = data.start_time || "";
      el.end.value = data.end_time || "";
      // Refresh the searchable room picker's label without a refetch storm.
      el.room.dispatchEvent(new Event("change", { bubbles: true }));
      renderAvail();
      loadBusy();
      window.KeyTrackModal.open(MODAL_ID, { trigger: trigger });
      el.room.focus();
    }

    /* ---- triggers ------------------------------------------------------ */

    // Room finder "Schedule this": room, day and slot are already chosen.
    document.addEventListener("keytrack:schedule-prefill", function (e) {
      var d = e.detail || {};
      if (d.term_id) el.term.value = String(d.term_id);
      // The subject room finder also knows the class: its section and subject.
      openCreate({ room: d.room_id, day: d.day_of_week, start: d.start_time, end: d.end_time,
                   section: d.section_id, subject: d.subject_id, trigger: d.trigger,
                   keepDuration: true });
    });

    document.addEventListener("click", function (e) {
      var add = e.target.closest("[data-class-new]");
      if (add) {
        e.preventDefault();
        openCreate({
          section: add.dataset.section, subject: add.dataset.subject, room: add.dataset.room, day: add.dataset.day,
          start: add.dataset.start, end: add.dataset.end, trigger: add,
        });
        return;
      }
      if (e.target.closest("[data-swap-pick]")) return;
      var cell = e.target.closest("[data-slot-day]");
      var card = e.target.closest("[data-block]");
      if (card && !e.target.closest("a, button")) {
        var data;
        try { data = JSON.parse(card.getAttribute("data-block")); } catch (err) { data = null; }
        if (data) { e.preventDefault(); openEdit(data, card); }
        return;
      }
      if (cell) {
        var grid = cell.closest("[data-schedule-grid]");
        var start = cell.dataset.slotTime;
        openCreate({
          day: cell.dataset.slotDay, start: start,
          end: hhmm(Math.min(minutesOf(start) + 90, 21 * 60)),
          section: grid && grid.dataset.section, room: grid && grid.dataset.room,
          trigger: cell,
        });
      }
    });

    /* ---- save ---------------------------------------------------------- */

    function request(url, body) {
      return fetch(url, {
        method: "POST", headers: KeyTrack.jsonHeaders(), credentials: "same-origin",
        body: body === undefined ? undefined : JSON.stringify(body),
      }).then(function (res) {
        if (KeyTrack.isSessionExpired && KeyTrack.isSessionExpired(res)) {
          window.KeyTrackModal.close(MODAL_ID);
          KeyTrack.handleSessionExpired();
          throw new Error("session_expired");
        }
        if (res.status === 403) {
          KeyTrack.toast("You do not have permission.", "error");
          window.KeyTrackModal.close(MODAL_ID);
          throw new Error("forbidden");
        }
        return res.json().catch(function () { return {}; }).then(function (data) {
          return { ok: res.ok, data: data };
        });
      });
    }

    function save() {
      clearError();
      var editing = !!el.blockId.value;
      if (!editing) {
        if (!el.section.value) { showError("Choose a section."); el.section.focus(); return; }
        if (!el.subject.value) { showError("Choose a subject from the section's offerings."); el.subject.focus(); return; }
      }
      if (!el.start.value || !el.end.value) { showError("Set a start and an end time."); return; }
      if (clashBlocked) { showError("That time is taken - pick a free slot under Availability."); return; }
      var body = {
        room: el.room.value, day_of_week: parseInt(el.day.value, 10),
        start_time: el.start.value, end_time: el.end.value,
      };
      var url;
      if (editing) {
        url = urls.move.replace("/0/", "/" + encodeURIComponent(el.blockId.value) + "/");
      } else {
        url = urls.place;
        body.term = el.term.value; body.section = el.section.value;
        body.subject = el.subject.value;
        body.repeat_days = Array.prototype.map.call(
          el.repeat.querySelectorAll("input:checked:not(:disabled)"),
          function (c) { return parseInt(c.value, 10); });
      }
      setBusy(true);
      request(url, body).then(function (r) {
        if (r.ok) {
          var n = r.data.created || 1;
          window.KeyTrackModal.close(MODAL_ID);
          KeyTrack.reloadWithToast(editing ? "Class moved."
            : n > 1 ? n + " classes added." : "Class added.", "success");
          return;
        }
        showError(r.data.error || "That could not be saved.");
      }).catch(function (err) {
        if (err.message === "forbidden" || err.message === "session_expired") return;
        showError("Could not save. Check your connection and try again.");
      }).then(function () { setBusy(false); });
    }

    el.save.addEventListener("click", function (e) { e.preventDefault(); save(); });
    $("class-form").addEventListener("submit", function (e) { e.preventDefault(); save(); });

    /* ---- remove -------------------------------------------------------- */

    el.del.addEventListener("click", function () {
      var id = el.blockId.value;
      if (!id) return;
      KeyTrack.confirm({
        title: "Remove from the timetable",
        message: "This meeting leaves the section, room and offering schedules. The subject offering stays, with one meeting left to place.",
        subject: el.title.textContent, label: "Remove", variant: "danger",
      }).then(function () {
        return request(urls.del.replace("/0/", "/" + encodeURIComponent(id) + "/")).then(function (r) {
          if (r.ok) {
            window.KeyTrackModal.close(MODAL_ID);
            KeyTrack.reloadWithToast("Class removed from the timetable.", "success");
          } else {
            showError(r.data.error || "Could not remove the class.");
          }
        });
      }, function () { /* cancelled */ });
    });

    KeyTrack.classModal = { openCreate: openCreate, openEdit: openEdit };
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
