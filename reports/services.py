import csv
import io
from datetime import datetime, time
from django.db.models import Count, Max, Q
from django.http import HttpResponse
from django.utils import timezone

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import HRFlowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from accounts.models import Borrower, User
from facilities.models import Room
from keys.models import Key
from transactions.models import BorrowTransaction, GuardShift


def parse_date_range(start_date_str, end_date_str):
    """
    Parses and validates optional start_date and end_date strings (YYYY-MM-DD).
    Returns a dict containing:
      - 'start_dt': timezone-aware datetime at 00:00:00 or None
      - 'end_dt': timezone-aware datetime at 23:59:59.999999 or None
      - 'start_date_str': normalized string or empty string
      - 'end_date_str': normalized string or empty string
      - 'error': error message string if invalid, else None
      - 'is_filtered': boolean indicating if a date filter is actively applied
    """
    start_date_str = (start_date_str or "").strip()
    end_date_str = (end_date_str or "").strip()
    tz = timezone.get_current_timezone()

    parsed_start = None
    parsed_end = None
    error = None

    if start_date_str:
        try:
            parsed_start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        except ValueError:
            error = f"Invalid start date '{start_date_str}'. Please use YYYY-MM-DD format."

    if end_date_str and not error:
        try:
            parsed_end = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        except ValueError:
            error = f"Invalid end date '{end_date_str}'. Please use YYYY-MM-DD format."

    if parsed_start and parsed_end and not error:
        if parsed_start > parsed_end:
            error = "Start date cannot be later than end date."

    if error:
        return {
            "start_dt": None,
            "end_dt": None,
            "start_date_str": start_date_str,
            "end_date_str": end_date_str,
            "error": error,
            "is_filtered": False,
        }

    start_dt = None
    end_dt = None

    if parsed_start:
        start_naive = datetime.combine(parsed_start, time.min)
        start_dt = timezone.make_aware(start_naive, tz)

    if parsed_end:
        end_naive = datetime.combine(parsed_end, time.max)
        end_dt = timezone.make_aware(end_naive, tz)

    return {
        "start_dt": start_dt,
        "end_dt": end_dt,
        "start_date_str": parsed_start.isoformat() if parsed_start else "",
        "end_date_str": parsed_end.isoformat() if parsed_end else "",
        "error": None,
        "is_filtered": bool(parsed_start or parsed_end),
    }


def get_summary_metrics(start_dt=None, end_dt=None):
    """
    Computes top-level summary metrics across the system respecting the date range.
    """
    txn_filter = Q()
    if start_dt:
        txn_filter &= Q(borrowed_at__gte=start_dt)
    if end_dt:
        txn_filter &= Q(borrowed_at__lte=end_dt)

    filtered_txns = BorrowTransaction.objects.filter(txn_filter)
    total_borrowings = filtered_txns.count()

    # Active borrows
    currently_borrowed = filtered_txns.filter(status=BorrowTransaction.Status.ACTIVE).count()

    # Overdue count
    now = timezone.now()
    overdue_q = Q(status=BorrowTransaction.Status.OVERDUE) | (
        Q(status=BorrowTransaction.Status.ACTIVE)
        & Q(expected_return_at__isnull=False)
        & Q(expected_return_at__lt=now)
    )
    overdue_count = (
        filtered_txns.filter(overdue_q)
        .exclude(status=BorrowTransaction.Status.RETURNED)
        .filter(returned_at__isnull=True)
        .count()
    )

    # Returned count in period
    returned_count = filtered_txns.filter(status=BorrowTransaction.Status.RETURNED).count()

    # Active borrowers in period
    active_borrowers = filtered_txns.values("borrower").distinct().count()

    # Active guards involved in releasing or receiving in period
    released_guards = filtered_txns.values_list("released_by", flat=True).distinct()
    recv_filter = Q(returned_at__isnull=False)
    if start_dt:
        recv_filter &= Q(returned_at__gte=start_dt)
    if end_dt:
        recv_filter &= Q(returned_at__lte=end_dt)
    received_guards = (
        BorrowTransaction.objects.filter(recv_filter)
        .values_list("received_by", flat=True)
        .distinct()
    )
    active_guards = len(set(filter(None, list(released_guards) + list(received_guards))))

    return {
        "total_borrowings": total_borrowings,
        "currently_borrowed": currently_borrowed,
        "overdue_count": overdue_count,
        "returned_count": returned_count,
        "active_borrowers": active_borrowers,
        "active_guards": active_guards,
    }


def get_key_utilisation_report(start_dt=None, end_dt=None):
    """
    Calculates key utilisation:
    - Key identifier
    - Total borrowings in date range
    - Currently borrowed count
    - Returned count in date range
    - Most recent borrowing date
    Sorted by total borrowings descending to identify frequent keys.
    """
    txn_date_q = Q()
    if start_dt:
        txn_date_q &= Q(transactions__borrowed_at__gte=start_dt)
    if end_dt:
        txn_date_q &= Q(transactions__borrowed_at__lte=end_dt)

    returned_q = txn_date_q & Q(transactions__status=BorrowTransaction.Status.RETURNED)
    active_q = Q(transactions__status=BorrowTransaction.Status.ACTIVE)

    keys = (
        Key.objects.select_related("room__building", "category")
        .annotate(
            total_borrowings=Count("transactions", filter=txn_date_q),
            currently_borrowed=Count("transactions", filter=active_q),
            returned_count=Count("transactions", filter=returned_q),
            last_borrowed_at=Max("transactions__borrowed_at", filter=txn_date_q),
        )
        .order_by("-total_borrowings", "key_code")
    )

    results = []
    max_borrowings = 0
    for k in keys:
        if k.total_borrowings > max_borrowings:
            max_borrowings = k.total_borrowings

    for k in keys:
        pct = round((k.total_borrowings / max_borrowings * 100)) if max_borrowings > 0 else 0
        results.append({
            "key": k,
            "key_code": k.key_code,
            "room_display": f"{k.room.building.code}-{k.room.room_code}" if k.room else "—",
            "building_code": k.room.building.code if k.room and k.room.building else "—",
            "category_name": k.category.name if k.category else "—",
            "total_borrowings": k.total_borrowings,
            "currently_borrowed": k.currently_borrowed,
            "returned_count": k.returned_count,
            "status": k.status,
            "last_borrowed_at": k.last_borrowed_at,
            "utilization_pct": pct,
        })

    return results


