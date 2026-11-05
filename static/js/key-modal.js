/* key-modal.js — Room selector live search & key modal interactions (TASK F4.3)
 *
 * Provides:
 * - Real-time filtering of room options and optgroups (grouped by building)
 * - Clear button for search input
 * - Dynamic "Original (Copy 1)" badge display on copy_number change
 * - Automatic key code generation and category suggestion when a room is picked
 *
 * Uses delegated listeners on document so it works across AJAX modal reloads.
 */
(function () {
  "use strict";

  // Filter room options and optgroups
  document.addEventListener("input", function (e) {
    var searchInput = e.target.closest("[data-room-search]");
    if (!searchInput) return;

    var container = searchInput.closest("[data-form-field]") || searchInput.closest("form");
    if (!container) return;

    var select = container.querySelector("select[name=room]");
    if (!select) return;

    var query = searchInput.value.trim().toLowerCase();
    var clearBtn = searchInput.parentNode.querySelector("[data-room-search-clear]");
    if (clearBtn) {
      clearBtn.classList.toggle("hidden", !query);
    }

    var optgroups = select.querySelectorAll("optgroup");
    var matchCount = 0;
    var totalCount = 0;

    if (optgroups.length > 0) {
      optgroups.forEach(function (group) {
        var groupLabelMatches = group.label.toLowerCase().indexOf(query) !== -1;
        var visibleInGroup = 0;
        var options = group.querySelectorAll("option");
        options.forEach(function (opt) {
          totalCount++;
          var optMatches = opt.text.toLowerCase().indexOf(query) !== -1;
          var matches = !query || groupLabelMatches || optMatches;
          opt.hidden = !matches;
          opt.style.display = matches ? "" : "none";
          if (matches) {
            visibleInGroup++;
            matchCount++;
          }
        });
        group.hidden = visibleInGroup === 0;
        group.style.display = visibleInGroup === 0 ? "none" : "";
      });
    } else {
      var options = select.querySelectorAll("option");
      options.forEach(function (opt) {
        if (!opt.value) return; // Skip placeholder
        totalCount++;
        var matches = !query || opt.text.toLowerCase().indexOf(query) !== -1;
        opt.hidden = !matches;
        opt.style.display = matches ? "" : "none";
        if (matches) matchCount++;
      });
    }

    var feedback = container.querySelector("[data-room-search-feedback]");
    if (feedback) {
      if (query) {
        feedback.textContent = "Showing " + matchCount + " of " + totalCount + " rooms";
        feedback.classList.remove("hidden");
      } else {
        feedback.classList.add("hidden");
        feedback.textContent = "";
      }
    }
  });

  // ---- Room combobox: the search input opens a dropdown of rooms (default picker) ----
  function enhanceRoomSearch(input) {
    if (input.dataset.combo === "1") return;
    var container = input.closest("[data-form-field]") || input.closest("form");
    var select = container && container.querySelector("select[name=room]");
    if (!select) return;
    input.dataset.combo = "1";
    select.classList.add("sr-only");
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");

    var wrap = input.closest(".relative");
    var panel = document.createElement("div");
    panel.className = "hidden absolute left-0 right-0 top-full z-20 mt-1 max-h-60 overflow-y-auto rounded-lg border border-stone-200 bg-white py-1 shadow-lg";
    panel.setAttribute("role", "listbox");
    wrap.appendChild(panel);
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-autocomplete", "list");

    var active = -1;

    function selectedLabel() {
      var o = select.options[select.selectedIndex];
      return o && o.value ? o.text : "";
    }
    function items() { return panel.querySelectorAll("[data-room-opt]"); }
    function setActive(i) {
      var list = items();
      if (!list.length) { active = -1; return; }
      active = (i + list.length) % list.length;
      list.forEach(function (el, n) {
        el.classList.toggle("bg-stone-100", n === active);
      });
      list[active].scrollIntoView({ block: "nearest" });
    }
    function render(query) {
      query = (query || "").trim().toLowerCase();
      panel.innerHTML = "";
      var count = 0;
      function addOpt(opt, groupMatch) {
        if (!opt.value) return;
        if (query && !groupMatch && opt.text.toLowerCase().indexOf(query) === -1) return;
        var b = document.createElement("button");
        b.type = "button";
        b.setAttribute("data-room-opt", opt.value);
        b.setAttribute("role", "option");
        b.className = "block w-full px-3 py-1.5 text-left text-[12px] text-stone-800 hover:bg-stone-100" +
          (opt.value === select.value ? " font-semibold text-[#1E6B4A]" : "");
        b.textContent = opt.text;
        panel.appendChild(b);
        count++;
      }
      Array.prototype.forEach.call(select.children, function (child) {
        if (child.tagName === "OPTGROUP") {
          var gm = !!query && child.label.toLowerCase().indexOf(query) !== -1;
          var before = panel.children.length;
          var head = document.createElement("div");
          head.className = "px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-stone-400";
          head.textContent = child.label;
          panel.appendChild(head);
          Array.prototype.forEach.call(child.children, function (o) { addOpt(o, gm); });
          if (panel.children.length === before + 1) panel.removeChild(head);
        } else if (child.tagName === "OPTION") {
          addOpt(child, false);
        }
      });
      if (!count) {
        var empty = document.createElement("p");
        empty.className = "px-3 py-2 text-[12px] text-stone-500";
        empty.textContent = "No rooms match";
        panel.appendChild(empty);
      }
      active = -1;
    }
    function open(filter) {
      render(filter);
      panel.classList.remove("hidden");
      input.setAttribute("aria-expanded", "true");
    }
    function close() {
      panel.classList.add("hidden");
      input.setAttribute("aria-expanded", "false");
      input.value = selectedLabel();
    }
    function choose(value) {
      select.value = value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      close();
    }

    input.value = selectedLabel();
    input.addEventListener("focus", function () { input.select(); open(""); });
    input.addEventListener("click", function () { if (panel.classList.contains("hidden")) open(""); });
    input.addEventListener("input", function () { open(input.value); });
    input.addEventListener("keydown", function (e) {
      var isOpen = !panel.classList.contains("hidden");
      if (e.key === "ArrowDown") { e.preventDefault(); if (!isOpen) open(""); setActive(active + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); if (isOpen) setActive(active - 1); }
      else if (e.key === "Enter" && isOpen) {
        e.preventDefault();
        var list = items();
        var pick = list[active >= 0 ? active : (list.length === 1 ? 0 : -1)];
        if (pick) choose(pick.getAttribute("data-room-opt"));
      } else if (e.key === "Escape" && isOpen) {
        e.preventDefault(); e.stopPropagation(); close();
      }
    });
    panel.addEventListener("mousedown", function (e) {
      var b = e.target.closest("[data-room-opt]");
      if (!b) return;
      e.preventDefault();
      choose(b.getAttribute("data-room-opt"));
    });
    document.addEventListener("mousedown", function (e) {
      if (!document.body.contains(input)) return;
      if (!wrap.contains(e.target) && !panel.classList.contains("hidden")) close();
    });
    select.addEventListener("change", function () {
      if (panel.classList.contains("hidden")) input.value = selectedLabel();
    });
  }

  function enhanceAll(root) {
    (root || document).querySelectorAll("[data-room-search]").forEach(enhanceRoomSearch);
  }
  enhanceAll();
  new MutationObserver(function () { enhanceAll(); }).observe(document.documentElement, { childList: true, subtree: true });

  // Clear room search input
  document.addEventListener("click", function (e) {
    var clearBtn = e.target.closest("[data-room-search-clear]");
    if (!clearBtn) return;
    var input = clearBtn.parentNode.querySelector("[data-room-search]");
    if (input) {
      input.value = "";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
    }
  });

  // Dynamic preview badge for copy_number == 1 (Original) and auto-updating key_code
  document.addEventListener("input", function (e) {
    var copyInput = e.target.closest("input[name=copy_number]");
    if (!copyInput) return;
    var form = copyInput.closest("form");
    if (!form) return;
    var badge = form.querySelector("#copy-badge-preview");
    if (badge) {
      var val = copyInput.value.trim();
      badge.classList.toggle("hidden", val !== "1");
    }

    var keyCodeInput = form.querySelector("input[name=key_code]");
    if (keyCodeInput && (keyCodeInput.dataset.autoGenerated === "true" || !keyCodeInput.value)) {
      var roomSelect = form.querySelector("select[name=room]");
      if (roomSelect && roomSelect.selectedIndex >= 0) {
        var opt = roomSelect.options[roomSelect.selectedIndex];
        if (opt && opt.dataset && opt.dataset.prefix) {
          var copyVal = copyInput.value.trim() || "1";
          keyCodeInput.value = opt.dataset.prefix + "-" + copyVal;
          keyCodeInput.dataset.autoGenerated = "true";
        }
      }
    }
  });

  // Track if user manually edits key_code
  document.addEventListener("input", function (e) {
    var keyCodeInput = e.target.closest("input[name=key_code]");
    if (!keyCodeInput) return;
    if (keyCodeInput.value.trim() === "") {
      keyCodeInput.dataset.autoGenerated = "true";
    } else {
      keyCodeInput.dataset.autoGenerated = "false";
    }
  });

  // Auto-generate key code and auto-select default category when room is picked
  document.addEventListener("change", function (e) {
    var roomSelect = e.target.closest("select[name=room]");
    if (!roomSelect) return;
    var form = roomSelect.closest("form");
    if (!form) return;

    if (roomSelect.selectedIndex < 0) return;
    var opt = roomSelect.options[roomSelect.selectedIndex];
    if (!opt || !opt.dataset || !opt.dataset.prefix) return;

    var copyInput = form.querySelector("input[name=copy_number]");
    if (copyInput) {
      if (!copyInput.dataset.userEdited && opt.dataset.nextCopy) {
        copyInput.value = opt.dataset.nextCopy;
        var badge = form.querySelector("#copy-badge-preview");
        if (badge) {
          badge.classList.toggle("hidden", opt.dataset.nextCopy !== "1");
        }
      }
    }

    var keyCodeInput = form.querySelector("input[name=key_code]");
    if (keyCodeInput && (keyCodeInput.dataset.autoGenerated !== "false" || !keyCodeInput.value)) {
      var copyNum = copyInput ? (copyInput.value.trim() || "1") : "1";
      keyCodeInput.value = opt.dataset.prefix + "-" + copyNum;
      keyCodeInput.dataset.autoGenerated = "true";
    }

    var catSelect = form.querySelector("select[name=category]");
    if (catSelect && (!catSelect.value || catSelect.dataset.autoSelected === "true") && opt.dataset.category) {
      catSelect.value = opt.dataset.category;
      catSelect.dataset.autoSelected = "true";
    }
  });

  // If user manually changes category or copy number, record it
  document.addEventListener("change", function (e) {
    var catSelect = e.target.closest("select[name=category]");
    if (catSelect) {
      catSelect.dataset.autoSelected = "false";
    }
    var copyInput = e.target.closest("input[name=copy_number]");
    if (copyInput) {
      copyInput.dataset.userEdited = "true";
    }
  });
})();
