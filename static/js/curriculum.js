/* curriculum.js — the Curriculum page and its "Add subjects" form.
 *
 *   - The term picker submits itself on change.
 *   - The add form's subject checklist: a text filter, a live "n selected"
 *     count, and "Already added" re-checked whenever the program, year or
 *     semester changes. Each row carries data-slots="BSIT|4|1ST ...", the
 *     slots that subject is already in, so no request is needed.
 *
 * The form arrives by AJAX (modal-form.js), so everything is delegated.
 */
(function () {
  "use strict";

  function rows(form) {
    return Array.prototype.slice.call(form.querySelectorAll("[data-curriculum-row]"));
  }

  function slotKey(form) {
    function val(name) {
      var el = form.querySelector('[data-curriculum-slot="' + name + '"]');
      return el ? String(el.value || "").trim() : "";
    }
    return val("program").toUpperCase() + "|" + val("year_level") + "|" + val("semester");
  }

  function count(form) {
    var out = form.querySelector("[data-curriculum-count]");
    if (!out) return;
    var n = form.querySelectorAll('input[name="subjects"]:checked:not(:disabled)').length;
    out.textContent = n + " selected";
  }

  function markAdded(form) {
    var key = slotKey(form);
    rows(form).forEach(function (row) {
      var box = row.querySelector('input[name="subjects"]');
      var badge = row.querySelector("[data-curriculum-added]");
      var slots = (row.getAttribute("data-slots") || "").split(" ");
      var inSlot = slots.indexOf(key) !== -1;
      if (box) {
        if (inSlot) box.checked = false;
        box.disabled = inSlot;
      }
      if (badge) badge.classList.toggle("hidden", !inSlot);
    });
    count(form);
  }

  function filter(form, query) {
    var q = String(query || "").trim().toLowerCase();
    var shown = 0;
    rows(form).forEach(function (row) {
      var hit = !q || (row.getAttribute("data-text") || "").indexOf(q) !== -1;
      row.classList.toggle("hidden", !hit);
      if (hit) shown++;
    });
    var empty = form.querySelector("[data-curriculum-empty]");
    if (empty) empty.classList.toggle("hidden", shown > 0 || rows(form).length === 0);
  }

  document.addEventListener("input", function (e) {
    var form = e.target.closest && e.target.closest("[data-curriculum-form]");
    if (!form) return;
    if (e.target.hasAttribute("data-curriculum-filter")) filter(form, e.target.value);
    else if (e.target.hasAttribute("data-curriculum-slot")) markAdded(form);
  });

  document.addEventListener("change", function (e) {
    var t = e.target;
    if (t.matches && t.matches("select[data-auto-submit]") && t.form) {
      t.form.submit();
      return;
    }
    var form = t.closest && t.closest("[data-curriculum-form]");
    if (!form) return;
    if (t.hasAttribute("data-curriculum-slot")) markAdded(form);
    else if (t.name === "subjects") count(form);
  });

  /* Enter in the filter box must not submit the form. */
  document.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && e.target.hasAttribute && e.target.hasAttribute("data-curriculum-filter")) {
      e.preventDefault();
    }
  });
})();
