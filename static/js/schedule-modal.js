/* schedule-modal.js — the shared schedule-block editor (F5.2).
 *
 * One controller for #schedule-form-modal wherever it is included: the room
 * timetable, the schedule list and the room finder. Entry points:
 *
 *   - [data-schedule-grid] "schedule:create" / "schedule:edit" events
 *     (dispatched by schedule-grid.js on cell / block clicks);
 *   - any [data-schedule-new] button (optional data-day, data-start, data-end,
 *     data-room, data-term attributes pre-fill the form);
 *   - any [data-schedule-edit] element carrying data-block='{"id":..}' JSON;
 *   - document "keytrack:schedule-prefill" (room finder "Schedule this");
 *   - KeyTrack.scheduleModal.openCreate(opts) / openEdit(data).
 *
 * Saves go to the JSON block API (data-save-url on the form); deactivating
 * uses the confirm modal and the soft-delete endpoint. "Repeat on" days
 * create one block per extra weekday, atomically, on the server.
 *
 * Requires: csrf.js, toast.js, modal.js, schedule-grid.js (conflict checks),
 * confirm.js (optional; falls back to native confirm).
 */
(function () {
  "use strict";

  var KeyTrack = (window.KeyTrack = window.KeyTrack || {});
  var MODAL_ID = "schedule-form-modal";

  function init() {
    var modal = document.getElementById(MODAL_ID);
    if (!modal) return;
    var form = document.getElementById("schedule-block-form");

    var $ = function (id) { return document.getElementById(id); };
    var titleInput = $("modal-title");
    var subjectSelect = $("modal-subject");
    var instructorSelect = $("modal-instructor");
    var daySelect = $("modal-day");
    var startInput = $("modal-start-time");
    var endInput = $("modal-end-time");
    var activeCheck = $("modal-is-active");
    var blockIdInput = $("modal-block-id");
    var roomSelect = $("modal-room");
    var termSelect = $("modal-term");
    var heading = $("schedule-modal-title");
    var desc = $("schedule-modal-desc");
    var saveBtn = $("modal-save-btn");
    var saveLabel = $("modal-save-label");
    var saveSpinner = $("modal-save-spinner");
    var deleteBtn = $("modal-delete-btn");
    var conflictBox = $("schedule-conflict-container");
    var errorAlert = $("modal-error-alert");
    var repeatWrap = modal.querySelector("[data-repeat-days]");

    var saveUrl = form.getAttribute("data-save-url");
    var deleteUrlTemplate = form.getAttribute("data-delete-url");

    /* ---- inline field errors ------------------------------------------ */

    function fieldGroup(name) {
      return form.querySelector('[data-form-field][data-field-name="' + name + '"]');
    }

    function clearFieldError(name) {
      var group = fieldGroup(name);
      if (!group) return;
      var control = group.querySelector("[data-form-control], input, select, textarea");
      var error = group.querySelector("[data-form-field-error]");
      if (control) {
        control.classList.remove("border-red-500", "text-red-700");
        control.removeAttribute("aria-invalid");
      }
      if (error) {
        error.classList.add("hidden");
        error.textContent = "";
      }
    }

    function setFieldError(name, message) {
      var group = fieldGroup(name);
      if (!group) return false;
      var control = group.querySelector("[data-form-control], input, select, textarea");
      if (!control) return false;
      var error = group.querySelector("[data-form-field-error]");
      if (!error) {
        error = document.createElement("p");
        error.id = (control.id || name) + "-error";
        error.setAttribute("data-form-field-error", "");
        error.setAttribute("role", "alert");
        error.className = "mt-1 text-[12px] text-red-700";
        group.appendChild(error);
      }
      control.classList.add("border-red-500", "text-red-700");
      control.setAttribute("aria-invalid", "true");
      control.setAttribute("aria-describedby", error.id);
      error.textContent = message;
      error.classList.remove("hidden");
      return true;
    }

    function showAlert(msg) {
      if (!errorAlert) return;
      errorAlert.textContent = msg;
      errorAlert.classList.remove("hidden");
    }

    function clearErrors() {
      Array.prototype.forEach.call(form.querySelectorAll("[data-form-field]"), function (g) {
        clearFieldError(g.getAttribute("data-field-name"));
      });
      if (errorAlert) { errorAlert.classList.add("hidden"); errorAlert.textContent = ""; }
      if (conflictBox) { conflictBox.classList.add("hidden"); conflictBox.textContent = ""; }
      setSaveEnabled(true);
    }

    function setSaveEnabled(on) {
      if (!saveBtn) return;
      saveBtn.disabled = !on;
      saveBtn.classList.toggle("opacity-50", !on);
      saveBtn.classList.toggle("cursor-not-allowed", !on);
    }

    form.addEventListener("input", function (e) { if (e.target.name) clearFieldError(e.target.name); });
    form.addEventListener("change", function (e) { if (e.target.name) clearFieldError(e.target.name); });

    /* ---- live conflict check ------------------------------------------ */

    var checker = KeyTrack.scheduleGrid
      ? KeyTrack.scheduleGrid.conflictChecker(function (result) {
          conflictBox.classList.remove("hidden");
          KeyTrack.scheduleGrid.renderConflictState(conflictBox, saveBtn, result);
        })
      : null;

    function runConflictCheck() {
      if (!checker) return;
      var payload = {
        room: roomSelect && roomSelect.value,
        term: termSelect && termSelect.value,
        day_of_week: daySelect && daySelect.value,
        start_time: startInput && startInput.value,
        end_time: endInput && endInput.value,
        exclude_block_id: (blockIdInput && blockIdInput.value) || null,
      };
      if (!payload.room || !payload.term || payload.day_of_week === "" ||
          !payload.start_time || !payload.end_time) {
        conflictBox.classList.add("hidden");
        setSaveEnabled(true);
        return;
      }
      checker.run(payload);
    }

    [roomSelect, termSelect, daySelect, startInput, endInput].forEach(function (el) {
      if (!el) return;
      el.addEventListener("input", runConflictCheck);
      el.addEventListener("change", runConflictCheck);
    });

    /* ---- open --------------------------------------------------------- */

    function setRepeatVisible(on) {
      if (!repeatWrap) return;
      repeatWrap.classList.toggle("hidden", !on);
      Array.prototype.forEach.call(repeatWrap.querySelectorAll("input[type=checkbox]"), function (c) {
        c.checked = false;
      });
    }

    function syncRepeatDisabled() {
      /* The block's own day is implied; don't offer it as a repeat. */
      if (!repeatWrap || !daySelect) return;
      Array.prototype.forEach.call(repeatWrap.querySelectorAll("input[type=checkbox]"), function (c) {
        var same = c.value === daySelect.value;
        c.disabled = same;
        if (same) c.checked = false;
        c.closest("label").classList.toggle("opacity-40", same);
      });
    }
    if (daySelect) daySelect.addEventListener("change", syncRepeatDisabled);

    function setIf(el, value) {
      if (el && value !== undefined && value !== null && value !== "") el.value = String(value);
    }

    function openCreate(opts) {
      opts = opts || {};
      clearErrors();
      heading.textContent = "Add schedule block";
      desc.textContent = opts.room_label
        ? "Schedule a class or booking in " + opts.room_label + "."
        : "Schedule a class or recurring booking.";
      saveLabel.textContent = "Create block";
      blockIdInput.value = "";
      titleInput.value = opts.title || "";
      if (subjectSelect) subjectSelect.value = "";
      if (instructorSelect) instructorSelect.value = "";
      setIf(roomSelect, opts.room);
      setIf(termSelect, opts.term);
      setIf(daySelect, opts.day);
      setIf(startInput, opts.start);
      setIf(endInput, opts.end);
      if (activeCheck) activeCheck.checked = true;
      if (deleteBtn) deleteBtn.classList.add("hidden");
      setRepeatVisible(true);
      syncRepeatDisabled();
      window.KeyTrackModal.open(MODAL_ID, { trigger: opts.trigger });
      titleInput.focus();
      runConflictCheck();
    }

    function openEdit(data, trigger) {
      data = data || {};
      clearErrors();
      heading.textContent = "Edit schedule block";
      desc.textContent = "Update the room, time or instructor. Changes apply to every week.";
      saveLabel.textContent = "Save changes";
      blockIdInput.value = data.id || "";
      titleInput.value = data.title || "";
      if (subjectSelect) subjectSelect.value = data.subject_id || "";
      if (instructorSelect) instructorSelect.value = data.instructor_id || "";
      setIf(roomSelect, data.room_id);
      setIf(termSelect, data.term_id);
      setIf(daySelect, data.day_of_week);
      setIf(startInput, data.start_time);
      setIf(endInput, data.end_time);
      if (activeCheck) activeCheck.checked = data.is_active !== false;
      if (deleteBtn) deleteBtn.classList.toggle("hidden", data.is_active === false);
      setRepeatVisible(false);
      window.KeyTrackModal.open(MODAL_ID, { trigger: trigger });
      titleInput.focus();
      runConflictCheck();
    }

    function parseBlock(el) {
      try { return JSON.parse(el.getAttribute("data-block") || "{}"); }
      catch (e) { return {}; }
    }

    /* ---- triggers ----------------------------------------------------- */

    document.addEventListener("schedule:create", function (e) {
      var d = e.detail || {};
      openCreate({ day: d.day, start: d.startTime, end: d.endTime });
    });
    document.addEventListener("schedule:edit", function (e) {
      var el = document.querySelector('[data-block-id="' + e.detail.blockId + '"]');
      if (el) openEdit(parseBlock(el), el);
    });
    document.addEventListener("keytrack:schedule-prefill", function (e) {
      var d = e.detail || {};
      openCreate({
        room: d.room_id, room_label: d.room_label, day: d.day_of_week,
        start: d.start_time, end: d.end_time, term: d.term_id,
      });
    });
    document.addEventListener("click", function (e) {
      var create = e.target.closest("[data-schedule-new]");
      if (create) {
        e.preventDefault();
        openCreate({
          day: create.dataset.day, start: create.dataset.start, end: create.dataset.end,
          room: create.dataset.room, term: create.dataset.term, trigger: create,
        });
        return;
      }
      var edit = e.target.closest("[data-schedule-edit]");
      if (edit) {
        e.preventDefault();
        openEdit(parseBlock(edit), edit);
      }
    });

    /* ---- save --------------------------------------------------------- */

    function repeatDays() {
      if (!repeatWrap || repeatWrap.classList.contains("hidden")) return [];
      return Array.prototype.map.call(
        repeatWrap.querySelectorAll("input[type=checkbox]:checked:not(:disabled)"),
        function (c) { return parseInt(c.value, 10); }
      );
    }

    function save() {
      clearErrors();
      if (!titleInput.value.trim()) {
        setFieldError("title", "Title / class name is required.");
        titleInput.focus();
        return;
      }
      var payload = {
        block_id: blockIdInput.value || null,
        room: roomSelect ? roomSelect.value : null,
        term: termSelect ? termSelect.value : null,
        title: titleInput.value.trim(),
        subject: subjectSelect ? subjectSelect.value || null : null,
        instructor: instructorSelect ? instructorSelect.value || null : null,
        day_of_week: parseInt(daySelect.value, 10),
        start_time: startInput.value,
        end_time: endInput.value,
        is_active: activeCheck ? activeCheck.checked : true,
        repeat_days: repeatDays(),
      };

      setSaveEnabled(false);
      if (saveSpinner) saveSpinner.classList.remove("hidden");

      fetch(saveUrl, {
        method: "POST",
        headers: KeyTrack.jsonHeaders(),
        body: JSON.stringify(payload),
        credentials: "same-origin",
      })
        .then(function (res) {
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
          return res.json().then(function (data) { return { ok: res.ok, data: data }; });
        })
        .then(function (result) {
          if (result.ok) {
            var n = result.data.created || 1;
            window.KeyTrackModal.close(MODAL_ID);
            KeyTrack.toast(payload.block_id ? "Schedule block updated."
                           : n > 1 ? n + " schedule blocks created." : "Schedule block created.",
                           "success");
            window.location.reload();
            return;
          }
          var errs = result.data || {};
          var placed = false;
          if (errs.error) {
            showAlert(errs.error);
            return;
          }
          Object.keys(errs).forEach(function (k) {
            var msg = Array.isArray(errs[k]) ? errs[k].join(" ") : String(errs[k]);
            if (k === "__all__") showAlert(msg);
            else if (setFieldError(k, msg)) placed = true;
            else showAlert(msg);
          });
          if (!placed && !errs.__all__ && errorAlert.classList.contains("hidden")) {
            showAlert("Please correct the errors.");
          }
        })
        .catch(function (err) {
          if (err.message === "forbidden" || err.message === "session_expired") return;
          if (KeyTrack.networkErrorToast) {
            KeyTrack.networkErrorToast(save, "Could not save the schedule block.");
          } else {
            showAlert("Could not save the schedule block. Check your connection and try again.");
          }
        })
        .then(function () {
          setSaveEnabled(true);
          if (saveSpinner) saveSpinner.classList.add("hidden");
        });
    }

    if (saveBtn) saveBtn.addEventListener("click", function (e) { e.preventDefault(); save(); });
    form.addEventListener("submit", function (e) { e.preventDefault(); save(); });

    /* ---- deactivate (soft delete) ------------------------------------- */

    function deactivate() {
      var id = blockIdInput.value;
      if (!id) return;
      var ask = KeyTrack.confirm
        ? KeyTrack.confirm({
            title: "Deactivate schedule block",
            message: "It stops appearing on the timetable and no longer blocks the room. You can reactivate it from the schedule list.",
            subject: titleInput.value,
            label: "Deactivate",
            variant: "danger",
          })
        : (window.confirm("Deactivate this schedule block?") ? Promise.resolve() : Promise.reject());

      ask.then(function () {
        return fetch(deleteUrlTemplate.replace("/0/", "/" + encodeURIComponent(id) + "/"), {
          method: "POST",
          headers: KeyTrack.jsonHeaders(),
          credentials: "same-origin",
        }).then(function (res) {
          if (res.ok) {
            window.KeyTrackModal.close(MODAL_ID);
            KeyTrack.toast("Schedule block deactivated.", "success");
            window.location.reload();
          } else if (res.status === 403) {
            KeyTrack.toast("You do not have permission.", "error");
          } else {
            showAlert("Could not deactivate the schedule block.");
          }
        });
      }, function () { /* cancelled */ });
    }

    if (deleteBtn) deleteBtn.addEventListener("click", deactivate);

    /* No-JS POST came back invalid: reopen with the server's errors. */
    if (form.getAttribute("data-open-on-load") === "1") {
      window.KeyTrackModal.open(MODAL_ID);
    }

    KeyTrack.scheduleModal = { openCreate: openCreate, openEdit: openEdit };
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