def format_duration(td):
    """Formats a timedelta into a concise human-readable duration."""
    if not td or td.total_seconds() < 0:
        return "0m"
    total_seconds = int(td.total_seconds())
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 and days == 0:
        parts.append(f"{minutes}m")
    return " ".join(parts) if parts else "< 1m"


def get_overdue_report(start_dt=None, end_dt=None):
    """
    Calculates overdue keys:
    - Never classifies a returned key as overdue.
    - Handles missing expected return dates safely.
    - Computes overdue duration against current time.
    """
    now = timezone.now()

    # Must not be returned
    base_q = Q(returned_at__isnull=True) & ~Q(status=BorrowTransaction.Status.RETURNED)

    # Overdue rule: status == OVERDUE or (ACTIVE with expected_return_at < now)
    overdue_rule = Q(status=BorrowTransaction.Status.OVERDUE) | (
        Q(status=BorrowTransaction.Status.ACTIVE)
        & Q(expected_return_at__isnull=False)
        & Q(expected_return_at__lt=now)
    )

    date_q = Q()
    if start_dt:
        date_q &= Q(borrowed_at__gte=start_dt)
    if end_dt:
        date_q &= Q(borrowed_at__lte=end_dt)

    overdue_txns = (
        BorrowTransaction.objects.select_related(
            "key__room__building", "borrower__department", "released_by"
        )
        .filter(base_q & overdue_rule & date_q)
        .order_by("expected_return_at", "-borrowed_at")
    )

    results = []
    for t in overdue_txns:
        overdue_duration_str = "—"
        duration_seconds = 0
        if t.expected_return_at and t.expected_return_at < now:
            diff = now - t.expected_return_at
            duration_seconds = int(diff.total_seconds())
            overdue_duration_str = format_duration(diff) + " overdue"
        elif t.status == BorrowTransaction.Status.OVERDUE:
            overdue_duration_str = "Marked Overdue"

        results.append({
            "transaction_no": t.transaction_no,
            "borrower_name": t.borrower.full_name if t.borrower else "Unknown",
            "borrower_id": t.borrower.id_number if t.borrower else "—",
            "borrower_email": t.borrower.email if t.borrower else "",
            "borrower_phone": t.borrower.contact_number if t.borrower else "",
            "borrower_type": t.borrower.get_borrower_type_display() if t.borrower else "—",
            "key_code": t.key.key_code if t.key else "—",
            "room_display": f"{t.room.building.code}-{t.room.room_code}" if t.room else "—",
            "building_code": t.room.building.code if t.room and t.room.building else "—",
            "borrowed_at": t.borrowed_at,
            "expected_return_at": t.expected_return_at,
            "actual_return_date": t.returned_at,  # None for currently overdue
            "overdue_duration": overdue_duration_str,
            "overdue_seconds": duration_seconds,
            "status": "OVERDUE",
            "released_by": t.released_by.full_name if t.released_by else "—",
        })

    return results


def get_guard_activity_report(start_dt=None, end_dt=None):
    """
    Summarizes key-related activities handled by guards:
    - Guard name and role
    - Number of keys released in period
    - Number of keys returned in period
    - Total transactions handled
    - Most recent activity date
    """
    # Find all users with GUARD role, or who have released/received keys
    guards = (
        User.objects.filter(
            Q(role=User.Role.GUARD)
            | Q(keys_released__isnull=False)
            | Q(keys_received__isnull=False)
        )
        .distinct()
        .order_by("username")
    )

    release_date_q = Q()
    if start_dt:
        release_date_q &= Q(borrowed_at__gte=start_dt)
    if end_dt:
        release_date_q &= Q(borrowed_at__lte=end_dt)

    return_date_q = Q(returned_at__isnull=False)
    if start_dt:
        return_date_q &= Q(returned_at__gte=start_dt)
    if end_dt:
        return_date_q &= Q(returned_at__lte=end_dt)

    results = []
    max_total = 0

    for g in guards:
        released_count = BorrowTransaction.objects.filter(
            Q(released_by=g) & release_date_q
        ).count()

        returned_count = BorrowTransaction.objects.filter(
            Q(received_by=g) & return_date_q
        ).count()

        total = released_count + returned_count

        last_release = (
            BorrowTransaction.objects.filter(Q(released_by=g) & release_date_q)
            .aggregate(m=Max("borrowed_at"))["m"]
        )
        last_return = (
            BorrowTransaction.objects.filter(Q(received_by=g) & return_date_q)
            .aggregate(m=Max("returned_at"))["m"]
        )

        dates = [d for d in [last_release, last_return] if d is not None]
        most_recent = max(dates) if dates else None

        # Shifts in period
        shift_q = Q(guard=g)
        if start_dt:
            shift_q &= Q(shift_start__gte=start_dt)
        if end_dt:
            shift_q &= Q(shift_start__lte=end_dt)
        shifts_count = GuardShift.objects.filter(shift_q).count()

        if total > max_total:
            max_total = total

        results.append({
            "guard": g,
            "guard_name": g.full_name,
            "username": g.username,
            "role": g.get_role_display(),
            "keys_released": released_count,
            "keys_returned": returned_count,
            "total_transactions": total,
            "shifts_count": shifts_count,
            "most_recent_activity": most_recent,
        })

    # Sort guards with highest activity first
    results.sort(key=lambda x: (-x["total_transactions"], -x["keys_released"], x["guard_name"]))

    for r in results:
        r["activity_pct"] = round((r["total_transactions"] / max_total * 100)) if max_total > 0 else 0

    return results


