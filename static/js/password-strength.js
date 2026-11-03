/* password-strength.js — live strength meter for every new-password field.
 *
 * Any input marked `data-password-strength` (set on the widget in
 * accounts/forms.py, so the full-page password change and the admin reset
 * modal both get it) grows a meter underneath: a four-segment bar, a
 * Weak/Fair/Good/Strong label and a checklist of the four rules
 *   - at least 8 characters
 *   - a letter
 *   - a number
 *   - a special character
 *
 * Fully delegated and lazy: the meter is built the first time the field is
 * focused or typed into, so a field injected into a modal long after load is
 * covered without any hook from modal-form.js.
 *
 * The checklist uses text marks, not Lucide icons, on purpose: it rerenders on
 * every keystroke and a refreshIcons() per keystroke would be wasteful.
 *
 * This is guidance, not enforcement. The server is still the only thing that
 * decides whether a password is acceptable (accounts/forms.py ->
 * django.contrib.auth.password_validation).
 *
 * Requires: csrf.js (window.KeyTrack).
 */
(function () {
  "use strict";

  var KeyTrack = (window.KeyTrack = window.KeyTrack || {});

  var RULES = [
    { key: "length", text: "8+ characters", test: function (v) { return v.length >= 8; } },
    { key: "letter", text: "A letter", test: function (v) { return /[A-Za-z]/.test(v); } },
    { key: "number", text: "A number", test: function (v) { return /[0-9]/.test(v); } },
    { key: "special", text: "A special character", test: function (v) { return /[^A-Za-z0-9]/.test(v); } }
  ];

  /* 4 bar segments; index 0 is the empty-field resting state. */
  var LEVELS = [
    { label: "Too short", bar: "bg-stone-300", text: "text-stone-500" },
    { label: "Weak", bar: "bg-red-500", text: "text-red-600" },
    { label: "Fair", bar: "bg-amber-500", text: "text-amber-600" },
    { label: "Good", bar: "bg-[#1E6B4A]", text: "text-[#1E6B4A]" },
    { label: "Strong", bar: "bg-[#14342A]", text: "text-[#14342A]" }
  ];

  /* Rules met -> level. Missing the special character caps a password at
   * Fair, and Strong needs all four rules *and* 12 characters, so the meter
   * never calls a password that only just clears the rules "Strong". */
  function score(value) {
    var met = 0;
    for (var i = 0; i < RULES.length; i++) if (RULES[i].test(value)) met++;
    if (met === 4) return value.length >= 12 ? 4 : 3;
    return met >= 3 ? 2 : 1;
  }

  function build(input) {
    var wrap = document.createElement("div");
    wrap.className = "mt-2";
    wrap.setAttribute("data-password-meter", "");

    var row = document.createElement("div");
    row.className = "flex items-center gap-2";
    var bars = document.createElement("div");
    bars.className = "flex flex-1 gap-1";
    bars.setAttribute("aria-hidden", "true");
    bars.setAttribute("data-strength-bars", "");
    for (var i = 0; i < 4; i++) {
      var seg = document.createElement("span");
      seg.className = "h-1.5 flex-1 rounded-full bg-stone-200";
      bars.appendChild(seg);
    }
    var label = document.createElement("span");
    label.className = "text-[12px] font-semibold text-stone-400";
    label.setAttribute("data-strength-label", "");
    label.textContent = LEVELS[0].label;
    row.appendChild(bars);
    row.appendChild(label);

    var list = document.createElement("ul");
    list.className = "mt-1.5 grid grid-cols-2 gap-x-3 gap-y-0.5 text-[12px]";
    for (var r = 0; r < RULES.length; r++) {
      var li = document.createElement("li");
      li.className = "flex items-center gap-1 text-stone-500";
      li.setAttribute("data-rule", RULES[r].key);
      var mark = document.createElement("span");
      mark.setAttribute("data-rule-mark", "");
      mark.setAttribute("aria-hidden", "true");
      mark.textContent = "○";
      var text = document.createElement("span");
      text.textContent = RULES[r].text;
      li.appendChild(mark);
      li.appendChild(text);
      list.appendChild(li);
    }

    /* One polite announcement instead of four: a screen reader hears the
     * strength, not every tick as it changes. */
    var live = document.createElement("p");
    live.className = "sr-only";
    live.setAttribute("aria-live", "polite");
    live.setAttribute("data-strength-live", "");

    wrap.appendChild(row);
    wrap.appendChild(list);
    wrap.appendChild(live);

    /* Under the input but above the server-rendered error list, which keeps
     * the error closest to the submit button where it is looked for. */
    var err = input.parentNode.querySelector("[data-form-field-error]");
    if (err) input.parentNode.insertBefore(wrap, err);
    else input.parentNode.appendChild(wrap);
    return wrap;
  }

  function render(input) {
    var meter = input.ktMeter;
    if (!meter) return;
    var value = input.value || "";
    var filled = score(value);
    var level = LEVELS[value ? filled : 0];

    var segs = meter.querySelectorAll("[data-strength-bars] > span");
    for (var i = 0; i < segs.length; i++) {
      segs[i].className = "h-1.5 flex-1 rounded-full " +
        (value && i < filled ? level.bar : "bg-stone-200");
    }

    var label = meter.querySelector("[data-strength-label]");
    label.className = "text-[12px] font-semibold " + (value ? level.text : "text-stone-400");
    label.textContent = level.label;

    for (var r = 0; r < RULES.length; r++) {
      var li = meter.querySelector('[data-rule="' + RULES[r].key + '"]');
      var ok = RULES[r].test(value);
      li.className = "flex items-center gap-1 " + (ok ? "text-[#1E6B4A]" : "text-stone-500");
      li.querySelector("[data-rule-mark]").textContent = ok ? "✓" : "○";
    }

    /* Only announce when the word itself changes, not on every keystroke. */
    var live = meter.querySelector("[data-strength-live]");
    var msg = "Password strength: " + level.label + ".";
    if (live.textContent !== msg) live.textContent = msg;
  }

  function attach(input) {
    if (!input || input.ktMeter) return;
    input.ktMeter = build(input);
    render(input);
  }

  function handle(e) {
    var el = e.target;
    if (!el || !el.matches || !el.matches("input[data-password-strength]")) return;
    attach(el);
    render(el);
  }

  document.addEventListener("focusin", handle);
  document.addEventListener("input", handle);

  /* Full-page forms: show the rules before anything is typed. Modal fields are
   * picked up by the delegated listeners above when they are focused. */
  function scan(root) {
    var fields = (root || document).querySelectorAll("input[data-password-strength]");
    for (var i = 0; i < fields.length; i++) attach(fields[i]);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { scan(document); });
  } else {
    scan(document);
  }

  KeyTrack.passwordStrength = { attach: attach, scan: scan };
})();
