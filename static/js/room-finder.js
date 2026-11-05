// Room finder, subject tab: live search against GET /api/scheduling/room-finder/.
// The server renders the results region (scheduling/_room_finder_results.html),
// so this script only gathers the form, swaps the region and hands a picked
// window to the add-class dialog (class-modal.js, "keytrack:schedule-prefill").
(function () {
  "use strict";

  var form = document.getElementById("room-finder-form");
  if (!form) return;

  var apiUrl = form.dataset.apiUrl;
  var region = document.getElementById("room-finder-results");
  var offering = form.elements.offering;
  var termSelect = form.querySelector("[data-term-select]");
  var DEBOUNCE_MS = 250;
  var debounceTimer = null;
  var inFlight = null;

  // --- errors -------------------------------------------------------------
  function clearErrors() {
    form.querySelectorAll("[data-error-for]").forEach(function (el) {
      el.textContent = "";
      el.classList.add("hidden");
    });
    form.querySelectorAll("[aria-invalid]").forEach(function (el) { el.removeAttribute("aria-invalid"); });
  }

  function showErrors(errors) {
    Object.keys(errors).forEach(function (field) {
      var el = form.querySelector('[data-error-for="' + field + '"]')
        || form.querySelector('[data-error-for="__all__"]');
      var messages = Array.isArray(errors[field]) ? errors[field] : [errors[field]];
      if (el) {
        el.textContent = messages.join(" ");
        el.classList.remove("hidden");
      }
      var input = form.elements[field];
      if (input && input.setAttribute) input.setAttribute("aria-invalid", "true");
    });
  }

  // --- query --------------------------------------------------------------
  // FormData(form) also picks up the meeting toggle, which lives in the
  // results region and joins the form through its form="room-finder-form".
  function currentParams() {
    var params = new URLSearchParams();
    new FormData(form).forEach(function (value, key) {
      if (String(value).trim() !== "") params.append(key, value);
    });
    return params;
  }

  // opts.params: a ready query (a pager link, Back/Forward) instead of the
  // form, which always starts over on page 1. opts.history: "push" for a page
  // change (so Back works), "none" when the URL is already right, else replace.
  function search(opts) {
    opts = opts || {};
    clearTimeout(debounceTimer);
    clearErrors();
    var params = opts.params || currentParams();
    if (!params.get("offering")) return;   // the start state is already showing

    if (inFlight) inFlight.abort();
    var controller = new AbortController();
    inFlight = controller;
    form.setAttribute("aria-busy", "true");
    if (window.KeyTrack && KeyTrack.skeletons && KeyTrack.skeletons.roomFinder) {
      KeyTrack.skeletons.roomFinder(region, 4);
    }

    fetch(apiUrl + "?" + params.toString(), {
      headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
      signal: controller.signal
    })
      .then(function (res) {
        if (window.KeyTrack && KeyTrack.isSessionExpired && KeyTrack.isSessionExpired(res)) {
          KeyTrack.handleSessionExpired();
          throw new Error("session_expired");
        }
        return res.json().catch(function () { return {}; }).then(function (body) {
          if (res.status === 200) {
            region.innerHTML = body.html;
            var url = form.action + "?" + params.toString();
            if (opts.history === "push") history.pushState({ roomFinder: true }, "", url);
            else if (opts.history !== "none") history.replaceState({ roomFinder: true }, "", url);
            if (opts.scroll) {
              var top = document.getElementById("room-finder-cards");
              if (top) top.scrollIntoView({ block: "start" });
            }
          } else if (res.status === 422) {
            region.innerHTML = lastHtml;
            showErrors(body);
          } else if (res.status === 403) {
            region.innerHTML = lastHtml;
            showErrors({ __all__: ["You do not have permission to search rooms."] });
          } else {
            region.innerHTML = lastHtml;
            showErrors({ __all__: ["Something went wrong (" + res.status + "). Try again."] });
          }
        });
      })
      .catch(function (err) {
        if (err.name === "AbortError" || err.message === "session_expired") return;
        region.innerHTML = lastHtml;
        showErrors({ __all__: ["Could not reach the server. Check your connection and try again."] });
        if (window.KeyTrack && KeyTrack.networkErrorToast) {
          KeyTrack.networkErrorToast(function () { search(opts); },
                                     "Room search failed due to a network error.");
        }
      })
      .finally(function () {
        if (inFlight !== controller) return;
        inFlight = null;
        form.removeAttribute("aria-busy");
        region.removeAttribute("aria-busy");
        lastHtml = region.innerHTML;
        if (window.KeyTrack && KeyTrack.refreshIcons) KeyTrack.refreshIcons(region);
      });
  }

  // What to put back when a search fails: the last good results, not a skeleton.
  var lastHtml = region.innerHTML;

  function scheduleSearch() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(search, DEBOUNCE_MS);
  }

  // --- hand-off to the add-class dialog -----------------------------------
  function openScheduler(btn) {
    var opt = offering.options[offering.selectedIndex];
    if (!opt || !opt.value) return;
    document.dispatchEvent(new CustomEvent("keytrack:schedule-prefill", {
      detail: {
        room_id: parseInt(btn.dataset.roomId, 10),
        room_label: btn.dataset.roomLabel,
        day_of_week: parseInt(btn.dataset.day, 10),
        start_time: btn.dataset.start,
        end_time: btn.dataset.end,
        section_id: opt.dataset.section,
        subject_id: opt.dataset.subject,
        term_id: termSelect ? termSelect.value : null,
        trigger: btn
      }
    }));
  }

  // --- wiring -------------------------------------------------------------
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    search();
  });

  // Pager links (partials/pagination.html) carry the full query plus ?page=;
  // swap only the results, like list-region.js does for the other lists.
  // A modified click (new tab) keeps the browser's own behaviour.
  region.addEventListener("click", function (e) {
    var link = e.target.closest("[data-finder-pagination] a[href]");
    if (!link || e.defaultPrevented || e.button !== 0 ||
        e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    var url = new URL(link.getAttribute("href"), window.location.href);
    search({ params: url.searchParams, history: "push", scroll: true });
  });

  // Back / Forward between pages: the URL already holds the query.
  window.addEventListener("popstate", function () {
    var params = new URLSearchParams(window.location.search);
    if (params.get("offering")) search({ params: params, history: "none" });
  });

  // A new term means a different list of offerings: reload the page for it.
  if (termSelect) {
    termSelect.addEventListener("change", function (e) {
      e.stopPropagation();
      window.location.href = form.action + "?term=" + encodeURIComponent(termSelect.value);
    });
  }

  // A different subject starts on its first meeting, not the old one's toggle.
  offering.addEventListener("change", function () {
    document.querySelectorAll('input[name="kind"][form="room-finder-form"]').forEach(function (r) {
      r.checked = false;
      r.disabled = true;
    });
  });

  form.addEventListener("change", function (e) {
    if (e.target === termSelect) return;
    scheduleSearch();
  });

  // The meeting toggle sits in the results region; its change bubbles there.
  region.addEventListener("change", function (e) {
    if (e.target.name === "kind") search();
  });

  region.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-finder-slot]");
    if (btn) openScheduler(btn);
  });
})();
