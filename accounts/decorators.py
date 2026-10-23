from functools import wraps
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            if request.user.is_superuser or request.user.role in roles:
                return view(request, *args, **kwargs)
            exc = PermissionDenied()
            exc.required_role = "/".join(roles) if roles else "authorized role"
            raise exc
        return wrapper
    return decorator
