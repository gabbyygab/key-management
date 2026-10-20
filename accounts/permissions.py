from .decorators import role_required


def admin_required(view):
    return role_required("ADMIN")(view)


def guard_required(view):
    return role_required("GUARD")(view)


def admin_or_guard_required(view):
    return role_required("ADMIN", "GUARD")(view)