def get_room_usage_report(start_dt=None, end_dt=None):
    """
    Shows key usage grouped by room:
    - Room name or identifier
    - Associated keys
    - Total borrowing transactions in period
    - Currently borrowed count
    - Most recent usage date
    Avoids double-counting transactions by querying per room.
    """
    txn_date_q = Q()
    if start_dt:
        txn_date_q &= Q(transactions__borrowed_at__gte=start_dt)
    if end_dt:
        txn_date_q &= Q(transactions__borrowed_at__lte=end_dt)

    active_q = Q(transactions__status=BorrowTransaction.Status.ACTIVE)

    rooms = (
        Room.objects.select_related("building")
        .prefetch_related("keys")
        .annotate(
            total_borrowings=Count("transactions", filter=txn_date_q),
            currently_borrowed=Count("transactions", filter=active_q),
            last_used_at=Max("transactions__borrowed_at", filter=txn_date_q),
        )
        .order_by("-total_borrowings", "building__code", "room_code")
    )

    results = []
    max_borrowings = 0
    for rm in rooms:
        if rm.total_borrowings > max_borrowings:
            max_borrowings = rm.total_borrowings

    for rm in rooms:
        associated_keys = list(rm.keys.values_list("key_code", flat=True))
        keys_display = ", ".join(associated_keys) if associated_keys else "No keys"
        pct = round((rm.total_borrowings / max_borrowings * 100)) if max_borrowings > 0 else 0

        results.append({
            "room": rm,
            "room_display": f"{rm.building.code}-{rm.room_code}",
            "room_name": rm.name or rm.get_room_type_display(),
            "building_code": rm.building.code,
            "room_type": rm.get_room_type_display(),
            "associated_keys": keys_display,
            "keys_count": len(associated_keys),
            "total_borrowings": rm.total_borrowings,
            "currently_borrowed": rm.currently_borrowed,
            "last_used_at": rm.last_used_at,
            "usage_pct": pct,
        })

    return results


def get_borrower_frequency_report(start_dt=None, end_dt=None):
    """
    Shows how frequently borrowers check out keys:
    - Borrower name, ID, type, department
    - Total borrowings in period
    - Currently active borrowings
    - Completed or returned borrowings
    - Most recent borrowing date
    Sorted by total borrowings descending to identify frequent borrowers.
    """
    txn_date_q = Q()
    if start_dt:
        txn_date_q &= Q(transactions__borrowed_at__gte=start_dt)
    if end_dt:
        txn_date_q &= Q(transactions__borrowed_at__lte=end_dt)

    returned_q = txn_date_q & Q(transactions__status=BorrowTransaction.Status.RETURNED)
    active_q = Q(transactions__status=BorrowTransaction.Status.ACTIVE)

    borrowers = (
        Borrower.objects.select_related("department")
        .annotate(
            total_borrowings=Count("transactions", filter=txn_date_q),
            currently_active=Count("transactions", filter=active_q),
            returned_borrowings=Count("transactions", filter=returned_q),
            last_borrowed_at=Max("transactions__borrowed_at", filter=txn_date_q),
        )
        .order_by("-total_borrowings", "last_name", "first_name")
    )

    results = []
    max_borrowings = 0
    for b in borrowers:
        if b.total_borrowings > max_borrowings:
            max_borrowings = b.total_borrowings

    for b in borrowers:
        pct = round((b.total_borrowings / max_borrowings * 100)) if max_borrowings > 0 else 0
        dept_name = b.department.code if b.department else "—"

        results.append({
            "borrower": b,
            "borrower_name": b.full_name,
            "id_number": b.id_number,
            "borrower_type": b.get_borrower_type_display(),
            "department": dept_name,
            "contact_number": b.contact_number or "—",
            "email": b.email or "—",
            "total_borrowings": b.total_borrowings,
            "currently_active": b.currently_active,
            "returned_borrowings": b.returned_borrowings,
            "last_borrowed_at": b.last_borrowed_at,
            "frequency_pct": pct,
        })

    return results


def sanitize_csv_cell(val):
    """
    Sanitizes a cell value to prevent CSV / Spreadsheet formula injection.
    If the text starts with '=', '+', '-', '@', '\t', or '\r', prefix with single quote "'".
    """
    if val is None:
        return ""
    if isinstance(val, (int, float)):
        return val
    str_val = str(val).strip()
    if str_val and str_val[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{str_val}"
    return str_val


def generate_csv_response(filename, headers, rows):
    """
    Generates a secure, lightweight CSV HttpResponse with standard quoting
    and formula injection prevention.
    """
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response, quoting=csv.QUOTE_MINIMAL)
    writer.writerow([sanitize_csv_cell(h) for h in headers])

    for row in rows:
        writer.writerow([sanitize_csv_cell(item) for item in row])

    return response


