/* static/js/history.js
 * Transaction history rows (F6.2).
 *
 * The record itself is server-rendered: each row's Details link points at
 * transactions:detail and carries data-modal-form="txn-detail-modal", so
 * modal-form.js loads and shows it. This file only makes the *rest* of the
 * row a click target for that same link, without turning a <tr> into a second
 * interactive control (the link itself stays the keyboard path).
 */
(function () {
  "use strict";

  function detailLink(row) {
    return row.querySelector("[data-txn-detail-btn]");
  }

  function openFor(row) {
    var link = detailLink(row);
    if (!link) return;
    if (window.KeyTrack && window.KeyTrack.loadModalForm) {
      window.KeyTrack.loadModalForm(link);
    } else {
      window.location.href = link.getAttribute("href");
    }
  }

  document.addEventListener("click", function (e) {
    var row = e.target.closest("[data-txn-row]");
    if (!row) return;
    /* Clicks on a real control (Details, Correct) are that control's own
     * business — modal-form.js already handles them. */
    if (e.target.closest("a, button, input, select, label")) return;
    /* Let a text selection inside the row stay a text selection. */
    var selection = window.getSelection && window.getSelection();
    if (selection && String(selection).length > 0) return;
    e.preventDefault();
    openFor(row);
  });
})();
