from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse


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
