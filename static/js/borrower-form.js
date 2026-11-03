/* Borrower form: the Department field only applies to Faculty and Staff.
   Works for the full page and for the modal partial (injected later), so it
   listens on the document and re-syncs when new markup appears. */
(function () {
  "use strict";
  var DEPT_TYPES = ["FACULTY", "STAFF"];

  function sync(select) {
    var form = select.form;
    if (!form) return;
    var wrap = form.querySelector('[data-field-name="department"]');
    if (!wrap) return;
    var show = DEPT_TYPES.indexOf(select.value) !== -1;
    wrap.classList.toggle("hidden", !show);
    var field = wrap.querySelector("select");
    if (field) {
      field.disabled = !show;
      if (!show) field.value = "";
    }
  }

  function syncAll() {
    document.querySelectorAll('select[name="borrower_type"]').forEach(sync);
  }

  document.addEventListener("change", function (e) {
    if (e.target && e.target.name === "borrower_type") sync(e.target);
  });
  new MutationObserver(syncAll).observe(document.body, { childList: true, subtree: true });
  syncAll();
})();
