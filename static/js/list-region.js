/* list-region.js — in-place paging for paginated list tables.
 *
 * Markup contract (see guide.md §5 "List pages: pagination"):
 *   <form method="get" data-list-region-form="#user-list-region">  filters
 *   <div id="user-list-region" data-list-region>
 *     <table> <thead>…</thead> <tbody>…</tbody> </table>
 *     {% include "partials/pagination.html" %}
 *   </div>
 *
 * A pagination link or a filter submit fetches the same URL, shows skeleton
 * rows in the table body while it waits (KeyTrack.skeletons.tableRows), swaps
 * the region's markup from the response and updates the address bar. Any
 * failure falls back to a normal page load, so the links keep working
 * without JS.
 *
 * Requires: csrf.js (KeyTrack.refreshIcons), skeletons.js.
 */
(function () {
  "use strict";

  var KeyTrack = (window.KeyTrack = window.KeyTrack || {});
  var SKELETON_ROWS = 10; /* = accounts.crud.LIST_PAGE_SIZE */

  function showSkeleton(region) {
    region.setAttribute("aria-busy", "true");
    var tbody = region.querySelector("tbody");
    var headCells = region.querySelectorAll("thead th").length;
    if (tbody && KeyTrack.skeletons && KeyTrack.skeletons.tableRows) {
      tbody.innerHTML = KeyTrack.skeletons.tableRows(headCells || 5, SKELETON_ROWS);
    }
    var nav = region.querySelector("nav[aria-label=Pagination]");
    if (nav) nav.classList.add("pointer-events-none", "opacity-60");
  }

  function load(region, url, push, message) {
    var selector = "#" + region.id;
    showSkeleton(region);
    fetch(url, {
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    })
      .then(function (res) {
        if (KeyTrack.isSessionExpired && KeyTrack.isSessionExpired(res)) {
          KeyTrack.handleSessionExpired();
          throw new Error("session_expired");
        }
        if (!res.ok || res.redirected) throw new Error("load failed: " + res.status);
        return res.text();
      })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var fresh = doc.querySelector(selector);
        if (!fresh) throw new Error("region missing");
        region.innerHTML = fresh.innerHTML;
        /* The filter form lives outside the region but shows state too (the
         * Reset link appears only while a filter is on) — refresh it. */
        var formSel = 'form[data-list-region-form="' + selector + '"]';
        var oldForm = document.querySelector(formSel);
        var newForm = doc.querySelector(formSel);
        if (oldForm && newForm) {
          var oldReset = oldForm.querySelector("a[href]");
          var newReset = newForm.querySelector("a[href]");
          if (oldReset && !newReset) oldReset.remove();
          else if (!oldReset && newReset) oldForm.appendChild(newReset);
        }
        /* Page furniture that depends on the filters (stat cards, header links
         * carrying ?term=) sits outside the region: mark it
         * `id="x" data-list-sync` and it is swapped along with the rows. */
        doc.querySelectorAll("[data-list-sync][id]").forEach(function (fresh2) {
          var old = document.getElementById(fresh2.id);
          if (old) {
            old.innerHTML = fresh2.innerHTML;
            KeyTrack.refreshIcons(old);
          }
        });
        region.removeAttribute("aria-busy");
        KeyTrack.refreshIcons(region);
        if (KeyTrack.bindSearch) KeyTrack.bindSearch(region);
        if (push) {
          /* Mark the entry we leave, so Back to it is handled too. */
          if (!history.state || !history.state.listRegion) {
            history.replaceState({ listRegion: selector }, "", window.location.href);
          }
          history.pushState({ listRegion: selector }, "", url);
        }
        if (region.getBoundingClientRect().top < 0) {
          region.scrollIntoView({ block: "start" });
        }
        if (message) KeyTrack.toast(message, "success");
        /* bulk-actions.js re-syncs its toolbar against the fresh rows. */
        region.dispatchEvent(new CustomEvent("keytrack:list-region-loaded", { bubbles: true }));
      })
      .catch(function (err) {
        if (err && err.message === "session_expired") return;
        window.location.href = url;
      });
  }

  document.addEventListener("click", function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var link = e.target.closest("[data-list-region] nav[aria-label=Pagination] a[href]");
    if (!link) return;
    var region = link.closest("[data-list-region]");
    if (!region || !region.id) return;
    e.preventDefault();
    load(region, link.href, true);
  });

  /* "Rows per page" select: each option's value is the target URL. */
  document.addEventListener("change", function (e) {
    var sel = e.target;
    if (!sel.matches || !sel.matches("select[data-per-page]")) return;
    var url = new URL(sel.value, window.location.href).toString();
    var region = sel.closest("[data-list-region]");
    if (region && region.id) load(region, url, true);
    else window.location.href = url;
  });

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.matches || !form.matches("form[data-list-region-form]")) return;
    if ((form.getAttribute("method") || "get").toLowerCase() !== "get") return;
    var region = document.querySelector(form.getAttribute("data-list-region-form"));
    if (!region || !region.id) return;
    e.preventDefault();
    var url = new URL(form.getAttribute("action") || window.location.pathname, window.location.origin);
    /* Filters always restart at page 1: no page param is sent. */
    new FormData(form).forEach(function (value, key) {
      if (value !== "") url.searchParams.append(key, value);
    });
    load(region, url.toString(), true);
  });

  /* Re-fetch a region in place, keeping the current page and filters. Used
   * after a bulk action (bulk-actions.js); `message`, if given, is toasted
   * once the fresh rows are in. Falls back to a full reload when the region
   * cannot be refreshed on its own. */
  KeyTrack.reloadListRegion = function (region, message) {
    if (!region || !region.id) {
      if (message && KeyTrack.reloadWithToast) KeyTrack.reloadWithToast(message, "success");
      else window.location.reload();
      return;
    }
    load(region, window.location.href, false, message);
  };

  /* Back/forward after an in-place page change: reload that URL's page. */
  window.addEventListener("popstate", function (e) {
    if (e.state && e.state.listRegion) window.location.reload();
  });
})();
