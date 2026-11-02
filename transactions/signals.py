from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver
from django.utils import timezone

from .models import GuardShift

DEFAULT_POST = "Main Desk"


def post_for(user):
    """B2.5 / B8.9: the admin-assigned post, else the old default."""
    post = getattr(user, "assigned_post", None)
    if post is not None and post.is_active:
        return post.name
    return DEFAULT_POST


@receiver(user_logged_in)
def start_shift_on_login(sender, request, user, **kwargs):
    """A guard's shift starts automatically when they log in.

    Only GUARD accounts get a shift: an admin logging in is not on duty, and
    counting them would put admins in the "guards on duty" list.

    Reuses the active shift if one is already open (double login,
    stale session) so we never stack duplicate open shifts.
    """
    if user is None or not getattr(user, "pk", None):
        return
    if getattr(user, "role", None) != "GUARD":
        return
    if not GuardShift.objects.filter(guard=user, is_on_duty=True).exists():
        GuardShift.objects.create(guard=user, post=post_for(user))


@receiver(user_logged_out)
def end_shift_on_logout(sender, request, user, **kwargs):
    """Logging out ends the shift."""
    if user is None or not getattr(user, "pk", None):
        return
    now = timezone.now()
    GuardShift.objects.filter(guard=user, is_on_duty=True).update(
        is_on_duty=False, shift_end=now)
