/* Login page only (templates/accounts/login.html).
 *
 * Two small enhancements, both progressive: without JS the form still posts
 * and the password field stays a normal password field.
 *
 *   1. Show / hide password toggle.
 *   2. Loading state on the sign-in button. The login POST is a full-page
 *      navigation that can take a second (SupabaseAuthBackend talks to
 *      /auth/v1/token over the network), so the button has to say something.
 *      It also blocks the double submit that would fire two sign-in requests.
 */
(function () {
  "use strict";

  var SPINNER =
    '<svg class="submit-spinner" width="16" height="16" viewBox="0 0 24 24" fill="none" ' +
    'xmlns="http://www.w3.org/2000/svg" aria-hidden="true">' +
    '<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2" opacity=".35"/>' +
    '<path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>' +
    "</svg>";

  function initPasswordToggle() {
    var toggle = document.querySelector("[data-password-toggle]");
    var input = document.getElementById("password");
    if (!toggle || !input) return;

    toggle.hidden = false;
    toggle.addEventListener("click", function () {
      var showing = toggle.getAttribute("aria-pressed") === "true";
      toggle.setAttribute("aria-pressed", showing ? "false" : "true");
      toggle.setAttribute("aria-label", showing ? "Show password" : "Hide password");
      input.type = showing ? "password" : "text";
      /* Keep the caret where the user left it. */
      input.focus();
      try {
        var end = input.value.length;
        input.setSelectionRange(end, end);
      } catch (e) {
        /* setSelectionRange is not allowed on every input type/browser. */
      }
    });
  }

  function initSubmitState() {
    var form = document.querySelector(".login-form");
    if (!form) return;
    var button = form.querySelector(".submit-button");
    if (!button) return;

    var originalHtml = button.innerHTML;

    function reset() {
      form.dataset.submitting = "0";
      button.disabled = false;
      button.removeAttribute("aria-busy");
      button.innerHTML = originalHtml;
    }

    form.addEventListener("submit", function (event) {
      if (form.dataset.submitting === "1") {
        event.preventDefault();
        return;
      }
      form.dataset.submitting = "1";
      button.setAttribute("aria-busy", "true");
      button.innerHTML = SPINNER + "<span>Signing in…</span>";
      /* Disable after the browser has taken the form's data, so the click
         still submits. */
      window.setTimeout(function () {
        button.disabled = true;
      }, 0);
    });

    /* Coming back with the Back button restores the page from the bfcache
       with the button still spinning — put it back. */
    window.addEventListener("pageshow", function (event) {
      if (event.persisted) reset();
    });
  }

  initPasswordToggle();
  initSubmitState();
})();
