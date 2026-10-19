/* One delegated modal controller for every KEYTRACK modal (TASK F1.2).
 *
 * Markup contract — see templates/partials/logout_modal.html:
 *   <div id="my-modal" class="hidden fixed inset-0 z-50 flex items-center justify-center p-4"
 *        role="dialog" aria-modal="true" aria-labelledby="my-modal-title">
 *     <div data-modal-overlay class="absolute inset-0 bg-stone-900/60"></div>
 *     <div class="relative ...card...">...<button data-modal-close>...</button></div>
 *   </div>
 *
 * Open with [data-modal-open="my-modal"], close with [data-modal-close], the
 * overlay, or Escape. Scripts drive it through window.KeyTrackModal.
 */
(function () {
  var BASE_Z = 50;
  var stack = [];          // open modal ids, innermost last
  var triggers = {};       // modal id -> element focus returns to

  var FOCUSABLE = [
    "a[href]", "button:not([disabled])", "textarea:not([disabled])",
    "input:not([disabled]):not([type=hidden])", "select:not([disabled])",
    "[tabindex]:not([tabindex='-1'])",
  ].join(",");

  function el(id) { return document.getElementById(id); }

  function focusables(modal) {
    return Array.prototype.filter.call(
      modal.querySelectorAll(FOCUSABLE),
      function (n) { return n.offsetParent !== null; });
  }

  function open(id, opts) {
    var modal = el(id);
    if (!modal || stack.indexOf(id) !== -1) return;
    triggers[id] = (opts && opts.trigger) || document.activeElement;
    stack.push(id);
    modal.style.zIndex = String(BASE_Z + stack.length * 10);
    modal.classList.remove("hidden");
    document.body.classList.add("overflow-hidden");
    if (window.lucide) lucide.createIcons();
    var items = focusables(modal);
    var first = modal.querySelector("[data-modal-autofocus]")
      || items.find(function (n) { return n.tagName === "INPUT" || n.tagName === "TEXTAREA"; })
      || items[0];
    if (first) first.focus();
    modal.dispatchEvent(new CustomEvent("modal:open", { detail: { id: id } }));
  }

  function close(id) {
    var at = stack.indexOf(id);
    if (at === -1) return;
    var modal = el(id);
    stack.splice(at, 1);
    modal.classList.add("hidden");
    modal.style.zIndex = "";
    if (!stack.length) document.body.classList.remove("overflow-hidden");
    var back = triggers[id];
    delete triggers[id];
    if (back && back.focus && document.contains(back)) back.focus();
    modal.dispatchEvent(new CustomEvent("modal:close", { detail: { id: id } }));
  }

  function topmost() { return stack.length ? el(stack[stack.length - 1]) : null; }

  document.addEventListener("click", function (e) {
    var opener = e.target.closest("[data-modal-open]");
    if (opener) {
      e.preventDefault();
      open(opener.getAttribute("data-modal-open"), { trigger: opener });
      return;
    }
    var closer = e.target.closest("[data-modal-close], [data-modal-overlay]");
    if (closer) {
      var modal = closer.closest("[role=dialog]");
      if (modal) {
        e.preventDefault();
        close(modal.id);
      }
    }
  });

  document.addEventListener("keydown", function (e) {
    var modal = topmost();
    if (!modal) return;
    if (e.key === "Escape") {
      e.preventDefault();
      close(modal.id);
      return;
    }
    if (e.key !== "Tab") return;
    var items = focusables(modal);
    if (!items.length) return;
    var first = items[0];
    var last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    } else if (!modal.contains(document.activeElement)) {
      e.preventDefault();
      first.focus();
    }
  });

  window.KeyTrackModal = {
    open: open,
    close: close,
    isOpen: function (id) { return stack.indexOf(id) !== -1; },
  };
})();
