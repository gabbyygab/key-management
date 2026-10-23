/* search.js — shared debounced live search (F3.2, F3.4, F4.*).
 *
 * One debounce implementation for every live search in the app, so the
 * 250–300ms window in GUIDE.md §5 is set in exactly one place.
 *
 * Declarative use — no per-page JS needed:
 *   <input data-search-url="/api/borrowers/search/"
 *          data-search-target="#borrower-results"
 *          data-search-param="q">
 * The endpoint answers with an HTML fragment, or with JSON when the input
 * also carries data-search-template (see KeyTrack.search() below for the
 * programmatic form the POS uses).
 *
 * Programmatic use:
 *   var run = KeyTrack.debounce(function (q) { ... }, 275);
 *
 * Requires: csrf.js (window.KeyTrack, refreshIcons).
 */
(function () {
  "use strict";

  var KeyTrack = (window.KeyTrack = window.KeyTrack || {});

  var DEFAULT_DELAY = 275; /* inside the agreed 250–300ms band */

  function debounce(fn, delay) {
    var timer = null;
    var wait = delay === undefined ? DEFAULT_DELAY : delay;
    function debounced() {
      var args = arguments;
      var self = this;
      window.clearTimeout(timer);
      timer = window.setTimeout(function () {
        fn.apply(self, args);
      }, wait);
    }
    debounced.cancel = function () {
      window.clearTimeout(timer);
    };
    return debounced;
  }

  /* A search helper that cancels the previous in-flight request, so a slow
   * early response can never overwrite a newer one (the classic live-search
   * race). */
  function createSearch(options) {
    var opts = options || {};
    var controller = null;

    var run = debounce(function (query) {
      if (controller) controller.abort();
      controller = new AbortController();

      var url = new URL(opts.url, window.location.origin);
      url.searchParams.set(opts.param || "q", query);
      if (opts.extraParams) {
        Object.keys(opts.extraParams).forEach(function (k) {
          url.searchParams.set(k, opts.extraParams[k]);
        });
      }

      if (opts.onStart) opts.onStart(query);

      fetch(url.toString(), {
        headers: { "X-Requested-With": "XMLHttpRequest" },
        signal: controller.signal,
        credentials: "same-origin",
      })
        .then(function (res) {
          if (res.status === 403) {
            if (KeyTrack.toast) KeyTrack.toast("You do not have permission.", "error");
            throw new Error("forbidden");
          }
          if (!res.ok) throw new Error("search failed: " + res.status);
          var type = res.headers.get("Content-Type") || "";
          return type.indexOf("application/json") !== -1 ? res.json() : res.text();
        })
        .then(function (data) {
          if (opts.onResults) opts.onResults(data, query);
        })
        .catch(function (err) {
          if (err.name === "AbortError") return; /* superseded, not an error */
          if (opts.onError) opts.onError(err);
        })
        .finally(function () {
          if (opts.onDone) opts.onDone(query);
        });
    }, opts.delay);

    return { run: run, cancel: run.cancel };
  }

  /* Wire every [data-search-url] input on the page. The endpoint is expected
   * to return an HTML fragment for the target container. */
  function bindDeclarative(root) {
    var scope = root || document;
    Array.prototype.forEach.call(
      scope.querySelectorAll("[data-search-url]:not([data-search-bound])"),
      function (input) {
        input.setAttribute("data-search-bound", "1");

        var target = document.querySelector(input.getAttribute("data-search-target"));
        if (!target) return;

        var search = createSearch({
          url: input.getAttribute("data-search-url"),
          param: input.getAttribute("data-search-param") || "q",
          delay: parseInt(input.getAttribute("data-search-delay"), 10) || undefined,
          onStart: function () {
            target.setAttribute("aria-busy", "true");
          },
          onResults: function (html) {
            if (typeof html !== "string") return;
            target.innerHTML = html;
            /* Injected rows carry bare <i data-lucide> — without this the
             * chevrons and badges render as blank space. */
            KeyTrack.refreshIcons(target);
          },
          onDone: function () {
            target.removeAttribute("aria-busy");
          },
        });

        input.addEventListener("input", function () {
          search.run(input.value.trim());
        });
      }
    );
  }

  KeyTrack.debounce = debounce;
  KeyTrack.createSearch = createSearch;
  KeyTrack.bindSearch = bindDeclarative;

  document.addEventListener("DOMContentLoaded", function () {
    bindDeclarative(document);
  });
})();