class NumberedCanvas(canvas.Canvas):
    """
    Two-pass canvas to calculate total page count and draw
    professional running footers and headers on all pages.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#6B7280"))
        # Top subtle border
        self.setStrokeColor(colors.HexColor("#E5E7EB"))
        self.setLineWidth(0.5)
        self.line(36, 582, 792 - 36, 582)
        # Bottom footer line
        self.line(36, 32, 792 - 36, 32)
        self.drawString(36, 20, "KEYTRACK — University Key Management System | Confidential & Internal Use Only")
        self.drawRightString(792 - 36, 20, f"Page {self._pageNumber} of {page_count}")
        self.restoreState()


def get_date_filter_label(start_date_str, end_date_str):
    """Returns a user-friendly label for the applied date filter range."""
    if start_date_str and end_date_str:
        return f"{start_date_str} to {end_date_str}"
    if start_date_str:
        return f"From {start_date_str}"
    if end_date_str:
        return f"Until {end_date_str}"
    return "All Available Records"


def get_report_data(report_type, start_dt=None, end_dt=None):
    """
    Extracts structured headers, row data, and formatting specs for a specific report.
    """
    if report_type == "key_utilisation":
        headers = [
            "Key Code",
            "Room",
            "Building",
            "Category",
            "Total Borrowings",
            "Currently Borrowed",
            "Returned Count",
            "Current Status",
            "Last Borrowed Date",
        ]
        data = get_key_utilisation_report(start_dt, end_dt)
        rows = [
            [
                item["key_code"],
                item["room_display"],
                item["building_code"],
                item["category_name"],
                item["total_borrowings"],
                item["currently_borrowed"],
                item["returned_count"],
                item["status"],
                item["last_borrowed_at"].strftime("%Y-%m-%d %H:%M") if item["last_borrowed_at"] else "Never",
            ]
            for item in data
        ]
        col_widths = [65, 75, 60, 85, 75, 70, 65, 85, 140]
        return {
            "title": "Key Utilisation Report",
            "base_filename": "key_utilisation_report",
            "headers": headers,
            "rows": rows,
            "col_widths": col_widths,
        }

    elif report_type == "overdue":
        headers = [
            "Transaction No",
            "Borrower Name",
            "Borrower ID",
            "Borrower Type",
            "Contact Number",
            "Email",
            "Key Code",
            "Room",
            "Building",
            "Borrowed Date",
            "Expected Return Date",
            "Actual Return Date",
            "Overdue Duration",
            "Status",
            "Released By Guard",
        ]
        data = get_overdue_report(start_dt, end_dt)
        rows = [
            [
                item["transaction_no"],
                item["borrower_name"],
                item["borrower_id"],
                item["borrower_type"],
                item["borrower_phone"],
                item["borrower_email"],
                item["key_code"],
                item["room_display"],
                item["building_code"],
                item["borrowed_at"].strftime("%Y-%m-%d %H:%M") if item["borrowed_at"] else "—",
                item["expected_return_at"].strftime("%Y-%m-%d %H:%M") if item["expected_return_at"] else "—",
                item["actual_return_date"].strftime("%Y-%m-%d %H:%M") if item["actual_return_date"] else "Not Returned",
                item["overdue_duration"],
                item["status"],
                item["released_by"],
            ]
            for item in data
        ]
        col_widths = [48, 65, 45, 40, 50, 60, 42, 42, 35, 58, 58, 52, 48, 42, 45]
        return {
            "title": "Overdue Keys Report",
            "base_filename": "overdue_report",
            "headers": headers,
            "rows": rows,
            "col_widths": col_widths,
        }

    elif report_type == "guard_activity":
        headers = [
            "Guard Name",
            "Username",
            "Role",
            "Keys Released",
            "Keys Returned",
            "Total Transactions",
            "Shifts Recorded",
            "Most Recent Activity",
        ]
        data = get_guard_activity_report(start_dt, end_dt)
        rows = [
            [
                item["guard_name"],
                item["username"],
                item["role"],
                item["keys_released"],
                item["keys_returned"],
                item["total_transactions"],
                item["shifts_count"],
                item["most_recent_activity"].strftime("%Y-%m-%d %H:%M") if item["most_recent_activity"] else "None",
            ]
            for item in data
        ]
        col_widths = [110, 80, 70, 75, 75, 85, 75, 150]
        return {
            "title": "Per-Guard Activity Report",
            "base_filename": "guard_activity_report",
            "headers": headers,
            "rows": rows,
            "col_widths": col_widths,
        }

    elif report_type == "room_usage":
        headers = [
            "Room",
            "Room Name",
            "Building",
            "Room Type",
            "Associated Keys",
            "Total Borrowings",
            "Currently Borrowed",
            "Last Used Date",
        ]
        data = get_room_usage_report(start_dt, end_dt)
        rows = [
            [
                item["room_display"],
                item["room_name"],
                item["building_code"],
                item["room_type"],
                item["associated_keys"],
                item["total_borrowings"],
                item["currently_borrowed"],
                item["last_used_at"].strftime("%Y-%m-%d %H:%M") if item["last_used_at"] else "Never",
            ]
            for item in data
        ]
        col_widths = [70, 110, 70, 80, 80, 80, 80, 150]
        return {
            "title": "Per-Room Usage Report",
            "base_filename": "room_usage_report",
            "headers": headers,
            "rows": rows,
            "col_widths": col_widths,
        }

    elif report_type == "borrower_frequency":
        headers = [
            "Borrower Name",
            "ID Number",
            "Type",
            "Department",
            "Contact Number",
            "Email",
            "Total Borrowings",
            "Currently Active",
            "Returned Borrowings",
            "Last Borrowed Date",
        ]
        data = get_borrower_frequency_report(start_dt, end_dt)
        rows = [
            [
                item["borrower_name"],
                item["id_number"],
                item["borrower_type"],
                item["department"],
                item["contact_number"],
                item["email"],
                item["total_borrowings"],
                item["currently_active"],
                item["returned_borrowings"],
                item["last_borrowed_at"].strftime("%Y-%m-%d %H:%M") if item["last_borrowed_at"] else "Never",
            ]
            for item in data
        ]
        col_widths = [100, 65, 55, 85, 75, 95, 60, 55, 60, 70]
        return {
            "title": "Borrower Frequency Report",
            "base_filename": "borrower_frequency_report",
            "headers": headers,
            "rows": rows,
            "col_widths": col_widths,
        }

    else:
        raise ValueError(f"Unknown report type: {report_type}")


def style_excel_worksheet(ws, report_title, headers, rows, date_filter_label="All Available Records", subtitle=None):
    """
    Applies an executive-grade design system to an openpyxl worksheet:
    - Corporate Forest Green / Emerald branding
    - Formatted title & metadata header
    - Formatted column headers with freeze pane
    - Zebra striped data rows with soft borders
    - Status pill color badges
    - Auto-sized columns
    - Safe formula injection prevention
    """
    ws.views.sheetView[0].showGridLines = True
    num_cols = len(headers)
    last_col_letter = get_column_letter(num_cols)

    brand_dark = "14342A"
    brand_green = "1E6B4A"
    zebra_bg = "F8FAF9"
    border_gray = "E5E7EB"

    # Row 1: Brand Banner
    ws.merge_cells(f"A1:{last_col_letter}1")
    title_cell = ws["A1"]
    title_cell.value = "KEYTRACK — UNIVERSITY KEY MANAGEMENT SYSTEM"
    title_cell.font = Font(name="Calibri", size=13, bold=True, color="FFFFFF")
    title_cell.fill = PatternFill(start_color=brand_dark, end_color=brand_dark, fill_type="solid")
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    # Row 2: Subtitle Banner
    ws.merge_cells(f"A2:{last_col_letter}2")
    sub_cell = ws["A2"]
    sub_cell.value = (subtitle or report_title).upper()
    sub_cell.font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    sub_cell.fill = PatternFill(start_color=brand_green, end_color=brand_green, fill_type="solid")
    sub_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 22

    # Row 3: Blank spacer
    ws.row_dimensions[3].height = 8

    # Row 4 & 5: Metadata Box
    now_str = timezone.now().strftime("%Y-%m-%d %I:%M %p")
    meta_font_bold = Font(name="Calibri", size=9.5, bold=True, color="4B5563")
    meta_font_val = Font(name="Calibri", size=9.5, color="1F2937")

    ws["A4"].value = "Date Filter Range:"
    ws["A4"].font = meta_font_bold
    ws["B4"].value = date_filter_label
    ws["B4"].font = meta_font_val

    if num_cols >= 4:
        ws["C4"].value = "Total Records:"
        ws["C4"].font = meta_font_bold
        ws["D4"].value = len(rows)
        ws["D4"].font = meta_font_val
    else:
        ws["A5"].value = "Total Records:"
        ws["A5"].font = meta_font_bold
        ws["B5"].value = len(rows)
        ws["B5"].font = meta_font_val

    ws["A5"].value = "Generated On:"
    ws["A5"].font = meta_font_bold
    ws["B5"].value = now_str
    ws["B5"].font = meta_font_val

    # Row 6: Blank spacer
    ws.row_dimensions[6].height = 8

    # Row 7: Table Headers
    header_font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color=brand_green, end_color=brand_green, fill_type="solid")
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin_border_side = Side(style="thin", color=border_gray)
    cell_border = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)

    ws.row_dimensions[7].height = 26
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=7, column=col_idx)
        cell.value = header
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = cell_border

    # Status badge configurations
    status_colors = {
        "AVAILABLE": {"fill": "DCFCE7", "font": "166534"},
        "OVERDUE": {"fill": "FEE2E2", "font": "991B1B"},
        "BORROWED": {"fill": "DBEAFE", "font": "1E40AF"},
        "ACTIVE": {"fill": "DBEAFE", "font": "1E40AF"},
        "RETURNED": {"fill": "F3F4F6", "font": "374151"},
        "MAINTENANCE": {"fill": "FEF3C7", "font": "92400E"},
        "LOST": {"fill": "FEE2E2", "font": "991B1B"},
    }

    # Data Rows
    row_font = Font(name="Calibri", size=10, color="1F2937")
    if rows:
        for r_idx, row in enumerate(rows, start=8):
            ws.row_dimensions[r_idx].height = 20
            is_even = (r_idx % 2 == 0)
            default_fill = PatternFill(start_color=zebra_bg if is_even else "FFFFFF",
                                       end_color=zebra_bg if is_even else "FFFFFF",
                                       fill_type="solid")

            for c_idx, val in enumerate(row, start=1):
                cell = ws.cell(row=r_idx, column=c_idx)
                clean_val = sanitize_csv_cell(val)
                cell.value = clean_val
                cell.border = cell_border
                cell.fill = default_fill
                cell.font = row_font

                val_str = str(val).strip().upper() if val is not None else ""
                if val_str in status_colors:
                    cfg = status_colors[val_str]
                    cell.fill = PatternFill(start_color=cfg["fill"], end_color=cfg["fill"], fill_type="solid")
                    cell.font = Font(name="Calibri", size=9.5, bold=True, color=cfg["font"])
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                elif isinstance(val, (int, float)):
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                elif any(k in headers[c_idx - 1].lower() for k in ["date", "time", "status", "no", "code", "id", "phone"]):
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                else:
                    cell.alignment = Alignment(horizontal="left", vertical="center")
    else:
        ws.merge_cells(f"A8:{last_col_letter}8")
        empty_cell = ws["A8"]
        empty_cell.value = "No records found for the selected period."
        empty_cell.font = Font(name="Calibri", size=10, italic=True, color="6B7280")
        empty_cell.alignment = Alignment(horizontal="center", vertical="center")
        empty_cell.border = cell_border
        ws.row_dimensions[8].height = 24

    # Freeze panes at header row
    ws.freeze_panes = "A8"

    # Column Auto-Adjustment
    for col_idx in range(1, num_cols + 1):
        col_letter = get_column_letter(col_idx)
        max_len = len(str(headers[col_idx - 1]))
        for row_idx in range(8, len(rows) + 8):
            cell_val = ws.cell(row=row_idx, column=col_idx).value
            if cell_val is not None:
                max_len = max(max_len, len(str(cell_val)))
        ws.column_dimensions[col_letter].width = max(13, min(max_len + 4, 45))


def generate_excel_response(filename, report_title, headers, rows, date_filter_label="All Available Records"):
    """Creates a downloadable .xlsx response with the styled single-sheet report."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = report_title.replace(" Report", "")[:31]
    style_excel_worksheet(ws, report_title, headers, rows, date_filter_label=date_filter_label)

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


