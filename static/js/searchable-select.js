/* searchable-select.js — turns <select data-searchable> into a search dropdown.
 *
 * The native <select> stays in the DOM (visually hidden) and remains the
 * source of truth, so form posts, validation errors and `.value =` writes from
 * other scripts keep working. Forms arrive by AJAX, so a MutationObserver
 * enhances selects as they appear.
 *
 * Panel is position:fixed so a scrolling modal body cannot clip it.
 * Keys: ArrowUp/ArrowDown open; type to filter; Up/Down move; Enter picks;
 * Escape closes the dropdown only (it does not reach the modal).
 */
(function () {
  "use strict";

  var uid = 0;

  function el(tag, cls) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    return n;
  }

  function enhance(select) {
    if (select.dataset.ssReady) return;
    select.dataset.ssReady = "1";
    var id = "ss-" + (++uid);
    var searchPlaceholder = select.getAttribute("data-search-placeholder") || "Search…";

    var wrap = el("div", "relative");
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);
    select.classList.add("sr-only");
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");

    var trigger = el("button",
      "flex w-full items-center justify-between gap-2 rounded-lg border border-stone-300 bg-white px-3 py-2 text-left text-[14px] focus:border-[#1E6B4A] focus:outline-none focus:ring-1 focus:ring-[#1E6B4A]");
    trigger.type = "button";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-controls", id + "-list");
    if (select.id) {
      var lab = document.querySelector('label[for="' + select.id + '"]');
      if (lab) {
        if (!lab.id) lab.id = id + "-label";
        trigger.setAttribute("aria-labelledby", lab.id);
        lab.addEventListener("click", function (e) { e.preventDefault(); trigger.focus(); });
      }
    }
    var text = el("span", "min-w-0 flex-1 truncate");
    var chev = el("span", "shrink-0 text-stone-400");
    chev.innerHTML = '<i data-lucide="chevron-down" aria-hidden="true" class="h-4 w-4"></i>';
    trigger.appendChild(text);
    trigger.appendChild(chev);
    wrap.appendChild(trigger);

    var panel = el("div",
      "hidden fixed z-[70] overflow-hidden rounded-xl border border-stone-200 bg-white shadow-xl");
    var searchWrap = el("div", "border-b border-stone-200 p-2");
    var search = el("input",
      "w-full rounded-md border border-stone-300 bg-white px-2.5 py-1.5 text-[13px] focus:border-[#1E6B4A] focus:outline-none focus:ring-1 focus:ring-[#1E6B4A]");
    search.type = "text";
    search.placeholder = searchPlaceholder;
    search.setAttribute("autocomplete", "off");
    search.setAttribute("role", "combobox");
    search.setAttribute("aria-controls", id + "-list");
    search.setAttribute("aria-label", "Search options");
    searchWrap.appendChild(search);
    var list = el("ul", "max-h-60 overflow-y-auto py-1");
    list.id = id + "-list";
    list.setAttribute("role", "listbox");
    var empty = el("p", "hidden px-3 py-2 text-[13px] text-stone-500");
    empty.textContent = "No matches";
    panel.appendChild(searchWrap);
    panel.appendChild(list);
    panel.appendChild(empty);
    wrap.appendChild(panel);

    var items = [];   /* {li, opt} */
    var active = -1;

    function label(opt) { return opt.textContent.trim(); }

    function syncTrigger() {
      var opt = select.options[select.selectedIndex];
      var none = !opt || opt.value === "";
      text.textContent = opt ? label(opt) : "";
      text.classList.toggle("text-stone-500", none);
      trigger.disabled = select.disabled;
      trigger.classList.toggle("opacity-60", select.disabled);
      if (select.hasAttribute("aria-invalid")) {
        trigger.classList.add("border-red-500");
        trigger.setAttribute("aria-invalid", "true");
      }
    }

    function build() {
      list.textContent = "";
      items = [];
      Array.prototype.forEach.call(select.options, function (opt, i) {
        var li = el("li", "cursor-pointer px-3 py-1.5 text-[13px] text-stone-800");
        var item = { li: li, opt: opt };
        li.setAttribute("role", "option");
        li.id = id + "-o" + i;
        li.textContent = label(opt);
        li.dataset.text = label(opt).toLowerCase();
        if (opt.value === "") li.classList.add("text-stone-500");
        /* data-complete: shown but de-prioritised (e.g. a section with nothing left). */
        if (opt.dataset.complete === "1") {
          li.classList.remove("text-stone-800");
          li.classList.add("text-stone-400", "italic");
        }
        li.addEventListener("mousedown", function (e) { e.preventDefault(); });
        li.addEventListener("click", function () { choose(opt); });
        li.addEventListener("mousemove", function () { setActive(items.indexOf(item)); });
        items.push(item);
        list.appendChild(li);
      });
    }

    function visible() { return items.filter(function (x) { return !x.li.hidden; }); }

    function paint() {
      items.forEach(function (x, i) {
        var on = i === active;
        x.li.classList.toggle("bg-[#1E6B4A]/10", on);
        x.li.setAttribute("aria-selected", x.opt.selected ? "true" : "false");
        x.li.classList.toggle("font-semibold", x.opt.selected);
      });
      var cur = items[active];
      if (cur) {
        search.setAttribute("aria-activedescendant", cur.li.id);
        cur.li.scrollIntoView({ block: "nearest" });
      } else {
        search.removeAttribute("aria-activedescendant");
      }
    }

    function setActive(i) { active = i; paint(); }

    function filter() {
      var q = search.value.trim().toLowerCase();
      items.forEach(function (x) { x.li.hidden = !!q && x.li.dataset.text.indexOf(q) === -1; });
      var vis = visible();
      empty.classList.toggle("hidden", vis.length > 0);
      active = vis.length ? items.indexOf(vis[0]) : -1;
      paint();
    }

    function place() {
      var r = trigger.getBoundingClientRect();
      var below = window.innerHeight - r.bottom;
      var h = Math.min(panel.offsetHeight || 300, 320);
      panel.style.left = r.left + "px";
      panel.style.width = r.width + "px";
      if (below < h + 8 && r.top > below) {
        panel.style.top = "auto";
        panel.style.bottom = (window.innerHeight - r.top + 4) + "px";
      } else {
        panel.style.bottom = "auto";
        panel.style.top = (r.bottom + 4) + "px";
      }
    }

    function isOpen() { return !panel.classList.contains("hidden"); }

    function onScroll(e) {
      /* Only a scroll of something holding the trigger moves it. */
      if (e.target === document || (e.target.contains && e.target.contains(trigger))) close();
    }

    function open() {
      if (select.disabled || isOpen()) return;
      build();
      search.value = "";
      /* Portal to <body>: a modal body scrolls and animates with a transform,
         which would clip or mis-anchor a position:fixed child. */
      document.body.appendChild(panel);
      panel.classList.remove("hidden");
      trigger.setAttribute("aria-expanded", "true");
      place();
      filter();
      active = Math.max(0, select.selectedIndex);
      paint();
      search.focus({ preventScroll: true });
      window.addEventListener("resize", close, true);
      window.addEventListener("scroll", onScroll, true);
    }

    function close(refocus) {
      if (!isOpen()) return;
      panel.classList.add("hidden");
      wrap.appendChild(panel);
      trigger.setAttribute("aria-expanded", "false");
      window.removeEventListener("resize", close, true);
      window.removeEventListener("scroll", onScroll, true);
      if (refocus === true) trigger.focus({ preventScroll: true });
    }

    function choose(opt) {
      select.value = opt.value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      syncTrigger();
      close(true);
    }

    trigger.addEventListener("click", function () { if (isOpen()) close(true); else open(); });
    trigger.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); open(); }
    });

    search.addEventListener("input", filter);
    search.addEventListener("keydown", function (e) {
      var vis = visible();
      var pos = vis.indexOf(items[active]);
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (vis.length) setActive(items.indexOf(vis[Math.min(pos + 1, vis.length - 1)]));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        if (vis.length) setActive(items.indexOf(vis[Math.max(pos - 1, 0)]));
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (items[active]) choose(items[active].opt);
      } else if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();           /* close the dropdown, not the modal */
        close(true);
      } else if (e.key === "Tab") {
        close(true);   /* focus back on the trigger; Tab then moves on from there */
      }
    });

    document.addEventListener("mousedown", function (e) {
      if (isOpen() && !wrap.contains(e.target) && !panel.contains(e.target)) close();
    });

    /* Other scripts (and a reset) change select.value directly. */
    select.addEventListener("change", syncTrigger);
    select.addEventListener("ss-sync", syncTrigger);
    if (select.form) select.form.addEventListener("reset", function () { setTimeout(syncTrigger, 0); });

    syncTrigger();
    if (window.KeyTrack && window.KeyTrack.refreshIcons) window.KeyTrack.refreshIcons(wrap);
  }

  function scan(root) {
    if (!root || root.nodeType !== 1) return;
    if (root.matches && root.matches("select[data-searchable]")) enhance(root);
    if (root.querySelectorAll) root.querySelectorAll("select[data-searchable]").forEach(enhance);
  }

  new MutationObserver(function (muts) {
    muts.forEach(function (m) { m.addedNodes.forEach(scan); });
  }).observe(document.body, { childList: true, subtree: true });
  scan(document.body);

  window.KeyTrack = window.KeyTrack || {};
  window.KeyTrack.searchableSelect = scan;
})();
