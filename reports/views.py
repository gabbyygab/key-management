from django.contrib import messages
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import render

from accounts.decorators import role_required
from .services import (
    export_report,
    export_report_to_csv,
    export_report_to_excel,
    export_report_to_pdf,
    get_borrower_frequency_report,
    get_guard_activity_report,
    get_key_utilisation_report,
    get_overdue_report,
    get_room_usage_report,
    get_summary_metrics,
    parse_date_range,
)


@role_required("ADMIN")
def reports_index(request):
    """
    Main Reports Page for Key Management System.
    Accessible only to administrators (and superusers).
    Supports:
    - Date range filtering (start_date, end_date)
    - 5 report sections:
        1. Key Utilisation
        2. Overdue Report
        3. Per-Guard Activity
        4. Per-Room Usage
        5. Borrower Frequency
    - High-level summary metric cards
    - Exporting in Excel (.xlsx), PDF (.pdf), and CSV formats
    """
    start_date_param = request.GET.get("start_date", "")
    end_date_param = request.GET.get("end_date", "")
    active_section = request.GET.get("section", "all")
    export_param = request.GET.get("export", "").strip()

    date_filter = parse_date_range(start_date_param, end_date_param)

    if date_filter["error"]:
        messages.error(request, date_filter["error"])

    start_dt = date_filter["start_dt"]
    end_dt = date_filter["end_dt"]

    # If export requested via query parameter (e.g. ?export=key_utilisation&format=xlsx)
    if export_param:
        valid_exports = [
            "key_utilisation",
            "overdue",
            "guard_activity",
            "room_usage",
            "borrower_frequency",
            "all",
            "summary",
        ]
        if export_param in valid_exports:
            export_format = request.GET.get("format", "csv").lower().strip()
            return export_report(
                report_type=export_param,
                export_format=export_format,
                start_dt=start_dt,
                end_dt=end_dt,
                start_date_str=date_filter["start_date_str"],
                end_date_str=date_filter["end_date_str"],
            )
        return HttpResponseBadRequest(f"Invalid export type: {export_param}")

    # Fetch data for all reports
    summary = get_summary_metrics(start_dt, end_dt)
    key_utilisation = get_key_utilisation_report(start_dt, end_dt)
    overdue_list = get_overdue_report(start_dt, end_dt)
    guard_activity = get_guard_activity_report(start_dt, end_dt)
    room_usage = get_room_usage_report(start_dt, end_dt)
    borrower_frequency = get_borrower_frequency_report(start_dt, end_dt)

    context = {
        "title": "Reports",
        "subtitle": "University key utilisation, overdue tracking, guard audit, and borrower analytics.",
        "start_date": date_filter["start_date_str"] or start_date_param,
        "end_date": date_filter["end_date_str"] or end_date_param,
        "is_filtered": date_filter["is_filtered"],
        "date_error": date_filter["error"],
        "active_section": active_section,
        # Metric cards
        "summary": summary,
        # Reports
        "key_utilisation": key_utilisation,
        "overdue_list": overdue_list,
        "guard_activity": guard_activity,
        "room_usage": room_usage,
        "borrower_frequency": borrower_frequency,
        # Counts for tabs
        "key_count": len(key_utilisation),
        "overdue_count": len(overdue_list),
        "guard_count": len(guard_activity),
        "room_count": len(room_usage),
        "borrower_count": len(borrower_frequency),
    }

    return render(request, "reports/index.html", context)


@role_required("ADMIN")
def report_export(request, report_type, export_format=None):
    """
    Dedicated report export endpoint supporting Excel (.xlsx), PDF (.pdf), and CSV.
    Example usages:
      /reports/export/key_utilisation/?format=xlsx&start_date=2026-10-01&end_date=2026-10-20
      /reports/export/key_utilisation/pdf/?start_date=2026-10-01
      /reports/export/all/?format=xlsx
    """
    valid_exports = [
        "key_utilisation",
        "overdue",
        "guard_activity",
        "room_usage",
        "borrower_frequency",
        "all",
        "summary",
    ]
    if report_type not in valid_exports:
        raise Http404(f"Report type '{report_type}' does not exist.")

    format_param = export_format or request.GET.get("format", "csv").lower().strip()
    start_date_param = request.GET.get("start_date", "")
    end_date_param = request.GET.get("end_date", "")

    date_filter = parse_date_range(start_date_param, end_date_param)

    return export_report(
        report_type=report_type,
        export_format=format_param,
        start_dt=date_filter["start_dt"],
        end_dt=date_filter["end_dt"],
        start_date_str=date_filter["start_date_str"],
        end_date_str=date_filter["end_date_str"],
    )