def generate_pdf_response(filename, report_title, headers, rows, date_filter_label="All Available Records", col_widths=None):
    """Creates a downloadable .pdf response with a landscape executive-grade report layout."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(letter),
        leftMargin=36,
        rightMargin=36,
        topMargin=40,
        bottomMargin=40,
    )
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=18,
        textColor=colors.HexColor("#14342A"),
    )
    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#1E6B4A"),
    )
    meta_style = ParagraphStyle(
        "DocMeta",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#4B5563"),
    )
    th_style = ParagraphStyle(
        "TH",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=9,
        textColor=colors.white,
        alignment=1,
    )
    td_style = ParagraphStyle(
        "TD",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7,
        leading=8.5,
        textColor=colors.HexColor("#1F2937"),
    )
    td_center_style = ParagraphStyle(
        "TDCenter",
        parent=td_style,
        alignment=1,
    )

    now_str = timezone.now().strftime("%Y-%m-%d %I:%M %p")
    story = [
        Paragraph("KEYTRACK — UNIVERSITY KEY MANAGEMENT SYSTEM", title_style),
        Spacer(1, 3),
        Paragraph(report_title.upper(), subtitle_style),
        Spacer(1, 3),
        Paragraph(f"<b>Date Filter:</b> {date_filter_label} &nbsp;&nbsp;|&nbsp;&nbsp; <b>Generated:</b> {now_str} &nbsp;&nbsp;|&nbsp;&nbsp; <b>Total Records:</b> {len(rows)}", meta_style),
        Spacer(1, 6),
        HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#1E6B4A"), spaceBefore=2, spaceAfter=8),
    ]

    num_cols = len(headers)
    if not col_widths or len(col_widths) != num_cols:
        col_widths = [720.0 / num_cols] * num_cols

    table_data = [[Paragraph(f"<b>{h}</b>", th_style) for h in headers]]
    if rows:
        for r_idx, row in enumerate(rows):
            row_cells = []
            for c_idx, val in enumerate(row):
                val_str = str(val) if val is not None else ""
                h_lower = headers[c_idx].lower()
                if any(k in h_lower for k in ["status", "date", "no", "code", "id", "count", "total", "phone"]):
                    p = Paragraph(val_str, td_center_style)
                else:
                    p = Paragraph(val_str, td_style)
                row_cells.append(p)
            table_data.append(row_cells)
    else:
        empty_p = Paragraph("<i>No records found for the selected period.</i>", td_center_style)
        table_data.append([empty_p] + [""] * (num_cols - 1))

    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    t_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E6B4A")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]
    if not rows:
        t_style.append(("SPAN", (0, 1), (-1, 1)))
    else:
        for r in range(1, len(table_data)):
            if r % 2 == 0:
                t_style.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#F8FAF9")))

    t.setStyle(TableStyle(t_style))
    story.append(t)

    doc.build(story, canvasmaker=NumberedCanvas)

    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write(buf.getvalue())
    return response


def export_all_reports_to_excel(start_dt=None, end_dt=None, start_date_str="", end_date_str=""):
    """
    Exports a comprehensive multi-tab Excel workbook containing:
    1. Executive Summary tab with top-level metric indicators
    2. Key Utilisation tab
    3. Overdue Keys tab
    4. Guard Activity tab
    5. Room Usage tab
    6. Borrower Frequency tab
    """
    date_suffix = f"_{start_date_str}_to_{end_date_str}" if (start_date_str and end_date_str) else (
        f"_from_{start_date_str}" if start_date_str else (
            f"_until_{end_date_str}" if end_date_str else "_all_time"
        )
    )
    date_filter_label = get_date_filter_label(start_date_str, end_date_str)
    filename = f"keytrack_complete_reports{date_suffix}.xlsx"

    wb = openpyxl.Workbook()

    # 1. Executive Summary Sheet
    ws_summary = wb.active
    ws_summary.title = "Executive Summary"
    ws_summary.views.sheetView[0].showGridLines = True

    summary_metrics = get_summary_metrics(start_dt, end_dt)
    brand_dark = "14342A"
    brand_green = "1E6B4A"
    border_gray = "E5E7EB"
    thin_border_side = Side(style="thin", color=border_gray)
    cell_border = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)

    ws_summary.merge_cells("A1:C1")
    ws_summary["A1"].value = "KEYTRACK — UNIVERSITY KEY MANAGEMENT SYSTEM"
    ws_summary["A1"].font = Font(name="Calibri", size=13, bold=True, color="FFFFFF")
    ws_summary["A1"].fill = PatternFill(start_color=brand_dark, end_color=brand_dark, fill_type="solid")
    ws_summary["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws_summary.row_dimensions[1].height = 28

    ws_summary.merge_cells("A2:C2")
    ws_summary["A2"].value = "EXECUTIVE SUMMARY & OPERATIONAL METRICS"
    ws_summary["A2"].font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    ws_summary["A2"].fill = PatternFill(start_color=brand_green, end_color=brand_green, fill_type="solid")
    ws_summary["A2"].alignment = Alignment(horizontal="center", vertical="center")
    ws_summary.row_dimensions[2].height = 22

    now_str = timezone.now().strftime("%Y-%m-%d %I:%M %p")
    ws_summary["A4"].value = "Date Filter Range:"
    ws_summary["A4"].font = Font(name="Calibri", size=9.5, bold=True, color="4B5563")
    ws_summary["B4"].value = date_filter_label
    ws_summary["B4"].font = Font(name="Calibri", size=9.5, color="1F2937")

    ws_summary["A5"].value = "Report Generated:"
    ws_summary["A5"].font = Font(name="Calibri", size=9.5, bold=True, color="4B5563")
    ws_summary["B5"].value = now_str
    ws_summary["B5"].font = Font(name="Calibri", size=9.5, color="1F2937")

    metric_headers = ["Key Metric Indicator", "Count / Value", "Operational Meaning"]
    for c_i, h in enumerate(metric_headers, start=1):
        c = ws_summary.cell(row=7, column=c_i)
        c.value = h
        c.font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
        c.fill = PatternFill(start_color=brand_green, end_color=brand_green, fill_type="solid")
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = cell_border
    ws_summary.row_dimensions[7].height = 24

    metrics_rows = [
        ("Total Key Borrowings", summary_metrics["total_borrowings"], "Total checkout transactions recorded in this period"),
        ("Currently Out (Active Loans)", summary_metrics["currently_borrowed"], "Keys currently checked out and in custody of borrowers"),
        ("Overdue Keys", summary_metrics["overdue_count"], "Keys past their expected return time requiring guard follow-up"),
        ("Completed Returns", summary_metrics["returned_count"], "Key returns successfully logged and received by guards"),
        ("Active Borrowers", summary_metrics["active_borrowers"], "Distinct professors, staff, and students who transacted"),
        ("Guards on Duty / Logged Activity", summary_metrics["active_guards"], "Guard personnel with shift transactions recorded"),
    ]

    for r_i, (label, val, desc) in enumerate(metrics_rows, start=8):
        ws_summary.row_dimensions[r_i].height = 22
        is_even = (r_i % 2 == 0)
        bg = "F8FAF9" if is_even else "FFFFFF"
        row_fill = PatternFill(start_color=bg, end_color=bg, fill_type="solid")

        c1 = ws_summary.cell(row=r_i, column=1, value=label)
        c1.font = Font(name="Calibri", size=10, bold=True, color="1F2937")
        c1.fill = row_fill
        c1.border = cell_border

        c2 = ws_summary.cell(row=r_i, column=2, value=val)
        c2.font = Font(name="Calibri", size=11, bold=True, color="1E6B4A" if label != "Overdue Keys" or val == 0 else "991B1B")
        c2.alignment = Alignment(horizontal="center", vertical="center")
        c2.fill = row_fill
        c2.border = cell_border

        c3 = ws_summary.cell(row=r_i, column=3, value=desc)
        c3.font = Font(name="Calibri", size=9.5, italic=True, color="6B7280")
        c3.fill = row_fill
        c3.border = cell_border

    ws_summary.column_dimensions["A"].width = 34
    ws_summary.column_dimensions["B"].width = 18
    ws_summary.column_dimensions["C"].width = 65

    # 2. Add each of the 5 report sheets
    report_types = [
        ("key_utilisation", "Key Utilisation"),
        ("overdue", "Overdue Keys"),
        ("guard_activity", "Guard Activity"),
        ("room_usage", "Room Usage"),
        ("borrower_frequency", "Borrower Frequency"),
    ]
    for r_type, sheet_name in report_types:
        spec = get_report_data(r_type, start_dt, end_dt)
        ws_sheet = wb.create_sheet(title=sheet_name)
        style_excel_worksheet(ws_sheet, spec["title"], spec["headers"], spec["rows"], date_filter_label=date_filter_label)

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


def export_all_reports_to_pdf(start_dt=None, end_dt=None, start_date_str="", end_date_str=""):
    """
    Exports a comprehensive multi-page executive PDF report covering all 5 operational reports.
    """
    date_suffix = f"_{start_date_str}_to_{end_date_str}" if (start_date_str and end_date_str) else (
        f"_from_{start_date_str}" if start_date_str else (
            f"_until_{end_date_str}" if end_date_str else "_all_time"
        )
    )
    date_filter_label = get_date_filter_label(start_date_str, end_date_str)
    filename = f"keytrack_complete_reports{date_suffix}.pdf"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(letter),
        leftMargin=36,
        rightMargin=36,
        topMargin=40,
        bottomMargin=40,
    )
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=18,
        textColor=colors.HexColor("#14342A"),
    )
    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#1E6B4A"),
    )
    meta_style = ParagraphStyle(
        "DocMeta",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#4B5563"),
    )
    th_style = ParagraphStyle(
        "TH",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=9,
        textColor=colors.white,
        alignment=1,
    )
    td_style = ParagraphStyle(
        "TD",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7,
        leading=8.5,
        textColor=colors.HexColor("#1F2937"),
    )
    td_center_style = ParagraphStyle(
        "TDCenter",
        parent=td_style,
        alignment=1,
    )

    now_str = timezone.now().strftime("%Y-%m-%d %I:%M %p")
    story = [
        Paragraph("KEYTRACK — UNIVERSITY KEY MANAGEMENT SYSTEM", title_style),
        Spacer(1, 3),
        Paragraph("EXECUTIVE AUDIT & OPERATIONS REPORT SUITE", subtitle_style),
        Spacer(1, 3),
        Paragraph(f"<b>Date Filter:</b> {date_filter_label} &nbsp;&nbsp;|&nbsp;&nbsp; <b>Generated:</b> {now_str}", meta_style),
        Spacer(1, 6),
        HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#1E6B4A"), spaceBefore=2, spaceAfter=8),
    ]

    # Executive Overview
    summary_metrics = get_summary_metrics(start_dt, end_dt)
    overview_headers = [Paragraph("<b>Operational Metric</b>", th_style), Paragraph("<b>Count / Value</b>", th_style), Paragraph("<b>Description</b>", th_style)]
    overview_rows = [
        overview_headers,
        [Paragraph("Total Key Borrowings", td_style), Paragraph(str(summary_metrics["total_borrowings"]), td_center_style), Paragraph("Total checkout transactions recorded in this period", td_style)],
        [Paragraph("Currently Out", td_style), Paragraph(str(summary_metrics["currently_borrowed"]), td_center_style), Paragraph("Active unreturned keys in custody of borrowers", td_style)],
        [Paragraph("Overdue Keys", td_style), Paragraph(str(summary_metrics["overdue_count"]), td_center_style), Paragraph("Unreturned keys past their expected return deadline", td_style)],
        [Paragraph("Completed Returns", td_style), Paragraph(str(summary_metrics["returned_count"]), td_center_style), Paragraph("Keys successfully checked back in by security guards", td_style)],
        [Paragraph("Active Borrowers", td_style), Paragraph(str(summary_metrics["active_borrowers"]), td_center_style), Paragraph("Professors, staff, and students transacting", td_style)],
        [Paragraph("Guards on Duty", td_style), Paragraph(str(summary_metrics["active_guards"]), td_center_style), Paragraph("Guards actively processing key releases and returns", td_style)],
    ]
    overview_table = Table(overview_rows, colWidths=[200, 100, 420], repeatRows=1)
    overview_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E6B4A")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(overview_table)
    story.append(Spacer(1, 15))

    report_list = [
        ("key_utilisation", "1. Key Utilisation Report"),
        ("overdue", "2. Overdue Keys Report"),
        ("guard_activity", "3. Per-Guard Activity Report"),
        ("room_usage", "4. Per-Room Usage Report"),
        ("borrower_frequency", "5. Borrower Frequency Report"),
    ]

    for idx, (r_type, sec_title) in enumerate(report_list):
        story.append(PageBreak())
        spec = get_report_data(r_type, start_dt, end_dt)

        story.append(Paragraph(sec_title.upper(), subtitle_style))
        story.append(Spacer(1, 2))
        story.append(Paragraph(f"<b>Records:</b> {len(spec['rows'])} &nbsp;&nbsp;|&nbsp;&nbsp; <b>Date Range:</b> {date_filter_label}", meta_style))
        story.append(Spacer(1, 6))

        num_cols = len(spec["headers"])
        col_w = spec.get("col_widths") or [720.0 / num_cols] * num_cols

        table_data = [[Paragraph(f"<b>{h}</b>", th_style) for h in spec["headers"]]]
        if spec["rows"]:
            for r_idx, row in enumerate(spec["rows"]):
                row_cells = []
                for c_idx, val in enumerate(row):
                    val_str = str(val) if val is not None else ""
                    h_lower = spec["headers"][c_idx].lower()
                    if any(k in h_lower for k in ["status", "date", "no", "code", "id", "count", "total", "phone"]):
                        p = Paragraph(val_str, td_center_style)
                    else:
                        p = Paragraph(val_str, td_style)
                    row_cells.append(p)
                table_data.append(row_cells)
        else:
            table_data.append([Paragraph("<i>No records found for the selected period.</i>", td_center_style)] + [""] * (num_cols - 1))

        t = Table(table_data, colWidths=col_w, repeatRows=1)
        t_style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E6B4A")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ]
        if not spec["rows"]:
            t_style.append(("SPAN", (0, 1), (-1, 1)))
        else:
            for r in range(1, len(table_data)):
                if r % 2 == 0:
                    t_style.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#F8FAF9")))

        t.setStyle(TableStyle(t_style))
        story.append(t)

    doc.build(story, canvasmaker=NumberedCanvas)

    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write(buf.getvalue())
    return response


def export_report_to_csv(report_type, start_dt=None, end_dt=None, start_date_str="", end_date_str=""):
    """
    Handles CSV export generation for report types:
    - key_utilisation
    - overdue
    - guard_activity
    - room_usage
    - borrower_frequency
    """
    date_suffix = f"_{start_date_str}_to_{end_date_str}" if (start_date_str and end_date_str) else (
        f"_from_{start_date_str}" if start_date_str else (
            f"_until_{end_date_str}" if end_date_str else "_all_time"
        )
    )

    if report_type in ("all", "summary"):
        return export_all_reports_to_excel(start_dt, end_dt, start_date_str, end_date_str)

    spec = get_report_data(report_type, start_dt, end_dt)
    filename = f"{spec['base_filename']}{date_suffix}.csv"
    return generate_csv_response(filename, spec["headers"], spec["rows"])


def export_report_to_excel(report_type, start_dt=None, end_dt=None, start_date_str="", end_date_str=""):
    """
    Generates a beautifully styled .xlsx Excel spreadsheet using openpyxl.
    """
    date_suffix = f"_{start_date_str}_to_{end_date_str}" if (start_date_str and end_date_str) else (
        f"_from_{start_date_str}" if start_date_str else (
            f"_until_{end_date_str}" if end_date_str else "_all_time"
        )
    )
    date_filter_label = get_date_filter_label(start_date_str, end_date_str)

    if report_type in ("all", "summary"):
        return export_all_reports_to_excel(start_dt, end_dt, start_date_str, end_date_str)

    spec = get_report_data(report_type, start_dt, end_dt)
    filename = f"{spec['base_filename']}{date_suffix}.xlsx"

    return generate_excel_response(
        filename=filename,
        report_title=spec["title"],
        headers=spec["headers"],
        rows=spec["rows"],
        date_filter_label=date_filter_label,
    )


def export_report_to_pdf(report_type, start_dt=None, end_dt=None, start_date_str="", end_date_str=""):
    """
    Generates a formatted landscape PDF document using reportlab.
    """
    date_suffix = f"_{start_date_str}_to_{end_date_str}" if (start_date_str and end_date_str) else (
        f"_from_{start_date_str}" if start_date_str else (
            f"_until_{end_date_str}" if end_date_str else "_all_time"
        )
    )
    date_filter_label = get_date_filter_label(start_date_str, end_date_str)

    if report_type in ("all", "summary"):
        return export_all_reports_to_pdf(start_dt, end_dt, start_date_str, end_date_str)

    spec = get_report_data(report_type, start_dt, end_dt)
    filename = f"{spec['base_filename']}{date_suffix}.pdf"

    return generate_pdf_response(
        filename=filename,
        report_title=spec["title"],
        headers=spec["headers"],
        rows=spec["rows"],
        date_filter_label=date_filter_label,
        col_widths=spec.get("col_widths"),
    )


def export_report(report_type, export_format="csv", start_dt=None, end_dt=None, start_date_str="", end_date_str=""):
    """
    Unified dispatcher for reports export in CSV, Excel (.xlsx), or PDF (.pdf) format.
    """
    fmt = (export_format or "csv").lower().strip()
    if fmt in ("xlsx", "excel"):
        return export_report_to_excel(report_type, start_dt, end_dt, start_date_str, end_date_str)
    elif fmt == "pdf":
        return export_report_to_pdf(report_type, start_dt, end_dt, start_date_str, end_date_str)
    elif fmt == "csv":
        return export_report_to_csv(report_type, start_dt, end_dt, start_date_str, end_date_str)
    else:
        return export_report_to_excel(report_type, start_dt, end_dt, start_date_str, end_date_str)

