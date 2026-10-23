from .services import pending_count


def pending_approvals(request):
    """Expose pending_approvals_count to templates for admin users."""
    user = getattr(request, "user", None)
    if user and user.is_authenticated and (user.is_superuser or getattr(user, "role", None) == "ADMIN"):
        return {"pending_approvals_count": pending_count()}
    return {"pending_approvals_count": 0}
