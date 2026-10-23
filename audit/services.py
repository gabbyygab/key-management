from .models import ActivityLog


def log_activity(user, action, obj=None, description="", request=None, changes=None):
    ip_address = None
    if request:
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            ip_address = x_forwarded_for.split(",")[0].strip()
        else:
            ip_address = request.META.get("REMOTE_ADDR")

    model_name = ""
    object_id = ""
    object_repr = ""
    if obj is not None:
        model_name = obj._meta.model_name
        object_id = str(getattr(obj, "pk", ""))
        object_repr = str(obj)[:255]

    user_obj = user if getattr(user, "is_authenticated", False) else None

    return ActivityLog.objects.create(
        user=user_obj,
        action=action,
        model_name=model_name,
        object_id=object_id,
        object_repr=object_repr,
        changes=changes or {},
        description=description,
        ip_address=ip_address,
    )
