"""Supabase Auth (GoTrue) client for KEYTRACK logins.

Supabase checks the password; Django keeps the session, the role and the
approval status. Two kinds of call:

* ``sign_in`` uses the anon/publishable key, the same call a browser client
  would make. It proves the password and returns the Supabase user id.
* ``create_user`` / ``update_user`` / ``set_banned`` use the service-role key
  against ``/auth/v1/admin/users``. Only admin screens call these, server side.

Built on urllib like supabase_sync/client.py, so no new dependency.
"""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.conf import settings

# Supabase has no "disable user" flag; a long ban is how its dashboard does it.
BAN_FOREVER = "876000h"  # ~100 years
UNBAN = "none"


class SupabaseAuthError(Exception):
    """Supabase answered, but refused the request."""

    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


class InvalidCredentials(SupabaseAuthError):
    """Wrong email/password, or the Supabase user is banned."""


class SupabaseAuthUnavailable(SupabaseAuthError):
    """Supabase could not be reached, or answered with a server error."""


def is_enabled():
    return bool(getattr(settings, "SUPABASE_AUTH_ENABLED", False))


class SupabaseAuthClient:
    def __init__(self, *, url=None, anon_key=None, service_role_key=None, timeout=None):
        self.url = (url if url is not None else settings.SUPABASE_URL or "").rstrip("/")
        self.anon_key = anon_key if anon_key is not None else settings.SUPABASE_ANON_KEY
        self.service_role_key = (service_role_key if service_role_key is not None
                                 else settings.SUPABASE_SERVICE_ROLE_KEY)
        self.timeout = timeout or settings.SUPABASE_TIMEOUT
        if not self.url:
            raise SupabaseAuthError("SUPABASE_URL is not set.")

    # -- sign-in (anon key) -------------------------------------------------

    def sign_in(self, email, password):
        """Return the Supabase user dict if the password is right.

        Raises InvalidCredentials on a wrong password or banned user, and
        SupabaseAuthUnavailable when Supabase cannot answer.
        """
        if not self.anon_key:
            raise SupabaseAuthError("SUPABASE_ANON_KEY is not set.")
        try:
            data = self._request("POST", "/token?grant_type=password", self.anon_key,
                                 {"email": email, "password": password})
        except SupabaseAuthError as e:
            # GoTrue answers 400 invalid_credentials for a wrong password and
            # 400 user_banned for a deactivated account.
            if e.status in (400, 401, 403, 422):
                raise InvalidCredentials(str(e), status=e.status, code=e.code) from None
            raise
        return data["user"]

    # -- admin (service-role key) -------------------------------------------

    def create_user(self, *, email, password, metadata=None):
        """Create a confirmed Supabase user. Returns the user dict."""
        return self._admin("POST", "/admin/users", {
            "email": email,
            "password": password,
            "email_confirm": True,  # admins vouch for guards; no confirmation mail
            "user_metadata": metadata or {},
        })

    def update_user(self, uid, **attrs):
        """Change email/password/ban_duration/user_metadata of a Supabase user."""
        if "email" in attrs:
            attrs.setdefault("email_confirm", True)
        return self._admin("PUT", f"/admin/users/{quote(str(uid))}", attrs)

    def set_banned(self, uid, banned):
        return self.update_user(uid, ban_duration=BAN_FOREVER if banned else UNBAN)

    def delete_user(self, uid):
        return self._admin("DELETE", f"/admin/users/{quote(str(uid))}", None)

    def find_user_by_email(self, email, *, per_page=500):
        """Page through the admin user list. Fine at campus scale (hundreds)."""
        email = email.strip().lower()
        page = 1
        while True:
            data = self._admin("GET", f"/admin/users?page={page}&per_page={per_page}", None) or {}
            users = data.get("users", [])
            for u in users:
                if (u.get("email") or "").lower() == email:
                    return u
            if len(users) < per_page:
                return None
            page += 1

    # -- internals ------------------------------------------------------------

    def _admin(self, method, path, body):
        if not self.service_role_key:
            raise SupabaseAuthError("SUPABASE_SERVICE_ROLE_KEY is not set.")
        return self._request(method, path, self.service_role_key, body)

    def _request(self, method, path, key, body):
        headers = {"apikey": key, "Accept": "application/json"}
        # Legacy keys are JWTs and also go in Authorization. The newer
        # sb_publishable_/sb_secret_ keys are accepted in `apikey` alone.
        if key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {key}"
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        req = Request(f"{self.url}/auth/v1{path}", data=data, method=method, headers=headers)
        try:
            with urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 (https URL from settings)
                raw = resp.read()
                return json.loads(raw) if raw else None
        except HTTPError as e:
            err = _error_from_http(e)
            if e.code >= 500 or e.code == 429:
                raise SupabaseAuthUnavailable(str(err), status=e.code, code=err.code) from None
            raise err from None
        except (URLError, TimeoutError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise SupabaseAuthUnavailable(f"Could not reach Supabase Auth: {reason}") from None


def _error_from_http(e):
    code = None
    message = f"HTTP {e.code}"
    try:
        payload = json.loads(e.read() or b"{}")
        code = payload.get("error_code") or payload.get("code") or payload.get("error")
        message = (payload.get("msg") or payload.get("message")
                   or payload.get("error_description") or message)
    except (ValueError, AttributeError):
        pass
    if not isinstance(code, str):
        code = None
    return SupabaseAuthError(message, status=e.code, code=code)


def get_client():
    return SupabaseAuthClient()
