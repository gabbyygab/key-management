"""audit/services.py — the one write path into the activity log (B6.2).

Every admin mutation, every borrow/return, login/logout, approve/reject and
every schedule override by a guard goes through log_activity(). Logging must
never break the action it records, so failures are swallowed and reported to
the Python logger instead of bubbling up into a view.
"""
import logging

from .models import ActivityLog

logger = logging.getLogger(__name__)

Action = ActivityLog.Action


def client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def log_activity(user, action, obj=None, description="", request=None, changes=None):
    """Record one audit row. Returns the ActivityLog, or None if it failed."""
    model_name = ""
    object_id = ""
    object_repr = ""
    if obj is not None:
        model_name = obj.__class__.__name__
        object_id = str(getattr(obj, "pk", "") or "")
        object_repr = str(obj)[:255]

    user_obj = user if getattr(user, "is_authenticated", False) else None
    user_agent = ""
    if request is not None:
        user_agent = (request.META.get("HTTP_USER_AGENT") or "")[:255]

    try:
        return ActivityLog.objects.create(
            user=user_obj,
            action=action,
            model_name=model_name,
            object_id=object_id,
            object_repr=object_repr,
            changes=changes or {},
            description=description,
            ip_address=client_ip(request),
            user_agent=user_agent,
        )
    except Exception:  # pragma: no cover - logging must never break the caller
        logger.exception("Could not write audit log entry (%s %s)", action, model_name)
        return None


def form_changes(form):
    """{field: [old, new]} for a bound ModelForm, for UPDATE rows.

    Uses form.initial (what was rendered) against cleaned_data (what was
    saved). Values are stringified so the JSONField never chokes on a model
    instance or a date.
    """
    changes = {}
    for name in getattr(form, "changed_data", []):
        old = form.initial.get(name)
        new = form.cleaned_data.get(name)
        if hasattr(old, "pk"):
            old = old.pk
        if hasattr(new, "pk"):
            new = new.pk
        changes[name] = [None if old is None else str(old), None if new is None else str(new)]
    return changes


def log_form_save(request, form, obj, *, created):
    """Convenience wrapper for the admin create/update views."""
    verb = "Created" if created else "Updated"
    return log_activity(
        request.user,
        Action.CREATE if created else Action.UPDATE,
        obj=obj,
        description=f"{verb} {obj.__class__.__name__.lower()} {obj}.",
        request=request,
        changes=None if created else form_changes(form),
    )
