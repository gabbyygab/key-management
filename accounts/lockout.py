"""Sign-in lockout: LOGIN_RATE_LIMIT wrong passwords lock an account for
LOGIN_RATE_WINDOW seconds.

State lives in the default cache under two keys per account:
  login-fail:<username>   running count of wrong passwords (starts the window)
  login-lock:<username>   the epoch second the lock ends; present while locked

The lock expiry is stored (not just a counter) so the Users page can show
admins exactly how long is left. Accounts are keyed by their real username, so
signing in with the email address cannot be used to get a second set of tries.
"""
import time

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q


def _key(prefix, identifier):
    return f"{prefix}:{identifier.strip().lower()}"


def canonical(identifier):
    """The username behind what was typed (username or email); unknown
    identifiers are used as typed so they are throttled too, which keeps the
    response identical whether or not the account exists."""
    from .models import User
    identifier = (identifier or "").strip()
    username = (User.objects.filter(Q(username__iexact=identifier) | Q(email__iexact=identifier))
                .values_list("username", flat=True).first())
    return (username or identifier).lower()


def remaining(username):
    """Seconds left on the lock, or 0 when the account is not locked."""
    until = cache.get(_key("login-lock", username))
    return max(0, int(until - time.time() + 0.999)) if until else 0


def is_locked(username):
    return remaining(username) > 0


def attempts_left(username):
    return max(0, settings.LOGIN_RATE_LIMIT - cache.get(_key("login-fail", username), 0))


def record_failure(username):
    """Count a wrong password; the one that reaches the limit locks the account."""
    window = settings.LOGIN_RATE_WINDOW
    fail = _key("login-fail", username)
    # add() is a no-op if the key exists, so the window starts at the first
    # failure and is not extended by later ones.
    cache.add(fail, 0, timeout=window)
    try:
        count = cache.incr(fail)
    except ValueError:  # expired between add() and incr()
        cache.set(fail, 1, timeout=window)
        count = 1
    if count >= settings.LOGIN_RATE_LIMIT:
        cache.set(_key("login-lock", username), time.time() + window, timeout=window)


def clear(username):
    cache.delete_many([_key("login-fail", username), _key("login-lock", username)])


def minutes_text():
    minutes = max(1, settings.LOGIN_RATE_WINDOW // 60)
    return f"{minutes} minute{'s' if minutes != 1 else ''}"
