from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver

from .services import Action, log_activity


@receiver(user_logged_in)
def audit_login(sender, request, user, **kwargs):
    if user is not None and getattr(user, "pk", None):
        log_activity(user, Action.LOGIN, obj=user, description=f"{user.username} logged in.",
                     request=request)


@receiver(user_logged_out)
def audit_logout(sender, request, user, **kwargs):
    if user is not None and getattr(user, "pk", None):
        log_activity(user, Action.LOGOUT, obj=user, description=f"{user.username} logged out.",
                     request=request)
