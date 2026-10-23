/* modal-form.js — AJAX form-in-modal helper (F1.3).
 *
 * The pattern for every add/edit modal:
 *   1. GET <endpoint>?modal=1  -> the server returns just the form partial
 *   2. inject it into the modal body, run lucide.createIcons()
 *   3. POST the form via fetch() with X-CSRFToken
 *   4. 200/201 -> close, toast, refresh the list region
 *      422     -> re-render the partial in place, modal STAYS OPEN, typed
 *                 input preserved (the server echoes bound data + errors)
 *      403     -> "You do not have permission", close
 *
 * Progressive enhancement: the full-page form templates keep working with no
 * JS. A plain GET still returns the whole page; only ?modal=1 returns the
 * partial. So a trigger should be a real <a href> — this script intercepts it.
 *
 * Markup:
 *   <a href="{% url 'keys:key_create' %}"
 *      data-modal-form="key-form"
 *      data-modal-refresh="#key-table">+ Add key</a>
 *
 *   <div id="key-form" role="dialog" ...>
 *     <div data-modal-body></div>
 *   </div>
 *
 * NOTE: the ?modal=1 endpoints this drives are task F4.* / backend work and
 * do not exist yet. This file is the agreed client half of that contract.
 *
 * Requires: csrf.js, modal.js, toast.js.
 */
