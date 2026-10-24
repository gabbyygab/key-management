/* key-modal.js — Room selector live search & key modal interactions (TASK F4.3)
 *
 * Provides:
 * - Real-time filtering of room options and optgroups (grouped by building)
 * - Clear button for search input
 * - Dynamic "Original (Copy 1)" badge display on copy_number change
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

  // Dynamic preview badge for copy_number == 1 (Original)
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
  });
})();
