/* auto-filter.js - filter forms apply themselves; there is no Filter button.
 *
 * Mark a GET form with `data-auto-filter`. Changing any <select> in it submits
 * the form (through requestSubmit, so list-region.js still swaps just the
 * region). A text box submits on Enter; in a form that also has
 * `data-list-region-form` it submits ~350ms after typing stops, since that
 * swap does not reload the page or drop focus.
 */
(function () {
  "use strict";

  function submit(form) {
    if (form.requestSubmit) form.requestSubmit();
    else form.submit();
  }

  document.addEventListener("change", function (e) {
    var el = e.target;
    if (!el.matches || !el.matches("select")) return;
    var form = el.closest("form[data-auto-filter]");
    if (form) submit(form);
  });

  var timer;
  document.addEventListener("input", function (e) {
    var el = e.target;
    if (!el.matches || !el.matches("input[type='text'], input[type='search'], input:not([type])")) return;
    var form = el.closest("form[data-auto-filter][data-list-region-form]");
    if (!form) return;
    clearTimeout(timer);
    timer = setTimeout(function () { submit(form); }, 350);
  });
})();
