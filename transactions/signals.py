from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver
from django.utils import timezone

from .models import GuardShift


@receiver(user_logged_in)
def start_shift_on_login(sender, request, user, **kwargs):
    """A guard's shift starts automatically when they log in.

    Reuses the active shift if one is already open (double login,
    stale session) so we never stack duplicate open shifts.
    """
    if user is None or not getattr(user, "pk", None):
        return
    if not GuardShift.objects.filter(guard=user, is_on_duty=True).exists():
        GuardShift.objects.create(guard=user, post="Main Desk")


@receiver(user_logged_out)
def end_shift_on_logout(sender, request, user, **kwargs):
    """Logging out ends the shift."""
    if user is None or not getattr(user, "pk", None):
        return
    now = timezone.now()
    GuardShift.objects.filter(guard=user, is_on_duty=True).update(
        is_on_duty=False, shift_end=now)
