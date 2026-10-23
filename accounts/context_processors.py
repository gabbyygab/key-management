from .services import pending_count


def pending_approvals(request):
    """Expose pending_approvals_count to templates for admin users."""
    user = getattr(request, "user", None)
    if user and user.is_authenticated and (user.is_superuser or getattr(user, "role", None) == "ADMIN"):
        return {"pending_approvals_count": pending_count()}
    return {"pending_approvals_count": 0}
"""accounts/context_processors.py — per-request context injected into every template.

F2.3: The guard shift indicator in navbar.html needs the active shift on every page,
not just the POS screen.  A context processor is the right place: one DB query per
request, zero duplication across views.

Registered in config/settings.py TEMPLATES[0]['OPTIONS']['context_processors'].
"""


def navbar_context(request):
    """Inject `navbar_shift` for authenticated guards on every page.

    * ADMIN / superuser: None (they see the quick-add + dropdown instead).
    * GUARD: the active GuardShift row, or None if off-duty.
    * Other roles: None (context key still present, just falsy).
    """
    if not request.user.is_authenticated:
        return {}

    # Avoid a DB hit for admins — they don't show the shift indicator.
    if request.user.is_superuser or getattr(request.user, "role", None) == "ADMIN":
        return {"navbar_shift": None}

    if getattr(request.user, "role", None) == "GUARD":
        from transactions.services import get_active_shift
        return {"navbar_shift": get_active_shift(request.user)}

    return {"navbar_shift": None}