(function () {
  "use strict";

  var KeyTrack = (window.KeyTrack = window.KeyTrack || {});

  var FOCUS_SELECTOR =
    "input:not([type=hidden]):not([disabled]), select:not([disabled]), textarea:not([disabled])";

  function bodyOf(modal) {
    return modal.querySelector("[data-modal-body]") || modal;
  }

  function setLoading(modal) {
    bodyOf(modal).innerHTML =
      '<div class="py-10 text-center text-[13px] text-stone-400">Loading…</div>';
  }

  /* Inject markup and re-init icons. Every injection path goes through here
   * so the createIcons() call can never be forgotten. */
  function inject(modal, html) {
    var body = bodyOf(modal);
    body.innerHTML = html;
    KeyTrack.refreshIcons(body);
    /* A form may itself contain a live search (instructor picker, borrower
     * select) — wire it now that it exists in the DOM. */
    if (KeyTrack.bindSearch) KeyTrack.bindSearch(body);
    var focus = body.querySelector(FOCUS_SELECTOR);
    if (focus) focus.focus();
    return body;
  }

  function withModalParam(url) {
    var u = new URL(url, window.location.origin);
    u.searchParams.set("modal", "1");
    return u.toString();
  }

  /* ---- load ---------------------------------------------------------- */

  function load(trigger) {
    var modalId = trigger.getAttribute("data-modal-form");
    var modal = document.getElementById(modalId);
    var url = trigger.getAttribute("href") || trigger.getAttribute("data-modal-url");
    if (!modal || !url) return;

    modal.dataset.refresh = trigger.getAttribute("data-modal-refresh") || "";
    modal.dataset.action = url;

    setLoading(modal);
    window.KeyTrackModal.open(modalId);

    fetch(withModalParam(url), {
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    })
      .then(function (res) {
        if (res.status === 403) {
          KeyTrack.toast("You do not have permission.", "error");
          window.KeyTrackModal.close(modalId);
          throw new Error("forbidden");
        }
        if (!res.ok) throw new Error("load failed: " + res.status);
        return res.text();
      })
      .then(function (html) {
        inject(modal, html);
      })
      .catch(function (err) {
        if (err.message === "forbidden") return;
        inject(
          modal,
          '<p class="py-8 text-center text-[13px] text-red-700">Could not load this form. Please try again.</p>'
        );
      });
  }

  /* ---- submit -------------------------------------------------------- */

  function submitButtonFor(form, modal) {
    var inside = form.querySelector("[type=submit]");
    if (inside) return inside;
    /* Sticky-footer buttons live outside the <form> and point at it by id. */
    if (form.id) {
      return modal.querySelector("[type=submit][form=" + CSS.escape(form.id) + "]");
    }
    return null;
  }

  function submit(form, modal) {
    var modalId = modal.id;
    var button = submitButtonFor(form, modal);

    /* Guard against the double-submit that would double-issue a key. */
    if (form.dataset.submitting === "1") return;
    form.dataset.submitting = "1";

    var originalHtml = null;
    if (button) {
      originalHtml = button.innerHTML;
      button.disabled = true;
      button.innerHTML =
        '<i data-lucide="loader-circle" class="w-4 h-4 animate-spin"></i> Saving…';
      KeyTrack.refreshIcons(button);
    }

    function restore() {
      form.dataset.submitting = "0";
      if (button) {
        button.disabled = false;
        if (originalHtml !== null) button.innerHTML = originalHtml;
        KeyTrack.refreshIcons(button);
      }
    }

    var action =
      form.getAttribute("action") || modal.dataset.action || window.location.href;

    fetch(withModalParam(action), {
      method: "POST",
      /* FormData keeps file inputs working; do NOT set Content-Type —
       * the browser must supply the multipart boundary itself. */
      headers: KeyTrack.formHeaders(),
      body: new FormData(form),
      credentials: "same-origin",
    })
      .then(function (res) {
        if (res.status === 403) {
          KeyTrack.toast("You do not have permission.", "error");
          window.KeyTrackModal.close(modalId);
          return null;
        }

        /* 422 = business-rule / validation failure. Expected, not a crash:
         * put the server-rendered partial back with its inline field errors
         * and leave the modal open with the user's input intact. */
        if (res.status === 422 || res.status === 400) {
          var type = res.headers.get("Content-Type") || "";
          if (type.indexOf("application/json") !== -1) {
            return res.json().then(function (data) {
              KeyTrack.toast(
                data.error || "Please correct the errors below.",
                "error"
              );
              restore();
              return null;
            });
          }
          return res.text().then(function (html) {
            inject(modal, html);
            restore();
            return null;
          });
        }

        if (res.status === 200 || res.status === 201) {
          KeyTrack.toast(
            form.getAttribute("data-success-message") || "Saved.",
            "success"
          );
          window.KeyTrackModal.close(modalId);
          refreshRegion(modal);
          return null;
        }

        throw new Error("save failed: " + res.status);
      })
      .catch(function () {
        KeyTrack.toast("Could not save. Please try again.", "error");
        restore();
      });
  }

  /* Refresh just the affected list region, or reload the page for v1 when the
   * template has not opted into a region. */
  function refreshRegion(modal) {
    var selector = modal.dataset.refresh;
    var region = selector ? document.querySelector(selector) : null;
    if (!region) {
      window.location.reload();
      return;
    }
    fetch(window.location.href, {
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    })
      .then(function (res) {
        return res.text();
      })
      .then(function (html) {
        var fresh = new DOMParser()
          .parseFromString(html, "text/html")
          .querySelector(selector);
        if (!fresh) {
          window.location.reload();
          return;
        }
        region.innerHTML = fresh.innerHTML;
        KeyTrack.refreshIcons(region);
        if (KeyTrack.bindSearch) KeyTrack.bindSearch(region);
      })
      .catch(function () {
        window.location.reload();
      });
  }

  /* ---- delegated listeners ------------------------------------------- */

  document.addEventListener("click", function (e) {
    var trigger = e.target.closest("[data-modal-form]");
    if (!trigger) return;
    e.preventDefault();
    load(trigger);
  });

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.matches("form")) return;
    var modal = form.closest("[role=dialog]");
    /* Only hijack forms that live inside an AJAX modal body. A plain
     * server-rendered form (the logout POST) must submit normally. */
    if (!modal || !modal.querySelector("[data-modal-body]")) return;
    if (form.hasAttribute("data-no-ajax")) return;
    e.preventDefault();
    submit(form, modal);
  });

  KeyTrack.loadModalForm = load;
  KeyTrack.refreshRegion = refreshRegion;
})();
