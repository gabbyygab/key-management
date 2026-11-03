/* bulk-actions.js — multi-select Archive / Restore / Delete forever.
 *
 * The everyday verb is Archive (Building, Room, KeyCategory, Key and Borrower
 * are all held by PROTECTed foreign keys), undone by Restore. On an Archived
 * page the toolbar also offers Delete forever, the one hard delete in the UI:
 * it cascades past PROTECT and takes the row's keys, their borrow history and
 * its schedule blocks with it, so it is offered only when every selected row
 * is already archived. See accounts/crud.py and accounts/retention.py for the
 * server side.
 *
 * Markup contract — one delegated controller, no per-page JS:
 *
 *   <div id="building-list-region" data-list-region
 *        data-bulk-url="/facilities/buildings/bulk/"
 *        data-bulk-noun="building" data-bulk-noun-plural="buildings">
 *     {% include "partials/bulk_bar.html" %}
 *     <table>
 *       <thead>… <th><input type="checkbox" data-bulk-all></th> …</thead>
 *       <tbody>
 *         <tr><td><input type="checkbox" data-bulk-item value="7"
 *                        data-bulk-state="active"   {# or "archived" #}
 *                        data-bulk-label="SCI · Science Hall"></td> … </tr>
 *
 * Behaviour:
 *   - header checkbox selects/clears the page, and shows the mixed state;
 *   - Shift-click extends the selection from the last clicked row;
 *   - the toolbar appears only with a selection and offers Archive, Restore
 *     and/or Delete forever depending on what is selected;
 *   - the action is confirmed through KeyTrack.confirm (never window.confirm),
 *     the pressed button goes busy, and the result is toasted;
 *   - the list region is then refreshed in place (list-region.js), so the
 *     current page, filters and scroll position survive.
 *
 * No-JS: nothing here is required. The checkboxes sit in a real <form> that
 * posts to the same endpoint, and every row keeps its own Archive button.
 *
 * Requires: csrf.js, toast.js, confirm.js, button-loading.js, list-region.js.
 */
