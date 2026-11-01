/* confirm.js — generic confirmation modal (F1.5).
 *
 * Any <form data-confirm="..."> is intercepted on submit: the shared
 * #confirm-modal opens with the message, the affected record and (optionally)
 * a required reason textarea. Accepting re-submits the form for real.
 *
 * Programmatic use:
 *   KeyTrack.confirm({title, message, subject, label, variant, reason})
 *     .then(function (result) { result.reason ... })   // rejects on cancel
 *
 * Requires: modal.js.
 */
(function () {
  "use strict";

  var KeyTrack = (window.KeyTrack = window.KeyTrack || {});
  var MODAL_ID = "confirm-modal";
  var pending = null; /* {resolve, reject, settled} */

  function el(sel) {
    var m = document.getElementById(MODAL_ID);
    return m ? m.querySelector(sel) : null;
  }

  function confirm(opts) {
    opts = opts || {};
    var modal = document.getElementById(MODAL_ID);
    if (!modal || !window.KeyTrackModal) {
      /* Degrade to the native dialog rather than skipping the confirmation. */
      return window.confirm(opts.message || "Are you sure?")
        ? Promise.resolve({ reason: "" })
        : Promise.reject(new Error("cancelled"));
    }
    if (pending && !pending.settled) pending.reject(new Error("cancelled"));

    var danger = opts.variant === "danger";
    document.getElementById("confirm-modal-title").textContent = opts.title || "Are you sure?";
    document.getElementById("confirm-modal-message").textContent = opts.message || "";

    var subject = el("[data-confirm-subject]");
    subject.textContent = opts.subject || "";
    subject.classList.toggle("hidden", !opts.subject);

    var wrap = el("[data-confirm-icon-wrap]");
    wrap.className =
      "flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border " +
      (danger ? "border-red-200 bg-red-50 text-red-700" : "border-amber-200 bg-amber-50 text-amber-700");

    var accept = el("[data-confirm-accept]");
    accept.textContent = opts.label || "Confirm";
    accept.className =
      "rounded-lg px-4 py-2 text-[13px] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50 " +
      (danger ? "bg-red-700 hover:bg-red-800" : "bg-[#14342A] hover:bg-[#0F2E1F]");

    var reasonWrap = el("[data-confirm-reason-wrap]");
    var reason = document.getElementById("confirm-modal-reason");
    reason.value = "";
    reasonWrap.classList.toggle("hidden", !opts.reason);
    reason.required = !!opts.reason;
    accept.disabled = !!opts.reason;

    return new Promise(function (resolve, reject) {
      pending = { resolve: resolve, reject: reject, settled: false, needsReason: !!opts.reason };
      window.KeyTrackModal.open(MODAL_ID);
      if (opts.reason) reason.focus();
      else accept.focus();
    });
  }

  function settle(ok) {
    if (!pending || pending.settled) return;
    pending.settled = true;
    var p = pending;
    if (ok) {
      var reason = document.getElementById("confirm-modal-reason").value.trim();
      p.resolve({ reason: reason });
    } else {
      p.reject(new Error("cancelled"));
    }
  }

  document.addEventListener("input", function (e) {
    if (e.target.id !== "confirm-modal-reason" || !pending) return;
    el("[data-confirm-accept]").disabled = e.target.value.trim().length < 3;
  });

  document.addEventListener("click", function (e) {
    if (!e.target.closest("#" + MODAL_ID + " [data-confirm-accept]")) return;
    if (pending && pending.needsReason &&
        document.getElementById("confirm-modal-reason").value.trim().length < 3) return;
    settle(true);
    window.KeyTrackModal.close(MODAL_ID);
  });

  /* Any other way the modal closes (Cancel, Escape, overlay) is a cancel.
   * modal.js hides the element, so watch for that. */
  document.addEventListener("DOMContentLoaded", function () {
    var modal = document.getElementById(MODAL_ID);
    if (!modal || !window.MutationObserver) return;
    new MutationObserver(function () {
      if (modal.classList.contains("hidden")) settle(false);
    }).observe(modal, { attributes: true, attributeFilter: ["class"] });
  });

  document.addEventListener(
    "submit",
    function (e) {
      var form = e.target;
      if (!form.matches || !form.matches("form[data-confirm]")) return;
      if (form.dataset.confirmed === "1") {
        form.dataset.confirmed = "";
        return; /* second pass: let it through */
      }
      e.preventDefault();
      e.stopImmediatePropagation();
      var submitter = e.submitter || null;
      confirm({
        title: form.dataset.confirmTitle,
        message: form.dataset.confirm,
        subject: form.dataset.confirmSubject,
        label: form.dataset.confirmLabel,
        variant: form.dataset.confirmVariant,
        reason: !!form.dataset.confirmReason,
      }).then(
        function (result) {
          var field = form.dataset.confirmReason;
          if (field) {
            var input = form.querySelector('input[name="' + field + '"]');
            if (!input) {
              input = document.createElement("input");
              input.type = "hidden";
              input.name = field;
              form.appendChild(input);
            }
            input.value = result.reason;
          }
          form.dataset.confirmed = "1";
          if (form.requestSubmit) form.requestSubmit(submitter || undefined);
          else form.submit();
        },
        function () { /* cancelled */ }
      );
    },
    true
  );

  KeyTrack.confirm = confirm;
})();
