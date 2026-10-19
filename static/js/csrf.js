/* Django CSRF token for fetch() calls. Loaded before every other KEYTRACK script. */
function getCsrfToken() {
  return document.cookie
    .split("; ")
    .find(function (c) { return c.startsWith("csrftoken="); })
    ?.split("=")[1];
}

/* fetch() with the headers Django expects on a JSON POST. */
function postJson(url, payload) {
  return fetch(url, {
    method: "POST",
    headers: {
      "X-CSRFToken": getCsrfToken(),
      "Content-Type": "application/json",
      "X-Requested-With": "XMLHttpRequest",
    },
    body: JSON.stringify(payload || {}),
  });
}