(function () {
  "use strict";

  var KeyTrack = (window.KeyTrack = window.KeyTrack || {});

  function regionOf(el) {
    return el.closest("[data-bulk-url]");
  }

  function items(region) {
    return Array.prototype.slice.call(region.querySelectorAll("[data-bulk-item]"));
  }

  function selected(region) {
    return items(region).filter(function (c) { return c.checked; });
  }

  /* Tailwind's `.hidden` does not reliably outrank `.inline-flex` in the
   * generated sheet (the two land in the same layer and the cascade settles
   * on source order), so showing and hiding is done with an inline display
   * instead of by class — an inline style always wins. */
  function setShown(el, on) {
    if (el) el.style.display = on ? "" : "none";
  }

  function nouns(region, n) {
    var one = region.getAttribute("data-bulk-noun") || "record";
    var many = region.getAttribute("data-bulk-noun-plural") || one + "s";
    return n === 1 ? one : many;
  }

  /* ---- toolbar ------------------------------------------------------- */

  function sync(region) {
    var bar = region.querySelector("[data-bulk-bar]");
    var all = region.querySelector("[data-bulk-all]");
    var rows = items(region);
    var chosen = rows.filter(function (c) { return c.checked; });

    if (all) {
      all.checked = rows.length > 0 && chosen.length === rows.length;
      all.indeterminate = chosen.length > 0 && chosen.length < rows.length;
    }

    /* Highlight the chosen rows so the selection is readable at a glance. */
    rows.forEach(function (c) {
      var tr = c.closest("tr");
      if (tr) tr.classList.toggle("kt-row-selected", c.checked);
    });

    if (!bar) return;
    bar.classList.toggle("hidden", chosen.length === 0);
    setShown(bar, chosen.length > 0);
    var count = bar.querySelector("[data-bulk-count]");
    if (count) {
      count.textContent = chosen.length + " " + nouns(region, chosen.length) + " selected";
    }

    /* Offer only the action that can do something with this selection. */
    var live = chosen.some(function (c) { return c.getAttribute("data-bulk-state") !== "archived"; });
    var archived = chosen.some(function (c) { return c.getAttribute("data-bulk-state") === "archived"; });
    var archiveBtn = bar.querySelector("[data-bulk-action='archive']");
    var restoreBtn = bar.querySelector("[data-bulk-action='restore']");
    var deleteBtn = bar.querySelector("[data-bulk-action='delete']");
    setShown(archiveBtn, live);
    setShown(restoreBtn, archived);
    /* Delete forever only when the whole selection is archived: mixing a live
     * row into a hard delete is never what was meant. */
    setShown(deleteBtn, archived && !live);
  }

  /* ---- selection ----------------------------------------------------- */

  var lastClicked = null;

  document.addEventListener("click", function (e) {
    var box = e.target.closest("[data-bulk-item]");
    if (!box) return;
    var region = regionOf(box);
    if (!region) return;

    /* Shift-click: fill in everything between the two rows. */
    if (e.shiftKey && lastClicked && regionOf(lastClicked) === region) {
      var rows = items(region);
      var from = rows.indexOf(lastClicked);
      var to = rows.indexOf(box);
      if (from !== -1 && to !== -1) {
        var lo = Math.min(from, to), hi = Math.max(from, to);
        for (var i = lo; i <= hi; i++) rows[i].checked = box.checked;
      }
    }
    lastClicked = box;
    sync(region);
  });

  document.addEventListener("change", function (e) {
    var all = e.target.closest("[data-bulk-all]");
    if (all) {
      var region = regionOf(all);
      if (!region) return;
      items(region).forEach(function (c) { c.checked = all.checked; });
      lastClicked = null;
      sync(region);
      return;
    }
    var box = e.target.closest("[data-bulk-item]");
    if (box && regionOf(box)) sync(regionOf(box));
  });

  document.addEventListener("click", function (e) {
    var clear = e.target.closest("[data-bulk-clear]");
    if (!clear) return;
    var region = regionOf(clear);
    if (!region) return;
    e.preventDefault();
    items(region).forEach(function (c) { c.checked = false; });
    var all = region.querySelector("[data-bulk-all]");
    if (all) { all.checked = false; all.indeterminate = false; }
    lastClicked = null;
    sync(region);
  });

  /* ---- run the action ------------------------------------------------ */

  function describe(chosen, limit) {
    return chosen.slice(0, limit)
      .map(function (c) { return c.getAttribute("data-bulk-label") || c.value; })
      .join(", ") + (chosen.length > limit ? ", and " + (chosen.length - limit) + " more" : "");
  }

  /* The confirm copy per action. "delete" says what goes with the row,
   * because that is the one action with nothing behind it. */
  function prompt(action, chosen, noun) {
    if (action === "delete") {
      return {
        title: "Delete " + chosen.length + " " + noun + " forever",
        message:
          "This cannot be undone. The selected " + noun + " are removed from the " +
          "database along with everything that hangs off them — their keys, the " +
          "borrow history of those keys, and their schedule blocks. Anything still " +
          "holding a key out, or with an open transaction, is skipped. " +
          "Restore instead if you only want them back in the lists.",
        label: "Delete forever",
        variant: "danger",
      };
    }
    if (action === "archive") {
      return {
        title: "Archive " + chosen.length + " " + noun,
        message:
          "Archived " + noun + " stop being offered anywhere new — the borrow screen, " +
          "the room finder and the pickers. Nothing is deleted yet: every past " +
          "transaction stays readable, and you can restore them at any time. " +
          "Anything still holding a key out is skipped.",
        label: "Archive",
        variant: "danger",
      };
    }
    return {
      title: "Restore " + chosen.length + " " + noun,
      message: "These " + noun + " become selectable again everywhere.",
      label: "Restore",
      variant: "",
    };
  }

  var BUSY_LABEL = { archive: "Archiving…", restore: "Restoring…", delete: "Deleting…" };

  function run(btn, region, action) {
    var chosen = selected(region).filter(function (c) {
      var archived = c.getAttribute("data-bulk-state") === "archived";
      return action === "archive" ? !archived : archived;
    });
    if (!chosen.length) return;

    var noun = nouns(region, chosen.length);
    var copy = prompt(action, chosen, noun);

    KeyTrack.confirm({
      title: copy.title,
      message: copy.message,
      subject: describe(chosen, 5),
      label: copy.label,
      variant: copy.variant,
    }).then(function () {
      var restore = KeyTrack.buttonLoading
        ? KeyTrack.buttonLoading(btn, BUSY_LABEL[action] || "Working…")
        : function () {};

      var body = new FormData();
      body.append("action", action);
      chosen.forEach(function (c) { body.append("ids", c.value); });

      fetch(region.getAttribute("data-bulk-url"), {
        method: "POST",
        headers: KeyTrack.formHeaders(),
        body: body,
        credentials: "same-origin",
      })
        .then(function (res) {
          if (KeyTrack.isSessionExpired && KeyTrack.isSessionExpired(res)) {
            KeyTrack.handleSessionExpired();
            throw new Error("session_expired");
          }
          if (res.status === 403) {
            KeyTrack.toast("You do not have permission.", "error");
            throw new Error("forbidden");
          }
          return res.json().then(function (data) {
            return { ok: res.ok, data: data };
          });
        })
        .then(function (r) {
          var msg = (r.data && r.data.message) || "Nothing to update.";
          /* 422 = the rule said no (keys still out). Not a crash: say why. */
          KeyTrack.toast(msg, r.ok ? (r.data.blocked && r.data.blocked.length ? "warning" : "success") : "error");
          restore();
          if (KeyTrack.reloadListRegion) KeyTrack.reloadListRegion(region);
          else window.location.reload();
        })
        .catch(function (err) {
          restore();
          if (err && (err.message === "session_expired" || err.message === "forbidden")) return;
          if (KeyTrack.networkErrorToast) {
            KeyTrack.networkErrorToast(function () { run(btn, region, action); },
                                       "Could not apply the bulk action.");
          }
        });
    }, function () { /* cancelled */ });
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-bulk-action]");
    if (!btn) return;
    var region = regionOf(btn);
    if (!region) return;
    e.preventDefault();
    run(btn, region, btn.getAttribute("data-bulk-action"));
  });

  /* The region's markup is swapped wholesale after a refresh — re-sync the
   * (now empty) selection so the toolbar hides itself. */
  document.addEventListener("keytrack:list-region-loaded", function (e) {
    if (e.target && e.target.hasAttribute && e.target.hasAttribute("data-bulk-url")) {
      lastClicked = null;
      sync(e.target);
    }
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      document.querySelectorAll("[data-bulk-url]").forEach(sync);
    });
  } else {
    document.querySelectorAll("[data-bulk-url]").forEach(sync);
  }
})();
