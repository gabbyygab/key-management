/* Users & Guards page: live countdown on every "Locked" badge.
 *
 * The server sends the seconds left (data-lock-seconds). The end time is
 * fixed against this browser's clock the first time a badge is seen, so a
 * wrong clock on the PC cannot skew it, and badges that arrive later through
 * list-region.js or a modal refresh are picked up by the same timer.
 * When the time runs out the badge turns into "Unlocked".
 */
(function () {
  "use strict";

  function fmt(total) {
    var m = Math.floor(total / 60), s = total % 60;
    return m + ":" + (s < 10 ? "0" : "") + s;
  }

  function tick() {
    var badges = document.querySelectorAll("[data-lock-seconds]");
    var now = Date.now();
    for (var i = 0; i < badges.length; i++) {
      var el = badges[i];
      if (!el.dataset.lockEnd) {
        el.dataset.lockEnd = String(now + parseInt(el.dataset.lockSeconds, 10) * 1000);
      }
      var left = Math.max(0, Math.ceil((parseInt(el.dataset.lockEnd, 10) - now) / 1000));
      var timer = el.querySelector("[data-lock-timer]");
      if (left > 0) {
        if (timer) timer.textContent = fmt(left);
        continue;
      }
      el.removeAttribute("data-lock-seconds");
      el.className = "inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 " +
                     "text-[11px] font-semibold text-emerald-700 ring-1 ring-emerald-200";
      el.textContent = "Unlocked";
      el.removeAttribute("title");
    }
  }

  tick();
  setInterval(tick, 1000);
})();
