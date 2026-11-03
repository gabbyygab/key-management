/* Offering form: show the lab fields only for a lecture + lab offering, and
   narrow the subject list to the section's curriculum (see below).
   The form arrives by AJAX, so everything is delegated from the document. */
(function () {
  "use strict";
  function sync(form) {
    var sel = form.querySelector('[name="meeting_format"]');
    var fmt = sel ? sel.value : "STANDARD";
    form.querySelectorAll("[data-show-for-format]").forEach(function (el) {
      el.classList.toggle("hidden", el.getAttribute("data-show-for-format") !== fmt);
    });
    form.querySelectorAll("[data-hide-for-format]").forEach(function (el) {
      el.classList.toggle("hidden", el.getAttribute("data-hide-for-format") === fmt);
    });
  }
  document.addEventListener("change", function (e) {
    if (e.target && e.target.name === "meeting_format") {
      var f = e.target.closest("[data-offering-form]");
      if (f) sync(f);
    }
  });
  /* ---- curriculum: narrow the subject list to the section's slot -------
   * When the term or section changes, ask the server which subjects that
   * section may still be offered this term (its program/year curriculum for
   * the term's semester, minus what it already has) and rebuild the subject
   * <select>. searchable-select.js rebuilds its list from the <select> on
   * open, and re-reads the label on "change". */
  var seq = 0;

  function setHint(form, text, warn, href) {
    var hint = form.querySelector("[data-curriculum-hint]");
    if (!hint) return;
    var span = hint.querySelector("[data-curriculum-hint-text]");
    if (span) span.textContent = text;
    var link = hint.querySelector("[data-curriculum-hint-link]");
    if (link && href) link.setAttribute("href", href);
    hint.classList.toggle("text-amber-700", !!warn);
    hint.classList.toggle("text-stone-500", !warn);
  }

  /* Shared group classes: a group with sibling groups can be filed as one
   * schedule for all of them; a whole block with groups already is one. */
  function setSharing(form, info) {
    info = info || {};
    var toggle = form.querySelector("[data-sharing-toggle]");
    var note = form.querySelector("[data-sharing-note]");
    if (toggle) {
      toggle.classList.toggle("hidden", !info.available);
      var groups = toggle.querySelector("[data-sharing-groups]");
      if (groups) {
        groups.textContent = info.available
          ? info.block_code + ": " + info.groups + " in one room, at one time." : "";
      }
      var box = toggle.querySelector('[name="shared"]');
      if (box && !info.available) box.checked = false;
    }
    if (note) {
      note.classList.toggle("hidden", !info.is_shared);
      var text = note.querySelector("[data-sharing-note-text]");
      if (text) {
        text.textContent = info.is_shared ? "Shared class: " + info.groups + " of "
          + info.block_code + " attend every meeting together." : "";
      }
    }
  }

  function setOptions(select, items, placeholder) {
    var keep = select.value;
    select.textContent = "";
    var first = document.createElement("option");
    first.value = "";
    first.textContent = placeholder;
    select.appendChild(first);
    var still = false;
    items.forEach(function (it) {
      var opt = document.createElement("option");
      opt.value = String(it.id);
      opt.textContent = it.label;
      if (opt.value === keep) still = true;
      select.appendChild(opt);
    });
    select.value = still ? keep : "";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function refreshSubjects(form) {
    var url = form.getAttribute("data-offerable-url");
    var section = form.querySelector('[name="section"]');
    var term = form.querySelector('[name="term"]');
    var subject = form.querySelector('[name="subject"]');
    if (!url || !section || !subject) return;

    if (!section.value) {
      setOptions(subject, [], "Pick a section first");
      subject.disabled = true;
      subject.dispatchEvent(new Event("change", { bubbles: true }));
      setHint(form, "Pick a section to see the subjects of its curriculum.", false);
      setSharing(form, null);
      return;
    }
    var mine = ++seq;
    var params = new URLSearchParams({
      section: section.value,
      term: term ? term.value : "",
      offering: form.getAttribute("data-offering-id") || "",
    });
    fetch(url + "?" + params.toString(), {
      headers: { "X-Requested-With": "XMLHttpRequest", Accept: "application/json" },
      credentials: "same-origin",
    })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (data) {
        if (mine !== seq) return; /* a newer pick already answered */
        subject.disabled = false;
        setOptions(subject, data.subjects || [], data.subjects && data.subjects.length
          ? "Pick a subject" : "Nothing left to offer");
        setHint(form, data.message || "", !data.restricted || !(data.subjects || []).length,
          data.curriculum_url);
        setSharing(form, data.sharing);
      })
      .catch(function () {
        if (mine !== seq) return;
        subject.disabled = false;
        if (window.KeyTrack && KeyTrack.toast) {
          KeyTrack.toast("Could not load the curriculum subjects. Every subject is still checked when you save.", "warning");
        }
      });
  }

  document.addEventListener("change", function (e) {
    var name = e.target && e.target.name;
    if (name !== "section" && name !== "term") return;
    var f = e.target.closest("[data-offering-form]");
    if (f) refreshSubjects(f);
  });

  /* A freshly opened create form has no section yet: lock the subject until
   * one is picked. An edit form (or a 422 re-render) was already narrowed by
   * the server, so it is left as rendered. */
  function primeSubjects(form) {
    var section = form.querySelector('[name="section"]');
    if (section && !section.value) refreshSubjects(form);
  }

  new MutationObserver(function (muts) {
    muts.forEach(function (m) {
      m.addedNodes.forEach(function (n) {
        if (n.nodeType !== 1) return;
        var forms = n.matches && n.matches("[data-offering-form]") ? [n]
          : n.querySelectorAll ? n.querySelectorAll("[data-offering-form]") : [];
        forms.forEach(function (f) { sync(f); primeSubjects(f); });
      });
    });
  }).observe(document.body, { childList: true, subtree: true });
  document.querySelectorAll("[data-offering-form]").forEach(function (f) { sync(f); primeSubjects(f); });

  /* Auto-open the offering modal when landing with ?add=1 or ?add=true */
  function checkAutoOpen() {
    try {
      var params = new URLSearchParams(window.location.search);
      if (params.get("add") === "1" || params.get("add") === "true") {
        var btn = document.querySelector('[data-modal-form="offering-modal"]');
        if (btn) {
          params.delete("add");
          var searchStr = params.toString();
          var cleanUrl = window.location.pathname + (searchStr ? "?" + searchStr : "") + window.location.hash;
          window.history.replaceState(null, "", cleanUrl);
          btn.click();
        }
      }
    } catch (err) {
      /* Ignore unsupported URLSearchParams or history API */
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", checkAutoOpen);
  } else {
    checkAutoOpen();
  }
})();
