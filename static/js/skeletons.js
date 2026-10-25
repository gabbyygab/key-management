/* skeletons.js — Loading skeletons for AJAX lists & dynamic views (F6.8).
 *
 * Provides animated pulse skeletons for:
 *   - POS borrower search results & available keys list
 *   - Room scheduling timetable grid
 *   - "Currently Out" keys list
 *   - Room finder card results
 *   - Modal forms & generic lists/tables
 *
 * Design tokens per GUIDE.md §5: stone-100, stone-200, rounded-xl/2xl, animate-pulse.
 * Always sets aria-busy="true" during loading and role="status" on skeleton containers.
 *
 * Requires: csrf.js (window.KeyTrack).
 */
(function () {
  "use strict";

  var KeyTrack = (window.KeyTrack = window.KeyTrack || {});

  function resolveElement(target) {
    if (!target) return null;
    return typeof target === "string" ? document.querySelector(target) : target;
  }

  /* POS borrower search result row skeleton */
  function posBorrower(count) {
    var n = count || 4;
    var rows = [];
    for (var i = 0; i < n; i++) {
      rows.push(
        '<div class="flex items-center justify-between py-2.5 px-2 rounded-lg border border-stone-100 bg-stone-50/50 animate-pulse">' +
          '<div class="space-y-1.5 flex-1">' +
            '<div class="h-3.5 bg-stone-200 rounded ' + (i % 2 === 0 ? "w-36" : "w-44") + '"></div>' +
            '<div class="h-2.5 bg-stone-100 rounded w-20"></div>' +
          '</div>' +
          '<div class="w-4 h-4 rounded bg-stone-200 shrink-0"></div>' +
        '</div>'
      );
    }
    return (
      '<div class="space-y-1.5 py-1" role="status" aria-label="Loading borrower results...">' +
      rows.join("") +
      '<span class="sr-only">Loading search results...</span>' +
      '</div>'
    );
  }

  /* POS available keys result card skeleton */
  function posKey(count) {
    var n = count || 3;
    var rows = [];
    for (var i = 0; i < n; i++) {
      rows.push(
        '<div class="border border-stone-200 rounded-xl p-2.5 animate-pulse space-y-2.5 bg-white">' +
          '<div class="flex items-center justify-between gap-2">' +
            '<div class="h-4 bg-stone-200 rounded ' + (i % 2 === 0 ? "w-28" : "w-32") + '"></div>' +
            '<div class="h-3 bg-stone-100 rounded w-16"></div>' +
          '</div>' +
          '<div class="h-8 bg-stone-100 rounded-lg w-full"></div>' +
          '<div class="flex justify-end">' +
            '<div class="h-8 bg-stone-200 rounded-lg w-20"></div>' +
          '</div>' +
        '</div>'
      );
    }
    return (
      '<div class="space-y-2 py-1" role="status" aria-label="Loading available keys...">' +
      rows.join("") +
      '<span class="sr-only">Loading available keys...</span>' +
      '</div>'
    );
  }

  /* Weekly room scheduling timetable grid skeleton */
  function scheduleGrid() {
    var days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    var headerCols = ['<div class="h-4 bg-stone-100 rounded w-10"></div>'];
    days.forEach(function (d) {
      headerCols.push(
        '<div class="text-center">' +
          '<div class="h-4 bg-stone-200 rounded w-16 mx-auto"></div>' +
          '<span class="sr-only">' + d + '</span>' +
        '</div>'
      );
    });

    var rows = [];
    var times = ["08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00"];
    times.forEach(function (t, idx) {
      var cells = ['<div class="text-[11px] font-mono text-stone-300 select-none">' + t + '</div>'];
      for (var col = 0; col < 6; col++) {
        // Vary filled slots to resemble actual timetabled blocks
        var isFilled = (idx + col) % 3 === 0;
        if (isFilled) {
          cells.push(
            '<div class="h-16 bg-stone-100 rounded-lg border border-stone-200 p-2 space-y-1.5">' +
              '<div class="h-3 bg-stone-200 rounded w-3/4"></div>' +
              '<div class="h-2.5 bg-stone-200 rounded w-1/2"></div>' +
            '</div>'
          );
        } else {
          cells.push(
            '<div class="h-16 rounded-lg border border-dashed border-stone-200 bg-stone-50/40"></div>'
          );
        }
      }
      rows.push(
        '<div class="grid grid-cols-7 gap-2 items-center">' + cells.join("") + '</div>'
      );
    });

    return (
      '<div class="border border-stone-200 rounded-xl bg-white p-4 shadow-sm animate-pulse space-y-3" role="status" aria-label="Loading timetable grid...">' +
        '<div class="grid grid-cols-7 gap-2 pb-3 border-b border-stone-200">' +
          headerCols.join("") +
        '</div>' +
        '<div class="space-y-3">' +
          rows.join("") +
        '</div>' +
        '<span class="sr-only">Loading timetable schedule...</span>' +
      '</div>'
    );
  }

  /* Currently-out live panel list skeleton */
  function currentlyOut(count) {
    var n = count || 3;
    var rows = [];
    for (var i = 0; i < n; i++) {
      rows.push(
        '<div class="border border-stone-200 rounded-xl p-2.5 animate-pulse bg-white space-y-2">' +
          '<div class="flex items-start justify-between gap-2">' +
            '<div class="space-y-1.5 flex-1">' +
              '<div class="flex items-center gap-2">' +
                '<div class="h-4 bg-stone-200 rounded ' + (i % 2 === 0 ? "w-24" : "w-28") + '"></div>' +
                '<div class="h-3.5 bg-stone-100 rounded w-16"></div>' +
              '</div>' +
              '<div class="h-3.5 bg-stone-200 rounded w-36"></div>' +
              '<div class="h-3 bg-stone-100 rounded w-28"></div>' +
            '</div>' +
            '<div class="h-7 w-16 bg-stone-200 rounded-lg shrink-0"></div>' +
          '</div>' +
        '</div>'
      );
    }
    return (
      '<div class="space-y-2" role="status" aria-label="Loading currently borrowed keys...">' +
      rows.join("") +
      '<span class="sr-only">Loading currently borrowed keys...</span>' +
      '</div>'
    );
  }

  /* Free rooms card grid skeleton */
  function roomCards(count) {
    var n = count || 6;
    var cards = [];
    for (var i = 0; i < n; i++) {
      cards.push(
        '<div class="border border-stone-200 rounded-xl p-3 bg-white shadow-sm animate-pulse space-y-3">' +
          '<div class="flex items-start justify-between">' +
            '<div class="space-y-1.5 flex-1">' +
              '<div class="h-4 bg-stone-200 rounded w-20"></div>' +
              '<div class="h-3 bg-stone-100 rounded w-28"></div>' +
            '</div>' +
            '<div class="w-6 h-6 rounded bg-stone-100"></div>' +
          '</div>' +
          '<div class="flex items-center justify-between pt-1">' +
            '<div class="h-3 bg-stone-100 rounded w-16"></div>' +
            '<div class="h-7 bg-stone-200 rounded-lg w-24"></div>' +
          '</div>' +
        '</div>'
      );
    }
    return (
      '<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3" role="status" aria-label="Loading free rooms...">' +
      cards.join("") +
      '<span class="sr-only">Loading free rooms...</span>' +
      '</div>'
    );
  }

  /* Form-in-modal skeleton */
  function form() {
    return (
      '<div class="p-6 space-y-4 animate-pulse" role="status" aria-label="Loading form...">' +
        '<div class="space-y-1.5">' +
          '<div class="h-3 bg-stone-200 rounded w-24"></div>' +
          '<div class="h-9 bg-stone-100 rounded-lg w-full"></div>' +
        '</div>' +
        '<div class="space-y-1.5">' +
          '<div class="h-3 bg-stone-200 rounded w-28"></div>' +
          '<div class="h-9 bg-stone-100 rounded-lg w-full"></div>' +
        '</div>' +
        '<div class="space-y-1.5">' +
          '<div class="h-3 bg-stone-200 rounded w-20"></div>' +
          '<div class="h-20 bg-stone-100 rounded-lg w-full"></div>' +
        '</div>' +
        '<div class="flex justify-end gap-2 pt-2">' +
          '<div class="h-8 bg-stone-100 rounded-lg w-16"></div>' +
          '<div class="h-8 bg-stone-200 rounded-lg w-20"></div>' +
        '</div>' +
        '<span class="sr-only">Loading form...</span>' +
      '</div>'
    );
  }

  /* Generic list skeleton */
  function list(count) {
    var n = count || 4;
    var rows = [];
    for (var i = 0; i < n; i++) {
      rows.push(
        '<div class="flex items-center justify-between py-3 px-2 border-b border-stone-100 animate-pulse">' +
          '<div class="space-y-1.5 flex-1">' +
            '<div class="h-3.5 bg-stone-200 rounded ' + (i % 2 === 0 ? "w-40" : "w-56") + '"></div>' +
            '<div class="h-2.5 bg-stone-100 rounded w-24"></div>' +
          '</div>' +
          '<div class="h-4 bg-stone-200 rounded w-16"></div>' +
        '</div>'
      );
    }
    return (
      '<div class="space-y-1" role="status" aria-label="Loading list...">' +
      rows.join("") +
      '<span class="sr-only">Loading list items...</span>' +
      '</div>'
    );
  }

  /* Render chosen skeleton into a container and mark aria-busy */
  function render(target, type, options) {
    var el = resolveElement(target);
    if (!el) return;
    var opts = options || {};
    var count = typeof opts === "number" ? opts : (opts.count || undefined);

    var html = "";
    switch (type) {
      case "pos-borrower":
      case "borrowers":
        html = posBorrower(count);
        break;
      case "pos-key":
      case "keys":
        html = posKey(count);
        break;
      case "schedule-grid":
      case "timetable":
        html = scheduleGrid();
        break;
      case "currently-out":
        html = currentlyOut(count);
        break;
      case "room-cards":
      case "cards":
        html = roomCards(count);
        break;
      case "form":
        html = form();
        break;
      default:
        html = list(count);
        break;
    }

    el.setAttribute("aria-busy", "true");
    el.innerHTML = html;
  }

  function clear(target) {
    var el = resolveElement(target);
    if (!el) return;
    el.removeAttribute("aria-busy");
  }

  var skeletons = {
    posBorrower: function (target, count) {
      if (!target) return posBorrower(count);
      render(target, "pos-borrower", count);
    },
    posKey: function (target, count) {
      if (!target) return posKey(count);
      render(target, "pos-key", count);
    },
    scheduleGrid: function (target) {
      if (!target) return scheduleGrid();
      render(target, "schedule-grid");
    },
    currentlyOut: function (target, count) {
      if (!target) return currentlyOut(count);
      render(target, "currently-out", count);
    },
    roomCards: function (target, count) {
      if (!target) return roomCards(count);
      render(target, "room-cards", count);
    },
    form: function (target) {
      if (!target) return form();
      render(target, "form");
    },
    list: function (target, count) {
      if (!target) return list(count);
      render(target, "list", count);
    },
    render: render,
    clear: clear,
  };

  KeyTrack.skeletons = skeletons;
})();
