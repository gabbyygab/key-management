from django.core.paginator import Paginator
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

    # B8.11: replace the hard logs[:200] slice with real pagination so the
    # audit trail stays complete and pageable instead of being silently cut.
    paginator = Paginator(logs, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    params = request.GET.copy()
    if "page" in params:
        del params["page"]
    querystring = params.urlencode()

    return render(
        request,
        "audit/log_list.html",
        {
            "logs": page_obj,
            "page_obj": page_obj,
            "querystring": querystring,
            "action": action,
            "actions": ActivityLog.Action.choices,
            "q": q,
        },
    )
