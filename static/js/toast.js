/* Toast notifications (TASK F1.4). window.KeyTrackToast.show(message, variant).
 * Variants: success | error | warning | info. Stacks top-right, auto-dismisses.
 * Django's {% messages %} still renders normally for non-AJAX page loads.
 */
(function () {
  var AUTO_DISMISS_MS = 4000;
  var VARIANTS = {
    success: { cls: "bg-green-50 border-green-200 text-green-800", icon: "check-circle" },
    error: { cls: "bg-red-50 border-red-200 text-red-800", icon: "alert-circle" },
    warning: { cls: "bg-amber-50 border-amber-200 text-amber-800", icon: "alert-triangle" },
    info: { cls: "bg-white border-stone-200 text-stone-700", icon: "info" },
  };

  function host() {
    var node = document.getElementById("toast-host");
    if (!node) {
      node = document.createElement("div");
      node.id = "toast-host";
      node.className = "fixed top-4 right-4 z-[100] w-80 max-w-[calc(100vw-2rem)] space-y-2";
      node.setAttribute("aria-live", "polite");
      node.setAttribute("aria-atomic", "true");
      document.body.appendChild(node);
    }
    return node;
  }

  function dismiss(toast) {
    toast.classList.add("opacity-0", "translate-x-2");
    setTimeout(function () { toast.remove(); }, 200);
  }

  function show(message, variant) {
    var spec = VARIANTS[variant] || VARIANTS.info;
    var toast = document.createElement("div");
    toast.className = "flex items-start gap-2 px-3.5 py-3 rounded-xl border shadow-sm text-[13px] "
      + "transition-all duration-200 opacity-0 translate-x-2 " + spec.cls;
    toast.setAttribute("role", variant === "error" ? "alert" : "status");
    toast.innerHTML =
      '<i data-lucide="' + spec.icon + '" class="w-4 h-4 mt-0.5 shrink-0"></i>'
      + '<p class="flex-1 min-w-0"></p>'
      + '<button type="button" aria-label="Dismiss" class="shrink-0 opacity-60 hover:opacity-100">'
      + '<i data-lucide="x" class="w-3.5 h-3.5"></i></button>';
    toast.querySelector("p").textContent = message;
    toast.querySelector("button").addEventListener("click", function () { dismiss(toast); });
    host().appendChild(toast);
    if (window.lucide) lucide.createIcons();
    requestAnimationFrame(function () {
      toast.classList.remove("opacity-0", "translate-x-2");
    });
    setTimeout(function () { dismiss(toast); }, AUTO_DISMISS_MS);
    return toast;
  }

  window.KeyTrackToast = {
    show: show,
    success: function (m) { return show(m, "success"); },
    error: function (m) { return show(m, "error"); },
    warning: function (m) { return show(m, "warning"); },
    info: function (m) { return show(m, "info"); },
  };
})();
