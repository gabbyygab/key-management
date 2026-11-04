from urllib.parse import urlencode

from django.contrib import messages
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import render
from django.urls import reverse

from accounts.cache_utils import remember
from accounts.decorators import role_required
from .services import (
    VALID_EXPORTS,
    export_report,
    get_borrower_frequency_report,
    get_guard_activity_report,
    get_key_utilisation_report,
    get_overdue_report,
    get_room_usage_report,
    get_summary_metrics,
    parse_date_range,
)

# (key, label, lucide icon). The first is the landing tab; the rest are the
# report types, one tab each.
TABS = [
    ("overview", "Overview", "layout-dashboard"),
    ("key_utilisation", "Key Utilisation", "key-round"),
    ("overdue", "Overdue", "clock-alert"),
    ("guard_activity", "Guard Activity", "shield-check"),
    ("room_usage", "Room Usage", "door-open"),
    ("borrower_frequency", "Borrower Frequency", "users"),
]
TAB_KEYS = [t[0] for t in TABS]


@role_required("ADMIN")
def reports_index(request):
    """
    Reports page for administrators.

    One tab per report (``?section=``): Overview, Key Utilisation, Overdue,
    Guard Activity, Room Usage and Borrower Frequency. Every report honours the
    ``start_date``/``end_date`` range, and every tab exports as PDF.
    """
    start_date_param = request.GET.get("start_date", "")
    end_date_param = request.GET.get("end_date", "")
    active_section = request.GET.get("section", "overview")
    if active_section not in TAB_KEYS:
        active_section = "overview"

    date_filter = parse_date_range(start_date_param, end_date_param)

    if date_filter["error"]:
        messages.error(request, date_filter["error"])

    start_dt = date_filter["start_dt"]
    end_dt = date_filter["end_dt"]

    # Fetch data for all reports (cached; dropped when transactions, keys or
    # rooms change).
    def build():
        return {
            "summary": get_summary_metrics(start_dt, end_dt),
            "key_utilisation": get_key_utilisation_report(start_dt, end_dt),
            "overdue_list": get_overdue_report(start_dt, end_dt),
            "guard_activity": get_guard_activity_report(start_dt, end_dt),
            "room_usage": get_room_usage_report(start_dt, end_dt),
            "borrower_frequency": get_borrower_frequency_report(start_dt, end_dt),
        }

    data = remember("reports", ["inventory", "txn", "shift"], build,
                    parts=[date_filter["start_date_str"], date_filter["end_date_str"]])

    counts = {
        "key_utilisation": len(data["key_utilisation"]),
        "overdue": len(data["overdue_list"]),
        "guard_activity": len(data["guard_activity"]),
        "room_usage": len(data["room_usage"]),
        "borrower_frequency": len(data["borrower_frequency"]),
    }

    start_date = date_filter["start_date_str"] or start_date_param
    end_date = date_filter["end_date_str"] or end_date_param
    # Tab links keep the date range, so switching tabs never drops the filter.
    carry = {k: v for k, v in (("start_date", start_date), ("end_date", end_date)) if v}
    index_url = reverse("reports:index")
    tabs = [{
        "key": key,
        "label": label,
        "icon": icon,
        "count": counts.get(key),
        "active": key == active_section,
        "url": f"{index_url}?{urlencode({'section': key, **carry})}",
    } for key, label, icon in TABS]

    context = {
        "title": "Reports",
        "subtitle": "University key utilisation, overdue tracking, guard audit, and borrower analytics.",
        "start_date": start_date,
        "end_date": end_date,
        "is_filtered": date_filter["is_filtered"],
        "date_error": date_filter["error"],
        "active_section": active_section,
        "tabs": tabs,
        "summary": data["summary"],
        "key_utilisation": data["key_utilisation"],
        "overdue_list": data["overdue_list"],
        "guard_activity": data["guard_activity"],
        "room_usage": data["room_usage"],
        "borrower_frequency": data["borrower_frequency"],
        "key_count": counts["key_utilisation"],
        "overdue_count": counts["overdue"],
        "guard_count": counts["guard_activity"],
        "room_count": counts["room_usage"],
        "borrower_count": counts["borrower_frequency"],
        # PDF for the open tab ("all" on Overview) and for the whole suite.
        "export_type": "all" if active_section == "overview" else active_section,
        "export_query": urlencode(carry),
    }

    return render(request, "reports/index.html", context)


@role_required("ADMIN")
def report_export(request, report_type):
    """
    PDF export endpoint, one report or the whole suite:
      /reports/export/key_utilisation/?start_date=2026-10-01&end_date=2026-10-20
      /reports/export/all/
    PDF is the only format; asking for another one is a 400, not a silent swap.
    """
    if report_type not in VALID_EXPORTS:
        raise Http404(f"Report type '{report_type}' does not exist.")

    export_format = request.GET.get("format", "pdf").lower().strip()
    if export_format != "pdf":
        return HttpResponseBadRequest("Reports are exported as PDF only.")

    date_filter = parse_date_range(request.GET.get("start_date", ""),
                                   request.GET.get("end_date", ""))
    if date_filter["error"]:
        return HttpResponseBadRequest(date_filter["error"])

    return export_report(
        report_type,
        start_dt=date_filter["start_dt"],
        end_dt=date_filter["end_dt"],
        start_date_str=date_filter["start_date_str"],
        end_date_str=date_filter["end_date_str"],
    )
