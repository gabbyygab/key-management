/* static/js/history.js
 * Transaction history interaction controller (TASK F6.2).
 * Handles opening transaction timeline detail modal with full metadata.
 */
(function () {
  "use strict";

  var MODAL_ID = "transaction-detail-modal";

  var STATUS_PILLS = {
    ACTIVE: '<span class="inline-flex items-center gap-1 bg-blue-50 text-blue-800 border border-blue-200 px-2.5 py-0.5 rounded-full text-[11px] font-semibold"><span aria-hidden="true">●</span> ACTIVE</span>',
    RETURNED: '<span class="inline-flex items-center gap-1 bg-green-50 text-green-800 border border-green-200 px-2.5 py-0.5 rounded-full text-[11px] font-semibold"><span aria-hidden="true">●</span> RETURNED</span>',
    OVERDUE: '<span class="inline-flex items-center gap-1 bg-red-50 text-red-800 border border-red-200 px-2.5 py-0.5 rounded-full text-[11px] font-semibold"><span aria-hidden="true">●</span> OVERDUE</span>',
    LOST: '<span class="inline-flex items-center gap-1 bg-red-50 text-red-800 border border-red-200 px-2.5 py-0.5 rounded-full text-[11px] font-semibold"><span aria-hidden="true">●</span> LOST</span>',
    DAMAGED: '<span class="inline-flex items-center gap-1 bg-amber-50 text-amber-800 border border-amber-200 px-2.5 py-0.5 rounded-full text-[11px] font-semibold"><span aria-hidden="true">●</span> DAMAGED</span>',
  };

  function setElText(modal, selector, text) {
    var el = modal.querySelector(selector);
    if (el) el.textContent = text || "—";
  }

  function openDetailModal(row) {
    var modal = document.getElementById(MODAL_ID);
    if (!modal) return;

    var ds = row.dataset;

    // Header values
    setElText(modal, "[data-modal-txn-no]", ds.txnNo);

    var statusContainer = modal.querySelector("[data-modal-status-container]");
    if (statusContainer) {
      var pillHtml = STATUS_PILLS[ds.status] || ('<span class="inline-flex items-center gap-1 bg-stone-100 text-stone-700 border border-stone-200 px-2.5 py-0.5 rounded-full text-[11px] font-semibold">● ' + (ds.status || "UNKNOWN") + '</span>');
      if (ds.isOverdue === "true" && ds.status === "ACTIVE") {
        pillHtml = '<span class="inline-flex items-center gap-1 bg-red-50 text-red-800 border border-red-200 px-2.5 py-0.5 rounded-full text-[11px] font-semibold"><span aria-hidden="true">●</span> OVERDUE</span>';
      }
      statusContainer.innerHTML = pillHtml;
    }

    // Key & Room Summary
    setElText(modal, "[data-modal-key]", ds.key);
    setElText(modal, "[data-modal-category]", ds.category);
    var roomBldg = ds.room ? (ds.room + (ds.building ? " (" + ds.building + ")" : "")) : "—";
    setElText(modal, "[data-modal-room-building]", roomBldg);

    // Borrower Summary
    setElText(modal, "[data-modal-borrower]", ds.borrower);
    var borrowerMeta = [];
    if (ds.borrowerId) borrowerMeta.push(ds.borrowerId);
    if (ds.borrowerType) borrowerMeta.push(ds.borrowerType);
    if (ds.borrowerDept) borrowerMeta.push(ds.borrowerDept);
    setElText(modal, "[data-modal-borrower-details]", borrowerMeta.join(" · "));

    setElText(modal, "[data-modal-purpose]", ds.purpose || "Room use");

    // Timeline - Release Step
    setElText(modal, "[data-modal-borrowed-at]", ds.borrowedAt);
    setElText(modal, "[data-modal-released-by]", ds.releasedBy);
    setElText(modal, "[data-modal-shift]", ds.shiftPost ? ("Post: " + ds.shiftPost) : "General Shift");
    setElText(modal, "[data-modal-expected-return]", ds.expectedReturn || "Not specified");

    // Timeline - Return Step
    var returnNode = modal.querySelector("[data-modal-return-step]");
    var returnCompleted = modal.querySelector("[data-modal-return-completed]");
    var returnPending = modal.querySelector("[data-modal-return-pending]");
    var returnStatusBadge = modal.querySelector("[data-modal-return-status-badge]");

    if (ds.returnedAt && ds.returnedAt !== "—") {
      if (returnCompleted) returnCompleted.classList.remove("hidden");
      if (returnPending) returnPending.classList.add("hidden");
      setElText(modal, "[data-modal-returned-at]", ds.returnedAt);
      setElText(modal, "[data-modal-received-by]", ds.receivedBy);
      if (returnStatusBadge) {
        returnStatusBadge.textContent = ds.status === "DAMAGED" ? "Returned (Damaged)" : (ds.status === "LOST" ? "Reported Lost" : "Returned in Good Condition");
      }
    } else {
      if (returnCompleted) returnCompleted.classList.add("hidden");
      if (returnPending) returnPending.classList.remove("hidden");
      var pendingNote = modal.querySelector("[data-modal-pending-note]");
      if (pendingNote) {
        if (ds.isOverdue === "true" || ds.status === "OVERDUE") {
          pendingNote.className = "text-[12px] font-semibold text-red-700 bg-red-50 border border-red-200 rounded-lg p-2.5 mt-1 inline-flex items-center gap-1.5";
          pendingNote.innerHTML = '<i data-lucide="alert-triangle" class="w-4 h-4 shrink-0 text-red-600"></i> Overdue: Key has not been returned by expected time.';
        } else {
          pendingNote.className = "text-[12px] text-blue-700 bg-blue-50 border border-blue-200 rounded-lg p-2.5 mt-1 inline-flex items-center gap-1.5";
          pendingNote.innerHTML = '<i data-lucide="clock" class="w-4 h-4 shrink-0 text-blue-600"></i> Active: Key is currently in circulation with borrower.';
        }
      }
    }

    // Schedule Override Reason section
    var overrideSec = modal.querySelector("[data-modal-override-section]");
    if (overrideSec) {
      if (ds.overrideReason && ds.overrideReason.trim()) {
        overrideSec.classList.remove("hidden");
        setElText(modal, "[data-modal-override-reason]", ds.overrideReason);
      } else {
        overrideSec.classList.add("hidden");
      }
    }

    // Remarks section
    var remarksSec = modal.querySelector("[data-modal-remarks-section]");
    if (remarksSec) {
      if (ds.remarks && ds.remarks.trim()) {
        remarksSec.classList.remove("hidden");
        setElText(modal, "[data-modal-remarks]", ds.remarks);
      } else {
        remarksSec.classList.add("hidden");
      }
    }

    // Open through standard modal controller
    if (window.KeyTrackModal && typeof window.KeyTrackModal.open === "function") {
      window.KeyTrackModal.open(MODAL_ID);
    } else {
      modal.classList.remove("hidden");
    }

    // Refresh lucide icons inside modal
    if (window.KeyTrack && typeof window.KeyTrack.refreshIcons === "function") {
      window.KeyTrack.refreshIcons(modal);
    } else if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }
  }

  function init() {
    var table = document.getElementById("transaction-history-table");
    if (!table) return;

    table.addEventListener("click", function (e) {
      // Don't trigger if user clicked an external link or form inside row
      if (e.target.closest("a") && !e.target.closest("[data-txn-detail-btn]")) return;

      var row = e.target.closest("[data-txn-row]");
      if (row) {
        openDetailModal(row);
      }
    });

    table.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        var row = e.target.closest("[data-txn-row]");
        if (row && document.activeElement === row) {
          e.preventDefault();
          openDetailModal(row);
        }
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
