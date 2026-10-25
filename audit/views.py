from django.shortcuts import render
from accounts.permissions import admin_required
from .models import ActivityLog


@admin_required
def log_list(request):
    action = request.GET.get("action", "").strip()
    q = request.GET.get("q", "").strip()
    logs = ActivityLog.objects.select_related("user").all()
    if action:
        logs = logs.filter(action=action)
    if q:
        logs = logs.filter(
            description__icontains=q
        ) | logs.filter(
            object_repr__icontains=q
        )
    return render(
        request,
        "audit/log_list.html",
        {
            "logs": logs[:200],
            "action": action,
            "actions": ActivityLog.Action.choices,
            "q": q,
        },
    )
