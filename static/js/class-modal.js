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
    };
    var urls = {
      options: modal.dataset.optionsUrl, place: modal.dataset.placeUrl,
      move: modal.dataset.moveUrl, del: modal.dataset.deleteUrl,
    };
    var subjects = [];        // rows from class-options for the chosen section
    var durationTouched = false;

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
      el.save.disabled = on;
    }

    /* ---- subjects of the chosen section -------------------------------- */

    function fillSubjects(rows, selectedId) {
      subjects = rows;
      el.subject.innerHTML = "";
      var first = document.createElement("option");
      first.value = "";
      first.textContent = rows.length ? "Choose a subject…" : "No subject left to place";
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
        el.subjectHint.textContent = "Only subjects in the section's curriculum for this semester are listed.";
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
      if (s && s.instructor_id) {
        el.instructor.value = String(s.instructor_id);
        el.instructor.disabled = true;
        el.instructorHint.textContent = "Set on the subject offering; change it under Subject Offerings.";
        el.instructorHint.classList.remove("hidden");
      } else {
        el.instructor.disabled = false;
        if (!s) el.instructor.value = "";
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

    el.section.addEventListener("change", function () { clearError(); loadSubjects(); });
    el.subject.addEventListener("change", function () { clearError(); onSubjectChange(); });
    el.end.addEventListener("input", function () { durationTouched = true; });
    el.start.addEventListener("input", function () {
      // Moving the start slides the end with it until the end is set by hand.
      if (!durationTouched && el.start.value) {
        var s = currentSubject();
        var end = minutesOf(el.start.value) + ((s && s.minutes) || 90);
        if (end <= 21 * 60) el.end.value = hhmm(end);
      }
    });

    function syncRepeat() {
      Array.prototype.forEach.call(el.repeat.querySelectorAll("input[type=checkbox]"), function (c) {
        var same = c.value === el.day.value;
        c.disabled = same;
        if (same) c.checked = false;
        c.closest("label").classList.toggle("opacity-40", same);
      });
    }
    el.day.addEventListener("change", syncRepeat);

    /* ---- open ---------------------------------------------------------- */

    function setIf(select, value) {
      if (value === undefined || value === null || value === "") return;
      select.value = String(value);
    }

    function openCreate(opts) {
      opts = opts || {};
      clearError();
      durationTouched = false;
      el.title.textContent = "Add a class";
      el.desc.textContent = "Pick a section and one of its curriculum subjects.";
      el.saveLabel.textContent = "Add class";
      el.blockId.value = "";
      el.section.disabled = false;
      el.instructor.disabled = false;
      el.instructor.dispatchEvent(new Event("change", { bubbles: true }));
      el.instructorHint.classList.add("hidden");
      el.note.classList.add("hidden");
      el.del.classList.add("hidden");
      el.repeat.classList.remove("hidden");
      Array.prototype.forEach.call(el.repeat.querySelectorAll("input"), function (c) { c.checked = false; });
      el.section.value = opts.section ? String(opts.section) : "";
      el.instructor.value = "";
      setIf(el.room, opts.room);
      setIf(el.day, opts.day !== undefined ? opts.day : 0);
      el.start.value = opts.start || "09:00";
      el.end.value = opts.end || hhmm(minutesOf(el.start.value) + 90);
      syncRepeat();
      loadSubjects(opts.subject);
      window.KeyTrackModal.open(MODAL_ID, { trigger: opts.trigger });
      (el.section.value ? el.subject : el.section).focus();
    }

    function openEdit(data, trigger) {
      data = data || {};
      clearError();
      durationTouched = true;
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
      window.KeyTrackModal.open(MODAL_ID, { trigger: trigger });
      el.room.focus();
    }

    /* ---- triggers ------------------------------------------------------ */

    // Room finder "Schedule this": room, day and slot are already chosen.
    document.addEventListener("keytrack:schedule-prefill", function (e) {
      var d = e.detail || {};
      if (d.term_id) el.term.value = String(d.term_id);
      openCreate({ room: d.room_id, day: d.day_of_week, start: d.start_time, end: d.end_time });
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
        if (!el.subject.value) { showError("Choose a subject from the section's curriculum."); el.subject.focus(); return; }
      }
      if (!el.start.value || !el.end.value) { showError("Set a start and an end time."); return; }
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
        body.instructor = el.instructor.value || null;
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
