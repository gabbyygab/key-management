import time

from django.conf import settings
from django.contrib.auth.middleware import LoginRequiredMiddleware
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.cache import add_never_cache_headers, patch_vary_headers


def _wants_json(request):
    """True for fetch/XHR calls and the JSON APIs, which cannot follow a
    redirect to an HTML login page in any useful way."""
    return (request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or request.path.startswith("/api/"))


class SessionRequiredMiddleware(LoginRequiredMiddleware):
    """Default-deny: every URL needs a signed-in user unless its view is
    marked `@login_not_required` (only the login page is).

    A new view is therefore protected without anyone remembering to decorate
    it; `role_required` then narrows access by role. Browsers get a redirect
    to the login page carrying `?next=`. AJAX/API callers get a 403 JSON error, which
    `csrf.js` turns into the "session expired" prompt instead of trying to
    render the login page inside a modal.
    """

    def handle_no_permission(self, request, view_func):
        if _wants_json(request):
            # 403, not 401: the documented AJAX contract. The wording matches
            # the check in csrf.js isSessionExpired(), which shows the prompt.
            return JsonResponse({"error": "Session expired. Please sign in again."}, status=403)
        return super().handle_no_permission(request, view_func)


class SessionTouchMiddleware:
    """Keep the sliding idle timeout without a session write per request.

    Writing the session row on every page costs a round trip to the remote
    database. Instead the session is marked modified (so Django saves it and
    pushes the expiry out) only when the last touch is older than
    SESSION_TOUCH_INTERVAL seconds. The idle timeout is therefore accurate to
    within that interval.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        session = getattr(request, "session", None)
        if session is not None and session.session_key:
            now = int(time.time())
            last = session.get("_touched", 0)
            if now - last >= settings.SESSION_TOUCH_INTERVAL:
                session["_touched"] = now
        return self.get_response(request)


class NoStoreMiddleware:
    """Stop browsers (and proxies) keeping a copy of any app page.

    Without this, Back after logout re-displays the signed-in page from the
    HTTP cache/bfcache, and Back after login re-displays the login form.
    `no-store` forces a fresh request each time, which the auth checks then
    answer correctly. Static and media files are left cacheable.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.skip = tuple(u for u in (settings.STATIC_URL, settings.MEDIA_URL) if u and u != "/")

    def __call__(self, request):
        response = self.get_response(request)
        if not request.path.startswith(self.skip):
            add_never_cache_headers(response)
            response["Cache-Control"] = "no-store, no-cache, must-revalidate, private, max-age=0"
            patch_vary_headers(response, ("Cookie",))
        return response


class ForcePasswordChangeMiddleware:
    """Send a user whose password an admin set (the Add user default, or a
    reset) to the Change password page until they pick their own.

    Only that page, logout and static/media files stay reachable, so the
    temporary password cannot be used for anything but replacing it.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and getattr(user, "must_change_password", False):
            target = reverse("accounts:password_change")
            allowed = (target, reverse("accounts:logout"))
            assets = tuple(u for u in (settings.STATIC_URL, settings.MEDIA_URL) if u and u != "/")
            if request.path not in allowed and not request.path.startswith(assets):
                return redirect(target)
        return self.get_response(request)
