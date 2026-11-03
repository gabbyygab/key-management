/* static/js/date-range-picker.js
 * Unified, aligned date range picker with modal dialog for KEYTRACK.
 *
 * Replaces disconnected native "Date From" and "Date To" inputs with a single,
 * beautifully aligned trigger and interactive modal with presets (Today, Last 7 days,
 * Last 30 days, This month, etc.) and calendar date selection.
 *
 * Contract:
 *   Trigger: <div data-date-range-picker data-auto-submit="true">
 *              <input type="hidden" name="start_date" value="YYYY-MM-DD" data-date-range-start>
 *              <input type="hidden" name="end_date" value="YYYY-MM-DD" data-date-range-end>
 *              <button type="button" data-date-range-trigger> ... </button>
 *              <button type="button" data-date-range-clear> ... </button>
 *            </div>
 *   Modal:   <div id="kt-date-range-modal" ...> in base.html
 *
 * Submits identical query parameters (start_date, end_date) in ISO format (YYYY-MM-DD).
 * Fully compatible with Django views, list-region.js, reports, and audit logs.
 */
(function () {
  "use strict";

  var KeyTrack = (window.KeyTrack = window.KeyTrack || {});

  var MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
  ];
  var MONTH_SHORT = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
  ];

  function pad(n) {
    return n < 10 ? "0" + n : String(n);
  }

  function formatISO(d) {
    if (!d || isNaN(d.getTime())) return "";
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  }

  function parseISO(str) {
    if (!str || typeof str !== "string") return null;
    var parts = str.trim().split("-");
    if (parts.length !== 3) return null;
    var y = parseInt(parts[0], 10);
    var m = parseInt(parts[1], 10) - 1;
    var d = parseInt(parts[2], 10);
    if (isNaN(y) || isNaN(m) || isNaN(d)) return null;
    var dt = new Date(y, m, d, 0, 0, 0, 0);
    return isNaN(dt.getTime()) ? null : dt;
  }

  function formatDisplay(d) {
    if (!d || isNaN(d.getTime())) return "";
    return MONTH_SHORT[d.getMonth()] + " " + d.getDate() + ", " + d.getFullYear();
  }

  function formatRange(startStr, endStr, placeholder) {
    placeholder = placeholder || "Select date range";
    var start = parseISO(startStr);
    var end = parseISO(endStr);

    if (start && end) {
      if (startStr === endStr) {
        return formatDisplay(start);
      }
      if (start.getFullYear() === end.getFullYear()) {
        if (start.getMonth() === end.getMonth()) {
          return MONTH_SHORT[start.getMonth()] + " " + start.getDate() + " – " + end.getDate() + ", " + start.getFullYear();
        }
        return MONTH_SHORT[start.getMonth()] + " " + start.getDate() + " – " + MONTH_SHORT[end.getMonth()] + " " + end.getDate() + ", " + start.getFullYear();
      }
      return formatDisplay(start) + " – " + formatDisplay(end);
    }
    if (start) return "From " + formatDisplay(start);
    if (end) return "Until " + formatDisplay(end);
    return placeholder;
  }

  function diffDays(start, end) {
    if (!start || !end) return 0;
    var t1 = Date.UTC(start.getFullYear(), start.getMonth(), start.getDate());
    var t2 = Date.UTC(end.getFullYear(), end.getMonth(), end.getDate());
    return Math.round(Math.abs((t2 - t1) / (24 * 60 * 60 * 1000))) + 1;
  }

  /* ---- Modal State ---- */
  var activePicker = null;
  var currentStart = null; // YYYY-MM-DD
  var currentEnd = null;   // YYYY-MM-DD
  var hoverDate = null;    // YYYY-MM-DD
  var viewYear = new Date().getFullYear();
  var viewMonth = new Date().getMonth();

  function getModal() {
    return document.getElementById("kt-date-range-modal");
  }

  function getDirectStartInput() {
    return document.getElementById("kt-date-modal-start");
  }

  function getDirectEndInput() {
    return document.getElementById("kt-date-modal-end");
  }

  function getSummaryEl() {
    return document.getElementById("kt-range-summary");
  }

  function getRangeDot() {
    return document.getElementById("kt-range-dot");
  }

  function updateDirectInputs() {
    var sInput = getDirectStartInput();
    var eInput = getDirectEndInput();
    if (sInput) sInput.value = currentStart || "";
    if (eInput) eInput.value = currentEnd || "";
  }

  function updateSummary() {
    var summaryEl = getSummaryEl();
    var dot = getRangeDot();
    if (!summaryEl) return;

    var s = parseISO(currentStart);
    var e = parseISO(currentEnd);

    if (s && e) {
      var days = diffDays(s, e);
      var text = (s <= e ? formatDisplay(s) + " to " + formatDisplay(e) : formatDisplay(e) + " to " + formatDisplay(s));
      summaryEl.textContent = text + " (" + days + " day" + (days > 1 ? "s" : "") + ")";
      if (dot) dot.className = "w-2 h-2 rounded-full bg-[#1E6B4A]";
    } else if (s) {
      summaryEl.textContent = "From " + formatDisplay(s) + " (select end date)";
      if (dot) dot.className = "w-2 h-2 rounded-full bg-amber-500";
    } else if (e) {
      summaryEl.textContent = "Until " + formatDisplay(e);
      if (dot) dot.className = "w-2 h-2 rounded-full bg-amber-500";
    } else {
      summaryEl.textContent = "No date filter active (showing all records)";
      if (dot) dot.className = "w-2 h-2 rounded-full bg-stone-300";
    }

    updatePresetButtons();
  }

  function computePreset(presetName) {
    var now = new Date();
    var today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    var s, e;

    switch (presetName) {
      case "today":
        s = new Date(today);
        e = new Date(today);
        break;
      case "yesterday":
        s = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
        e = new Date(s);
        break;
      case "last7":
        s = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 6);
        e = new Date(today);
        break;
      case "last30":
        s = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 29);
        e = new Date(today);
        break;
      case "thisMonth":
        s = new Date(today.getFullYear(), today.getMonth(), 1);
        e = new Date(today.getFullYear(), today.getMonth() + 1, 0);
        break;
      case "lastMonth":
        s = new Date(today.getFullYear(), today.getMonth() - 1, 1);
        e = new Date(today.getFullYear(), today.getMonth(), 0);
        break;
      case "yearToDate":
        s = new Date(today.getFullYear(), 0, 1);
        e = new Date(today);
        break;
      case "allTime":
        return { start: "", end: "" };
      default:
        return null;
    }
    return { start: formatISO(s), end: formatISO(e) };
  }

  function updatePresetButtons() {
    var modal = getModal();
    if (!modal) return;
    var btns = modal.querySelectorAll("[data-preset]");
    var matchedPreset = null;

    btns.forEach(function (btn) {
      var name = btn.getAttribute("data-preset");
      var range = computePreset(name);
      if (range && range.start === (currentStart || "") && range.end === (currentEnd || "")) {
        matchedPreset = name;
        btn.classList.add("bg-emerald-50", "text-[#1E6B4A]", "font-semibold", "border", "border-emerald-200");
        btn.classList.remove("text-stone-600");
      } else {
        btn.classList.remove("bg-emerald-50", "text-[#1E6B4A]", "font-semibold", "border", "border-emerald-200");
        btn.classList.add("text-stone-600");
      }
    });
  }

  function renderCalendar() {
    var modal = getModal();
    if (!modal) return;

    var titleEl = modal.querySelector("#kt-cal-title");
    var daysEl = modal.querySelector("#kt-cal-days");
    if (!titleEl || !daysEl) return;

    titleEl.textContent = MONTH_NAMES[viewMonth] + " " + viewYear;

    var firstDayIndex = new Date(viewYear, viewMonth, 1).getDay(); // 0 = Sun
    var daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
    var daysInPrevMonth = new Date(viewYear, viewMonth, 0).getDate();

    var todayISO = formatISO(new Date());

    var html = "";

    // Prev month padding
    for (var p = firstDayIndex - 1; p >= 0; p--) {
      var prevDayNum = daysInPrevMonth - p;
      html += '<div class="h-8 flex items-center justify-center text-[12px] text-stone-300 select-none pointer-events-none">' +
        prevDayNum +
        "</div>";
    }

    // Days in current month
    for (var d = 1; d <= daysInMonth; d++) {
      var dateStr = viewYear + "-" + pad(viewMonth + 1) + "-" + pad(d);

      var isStart = currentStart === dateStr;
      var isEnd = currentEnd === dateStr;
      var isSingleSelected = isStart && (!currentEnd || currentStart === currentEnd);
      var isBothSame = isStart && isEnd;

      var effectiveStart = currentStart;
      var effectiveEnd = currentEnd;

      // When hovering before selecting second date
      if (currentStart && !currentEnd && hoverDate) {
        if (hoverDate >= currentStart) {
          effectiveStart = currentStart;
          effectiveEnd = hoverDate;
        } else {
          effectiveStart = hoverDate;
          effectiveEnd = currentStart;
        }
      }

      var inRange = false;
      if (effectiveStart && effectiveEnd) {
        inRange = dateStr > effectiveStart && dateStr < effectiveEnd;
      }

      var isToday = dateStr === todayISO;

      var cellClasses = "h-8 flex items-center justify-center text-[12px] font-medium transition-colors relative cursor-pointer ";
      var innerClasses = "w-7 h-7 flex items-center justify-center rounded-lg text-center ";

      if (isSingleSelected || isBothSame) {
        cellClasses += "bg-transparent ";
        innerClasses += "bg-[#1E6B4A] text-white font-bold shadow-xs ";
      } else if (isStart) {
        cellClasses += "bg-emerald-50 rounded-l-lg ";
        innerClasses += "bg-[#1E6B4A] text-white font-bold shadow-xs ";
      } else if (isEnd) {
        cellClasses += "bg-emerald-50 rounded-r-lg ";
        innerClasses += "bg-[#1E6B4A] text-white font-bold shadow-xs ";
      } else if (inRange) {
        cellClasses += "bg-emerald-50 text-emerald-950 font-medium ";
        innerClasses += "text-emerald-950 font-semibold ";
      } else {
        cellClasses += "hover:bg-stone-100 text-stone-700 rounded-lg ";
        if (isToday) {
          innerClasses += "font-bold text-[#1E6B4A] ring-1 ring-[#1E6B4A]/40 ";
        }
      }

      html += '<button type="button" data-cal-date="' + dateStr + '" class="' + cellClasses + '" aria-label="' + dateStr + '">';
      html += '<span class="' + innerClasses + '">' + d + "</span>";
      html += "</button>";
    }

    // Next month padding to fill row
    var totalCells = firstDayIndex + daysInMonth;
    var nextPadding = (7 - (totalCells % 7)) % 7;
    for (var n = 1; n <= nextPadding; n++) {
      html += '<div class="h-8 flex items-center justify-center text-[12px] text-stone-300 select-none pointer-events-none">' +
        n +
        "</div>";
    }

    daysEl.innerHTML = html;
  }

  function handleDateClick(dateStr) {
    if (!currentStart || (currentStart && currentEnd)) {
      currentStart = dateStr;
      currentEnd = null;
      hoverDate = null;
    } else if (currentStart && !currentEnd) {
      if (dateStr < currentStart) {
        // Inverted: make the earlier date start and previous start end
        currentEnd = currentStart;
        currentStart = dateStr;
      } else {
        currentEnd = dateStr;
      }
      hoverDate = null;
    }

    updateDirectInputs();
    updateSummary();
    renderCalendar();
  }

  function openPicker(pickerEl) {
    activePicker = pickerEl;
    var startInput = pickerEl.querySelector("[data-date-range-start]");
    var endInput = pickerEl.querySelector("[data-date-range-end]");

    currentStart = startInput && startInput.value ? startInput.value.trim() : null;
    currentEnd = endInput && endInput.value ? endInput.value.trim() : null;
    hoverDate = null;

    // View the start date's month if provided, else today
    var initialDate = parseISO(currentStart) || parseISO(currentEnd) || new Date();
    viewYear = initialDate.getFullYear();
    viewMonth = initialDate.getMonth();

    updateDirectInputs();
    updateSummary();
    renderCalendar();

    if (window.KeyTrackModal && window.KeyTrackModal.open) {
      window.KeyTrackModal.open("kt-date-range-modal", { trigger: pickerEl.querySelector("[data-date-range-trigger]") });
    } else {
      var m = getModal();
      if (m) m.classList.remove("hidden");
    }
  }

  function applyRange() {
    if (!activePicker) return;

    var startInput = activePicker.querySelector("[data-date-range-start]");
    var endInput = activePicker.querySelector("[data-date-range-end]");
    var labelEl = activePicker.querySelector("[data-date-range-label]");
    var clearBtn = activePicker.querySelector("[data-date-range-clear]");

    var sVal = currentStart || "";
    var eVal = currentEnd || "";

    // Normalize: if start is after end, swap
    if (sVal && eVal && sVal > eVal) {
      var tmp = sVal;
      sVal = eVal;
      eVal = tmp;
    }

    if (startInput) startInput.value = sVal;
    if (endInput) endInput.value = eVal;

    var placeholder = activePicker.getAttribute("data-placeholder") || "Select date range";
    if (labelEl) {
      labelEl.textContent = formatRange(sVal, eVal, placeholder);
    }

    if (clearBtn) {
      if (sVal || eVal) {
        clearBtn.classList.remove("hidden");
      } else {
        clearBtn.classList.add("hidden");
      }
    }

    if (window.KeyTrackModal && window.KeyTrackModal.close) {
      window.KeyTrackModal.close("kt-date-range-modal");
    } else {
      var m = getModal();
      if (m) m.classList.add("hidden");
    }

    // Fire change event
    activePicker.dispatchEvent(new CustomEvent("date-range:change", {
      bubbles: true,
      detail: { start: sVal, end: eVal }
    }));

    // Auto submit if configured
    var autoSubmit = activePicker.getAttribute("data-auto-submit") === "true";
    if (autoSubmit) {
      var form = activePicker.closest("form");
      if (form) {
        if (typeof form.requestSubmit === "function") {
          form.requestSubmit();
        } else {
          form.submit();
        }
      }
    }
  }

  function clearPicker(pickerEl) {
    var startInput = pickerEl.querySelector("[data-date-range-start]");
    var endInput = pickerEl.querySelector("[data-date-range-end]");
    var labelEl = pickerEl.querySelector("[data-date-range-label]");
    var clearBtn = pickerEl.querySelector("[data-date-range-clear]");

    if (startInput) startInput.value = "";
    if (endInput) endInput.value = "";

    var placeholder = pickerEl.getAttribute("data-placeholder") || "Select date range";
    if (labelEl) labelEl.textContent = placeholder;
    if (clearBtn) clearBtn.classList.add("hidden");

    pickerEl.dispatchEvent(new CustomEvent("date-range:change", {
      bubbles: true,
      detail: { start: "", end: "" }
    }));

    var autoSubmit = pickerEl.getAttribute("data-auto-submit") === "true";
    if (autoSubmit) {
      var form = pickerEl.closest("form");
      if (form) {
        if (typeof form.requestSubmit === "function") {
          form.requestSubmit();
        } else {
          form.submit();
        }
      }
    }
  }

  /* ---- Initialize Pickers on Page ---- */
  function initPickers(root) {
    root = root || document;
    var pickers = root.querySelectorAll("[data-date-range-picker]");
    pickers.forEach(function (picker) {
      var startInput = picker.querySelector("[data-date-range-start]");
      var endInput = picker.querySelector("[data-date-range-end]");
      var labelEl = picker.querySelector("[data-date-range-label]");
      var clearBtn = picker.querySelector("[data-date-range-clear]");

      var sVal = startInput ? startInput.value.trim() : "";
      var eVal = endInput ? endInput.value.trim() : "";
      var placeholder = picker.getAttribute("data-placeholder") || "Select date range";

      if (labelEl) {
        labelEl.textContent = formatRange(sVal, eVal, placeholder);
      }
      if (clearBtn) {
        if (sVal || eVal) {
          clearBtn.classList.remove("hidden");
        } else {
          clearBtn.classList.add("hidden");
        }
      }
    });
    KeyTrack.refreshIcons(root);
  }

  /* ---- Delegated Global Events ---- */
  document.addEventListener("keydown", function (e) {
    if (e.key === "Enter" || e.key === " ") {
      var clearBtn = e.target.closest("[data-date-range-clear]");
      if (clearBtn) {
        e.preventDefault();
        e.stopPropagation();
        var picker = clearBtn.closest("[data-date-range-picker]");
        if (picker) clearPicker(picker);
      }
    }
  });

  document.addEventListener("click", function (e) {
    // 1. Clear button on trigger
    var clearBtn = e.target.closest("[data-date-range-clear]");
    if (clearBtn) {
      e.preventDefault();
      e.stopPropagation();
      var picker = clearBtn.closest("[data-date-range-picker]");
      if (picker) clearPicker(picker);
      return;
    }

    // 2. Trigger button click -> open modal
    var trigger = e.target.closest("[data-date-range-trigger]");
    if (trigger) {
      e.preventDefault();
      var p = trigger.closest("[data-date-range-picker]");
      if (p) openPicker(p);
      return;
    }

    // 3. Calendar day click
    var calBtn = e.target.closest("[data-cal-date]");
    if (calBtn) {
      e.preventDefault();
      var dt = calBtn.getAttribute("data-cal-date");
      if (dt) handleDateClick(dt);
      return;
    }

    // 4. Calendar Prev month
    var prevBtn = e.target.closest("#kt-cal-prev");
    if (prevBtn) {
      e.preventDefault();
      viewMonth--;
      if (viewMonth < 0) {
        viewMonth = 11;
        viewYear--;
      }
      renderCalendar();
      return;
    }

    // 5. Calendar Next month
    var nextBtn = e.target.closest("#kt-cal-next");
    if (nextBtn) {
      e.preventDefault();
      viewMonth++;
      if (viewMonth > 11) {
        viewMonth = 0;
        viewYear++;
      }
      renderCalendar();
      return;
    }

    // 6. Preset button click
    var presetBtn = e.target.closest("[data-preset]");
    if (presetBtn && presetBtn.closest("#kt-date-range-modal")) {
      e.preventDefault();
      var presetName = presetBtn.getAttribute("data-preset");
      var range = computePreset(presetName);
      if (range) {
        currentStart = range.start || null;
        currentEnd = range.end || null;
        hoverDate = null;
        if (currentStart) {
          var pDate = parseISO(currentEnd || currentStart);
          if (pDate) {
            viewYear = pDate.getFullYear();
            viewMonth = pDate.getMonth();
          }
        }
        updateDirectInputs();
        updateSummary();
        renderCalendar();
      }
      return;
    }

    // 7. Modal Reset / Clear button
    var resetBtn = e.target.closest("#kt-range-reset-btn");
    if (resetBtn) {
      e.preventDefault();
      currentStart = null;
      currentEnd = null;
      hoverDate = null;
      updateDirectInputs();
      updateSummary();
      renderCalendar();
      return;
    }

    // 8. Modal Apply button
    var applyBtn = e.target.closest("#kt-range-apply-btn");
    if (applyBtn) {
      e.preventDefault();
      applyRange();
      return;
    }
  });

  // Hover preview over calendar days
  document.addEventListener("mouseover", function (e) {
    if (!currentStart || currentEnd) return;
    var dayBtn = e.target.closest("[data-cal-date]");
    if (dayBtn) {
      var d = dayBtn.getAttribute("data-cal-date");
      if (d !== hoverDate) {
        hoverDate = d;
        renderCalendar();
      }
    }
  });

  // Direct date input changes
  document.addEventListener("change", function (e) {
    if (e.target && (e.target.id === "kt-date-modal-start" || e.target.id === "kt-date-modal-end")) {
      var sInput = getDirectStartInput();
      var eInput = getDirectEndInput();
      currentStart = sInput && sInput.value ? sInput.value.trim() : null;
      currentEnd = eInput && eInput.value ? eInput.value.trim() : null;

      if (currentStart) {
        var dt = parseISO(currentStart);
        if (dt) {
          viewYear = dt.getFullYear();
          viewMonth = dt.getMonth();
        }
      }
      updateSummary();
      renderCalendar();
    }
  });

  // Initialize on DOM ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      initPickers(document);
    });
  } else {
    initPickers(document);
  }

  // Public API
  KeyTrack.DateRangePicker = {
    init: initPickers,
    open: openPicker,
    clear: clearPicker,
    formatRange: formatRange,
    parseISO: parseISO,
    formatISO: formatISO,
  };
})();
