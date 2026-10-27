import datetime

from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render
from accounts.permissions import admin_required
from .models import ActivityLog


# Lucide icon + Tailwind colour pill per action type.
# Icons are rendered as <i data-lucide="..."> and initialised by init-icons.js.
ACTION_ICONS = {
    ActivityLog.Action.LOGIN: "log-in",
    ActivityLog.Action.LOGOUT: "log-out",
    ActivityLog.Action.CREATE: "plus",
    ActivityLog.Action.UPDATE: "edit-2",
    ActivityLog.Action.DELETE: "trash-2",
    ActivityLog.Action.BORROW: "key",
    ActivityLog.Action.RETURN: "check-circle",
    ActivityLog.Action.APPROVE: "check-circle-2",
    ActivityLog.Action.REJECT: "x-circle",
    ActivityLog.Action.OVERRIDE: "alert-triangle",
}

ACTION_COLOURS = {
    ActivityLog.Action.LOGIN: ("bg-blue-50", "text-blue-800", "border-blue-200"),
    ActivityLog.Action.LOGOUT: ("bg-stone-50", "text-stone-700", "border-stone-200"),
    ActivityLog.Action.CREATE: ("bg-emerald-50", "text-emerald-800", "border-emerald-200"),
    ActivityLog.Action.UPDATE: ("bg-blue-50", "text-blue-800", "border-blue-200"),
    ActivityLog.Action.DELETE: ("bg-red-50", "text-red-800", "border-red-200"),
    ActivityLog.Action.BORROW: ("bg-blue-50", "text-blue-800", "border-blue-200"),
    ActivityLog.Action.RETURN: ("bg-green-50", "text-green-800", "border-green-200"),
    ActivityLog.Action.APPROVE: ("bg-green-50", "text-green-800", "border-green-200"),
    ActivityLog.Action.REJECT: ("bg-red-50", "text-red-800", "border-red-200"),
    ActivityLog.Action.OVERRIDE: ("bg-amber-50", "text-amber-800", "border-amber-200"),
}


@admin_required
def log_list(request):
    """Read-only audit log list.

    Admin only. Filterable by user, action type, date range, model and
    free-text search. Results are paginated because the table grows fast.
    """
    user_filter = request.GET.get("user", "").strip()
    action = request.GET.get("action", "").strip()
    q = request.GET.get("q", "").strip()
    model_filter = request.GET.get("model", "").strip()
    start_date = (
        request.GET.get("start_date", "").strip()
        or request.GET.get("date_from", "").strip()
    )
    end_date = (
        request.GET.get("end_date", "").strip()
        or request.GET.get("date_to", "").strip()
    )

    logs = ActivityLog.objects.select_related("user").all()

    if user_filter:
        if user_filter.isdigit():
            logs = logs.filter(user_id=int(user_filter))
        else:
            logs = logs.filter(
                Q(user__username__iexact=user_filter)
                | Q(user__first_name__icontains=user_filter)
                | Q(user__last_name__icontains=user_filter)
            )

    if action:
        logs = logs.filter(action=action)

    if model_filter:
        logs = logs.filter(model_name=model_filter)

    if q:
        logs = logs.filter(
            Q(description__icontains=q) | Q(object_repr__icontains=q)
        )

    if start_date:
        try:
            d_start = datetime.date.fromisoformat(start_date)
            logs = logs.filter(created_at__date__gte=d_start)
        except ValueError:
            pass

    if end_date:
        try:
            d_end = datetime.date.fromisoformat(end_date)
            logs = logs.filter(created_at__date__lte=d_end)
        except ValueError:
            pass

    # B8.11: real pagination so the growing audit table stays pageable.
    paginator = Paginator(logs, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # Attach per-action icon + colour pill classes and a safe user label to
    # each log row so the template can render them without custom tags or
    # crashing on system (user=None) entries.
    for log in page_obj:
        log.action_icon = ACTION_ICONS.get(log.action)
        bg, text, border = ACTION_COLOURS.get(
            log.action, ("bg-stone-100", "text-stone-700", "border-stone-200")
        )
        log.action_bg = bg
        log.action_text = text
        log.action_border = border
        if log.user:
            log.user_label = log.user.get_full_name() or log.user.username
            log.user_username = log.user.username
        else:
            log.user_label = "System"
            log.user_username = None

    # Preserve query string (without 'page') for pagination links.
    params = request.GET.copy()
    if "page" in params:
        del params["page"]
    querystring = params.urlencode()

    # Filter dropdown options: users who have logged activity, and models
    # that appear in the log, so empty choices never appear.
    users = (
        logs.values_list("user", flat=True)
        .distinct()
        .exclude(user__isnull=True)
    )
    filter_users = (
        ActivityLog.objects.filter(user__in=users)
        .select_related("user")
        .order_by("user__first_name", "user__last_name", "user__username")
        .values_list("user__pk", "user__username", "user__first_name", "user__last_name")
        .distinct()
    )
    models = (
        logs.exclude(model_name="")
        .values_list("model_name", flat=True)
        .distinct()
        .order_by("model_name")
    )

    return render(
        request,
        "audit/log_list.html",
        {
            "logs": page_obj,
            "page_obj": page_obj,
            "querystring": querystring,
            "user_filter": user_filter,
            "action": action,
            "actions": ActivityLog.Action.choices,
            "q": q,
            "model_filter": model_filter,
            "models": models,
            "filter_users": filter_users,
            "start_date": start_date,
            "end_date": end_date,
        },
    )
