import io
from datetime import datetime, time
from xml.sax.saxutils import escape

from django.db.models import Count, Max, Q
from django.http import HttpResponse
from django.utils import timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import HRFlowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from accounts.models import Borrower, User
from facilities.models import Room
from keys.models import Key
from transactions.models import BorrowTransaction, GuardShift


# A key is "out" while it has not come back: borrowed on time (ACTIVE) or past
# due (OVERDUE). LOST / DAMAGED loans are closed out with a different outcome.
OUT_STATUSES = (BorrowTransaction.Status.ACTIVE, BorrowTransaction.Status.OVERDUE)


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

    # Still out = not returned yet, on time or overdue (an overdue key is out).
    currently_borrowed = filtered_txns.filter(
        returned_at__isnull=True, status__in=OUT_STATUSES
    ).count()

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
    active_q = Q(transactions__returned_at__isnull=True, transactions__status__in=OUT_STATUSES)

    keys = (
        Key.objects.select_related("room__building", "category")
        .annotate(
            total_borrowings=Count("transactions", filter=txn_date_q),
            currently_borrowed=Count("transactions", filter=active_q),
            returned_count=Count("transactions", filter=returned_q),
            last_borrowed_at=Max("transactions__borrowed_at", filter=txn_date_q),
        )
        # An archived key only stays on the report if it has history in the period.
        .exclude(Q(status=Key.Status.INACTIVE) & Q(total_borrowings=0))
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
            "status_label": k.get_status_display(),
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

    active_q = Q(transactions__returned_at__isnull=True, transactions__status__in=OUT_STATUSES)

    rooms = (
        Room.objects.select_related("building")
        .prefetch_related("keys")
        .annotate(
            total_borrowings=Count("transactions", filter=txn_date_q),
            currently_borrowed=Count("transactions", filter=active_q),
            last_used_at=Max("transactions__borrowed_at", filter=txn_date_q),
        )
        # An archived room only stays on the report if it has history in the period.
        .exclude(Q(is_active=False) & Q(total_borrowings=0))
        .order_by("-total_borrowings", "building__code", "room_code")
    )

    results = []
    max_borrowings = 0
    for rm in rooms:
        if rm.total_borrowings > max_borrowings:
            max_borrowings = rm.total_borrowings

    for rm in rooms:
        # Uses the prefetch (no query per room); archived keys are not "associated".
        associated_keys = [k.key_code for k in rm.keys.all()
                           if k.status != Key.Status.INACTIVE]
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
    active_q = Q(transactions__returned_at__isnull=True, transactions__status__in=OUT_STATUSES)

    borrowers = (
        Borrower.objects.select_related("department")
        .annotate(
            total_borrowings=Count("transactions", filter=txn_date_q),
            currently_active=Count("transactions", filter=active_q),
            returned_borrowings=Count("transactions", filter=returned_q),
            last_borrowed_at=Max("transactions__borrowed_at", filter=txn_date_q),
        )
        # A frequency ranking lists people who borrowed in the period, which
        # also makes its length match the "Active Borrowers" card.
        .filter(total_borrowings__gt=0)
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



# ---------------------------------------------------------------------------
# PDF export (the only export format the reports page offers)
# ---------------------------------------------------------------------------

REPORT_TYPES = (
    "key_utilisation",
    "overdue",
    "guard_activity",
    "room_usage",
    "borrower_frequency",
)
# "all" (and its old alias "summary") is the full multi-page suite.
VALID_EXPORTS = REPORT_TYPES + ("all", "summary")

PAGE_WIDTH = 792 - 72  # landscape letter minus the 36pt side margins
BRAND_DARK = "#14342A"
BRAND_GREEN = "#1E6B4A"


class NumberedCanvas(canvas.Canvas):
    """
    Two-pass canvas to calculate total page count and draw
    running footers and headers on all pages.
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
        self.setStrokeColor(colors.HexColor("#E5E7EB"))
        self.setLineWidth(0.5)
        self.line(36, 582, 792 - 36, 582)
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


def _date_suffix(start_date_str, end_date_str):
    if start_date_str and end_date_str:
        return f"_{start_date_str}_to_{end_date_str}"
    if start_date_str:
        return f"_from_{start_date_str}"
    if end_date_str:
        return f"_until_{end_date_str}"
    return "_all_time"


def _fmt_dt(value, empty="—"):
    if not value:
        return empty
    return timezone.localtime(value).strftime("%Y-%m-%d %H:%M")


def get_report_data(report_type, start_dt=None, end_dt=None):
    """
    Headers, rows and relative column weights for one report, laid out for a
    landscape PDF page. Cells are plain text; a newline becomes a line break.
    ``weights`` are scaled to the page width, so they only set proportions.
    """
    if report_type == "key_utilisation":
        data = get_key_utilisation_report(start_dt, end_dt)
        return {
            "title": "Key Utilisation Report",
            "base_filename": "key_utilisation_report",
            "headers": ["Key Code", "Room", "Category", "Total Borrowings",
                        "Currently Out", "Returned", "Key Status", "Last Borrowed"],
            "rows": [[
                r["key_code"],
                r["room_display"],
                r["category_name"],
                r["total_borrowings"],
                r["currently_borrowed"],
                r["returned_count"],
                r["status_label"],
                _fmt_dt(r["last_borrowed_at"], "Never"),
            ] for r in data],
            "weights": [80, 70, 90, 70, 65, 60, 75, 110],
            "center": {3, 4, 5, 6, 7},
        }

    if report_type == "overdue":
        data = get_overdue_report(start_dt, end_dt)
        return {
            "title": "Overdue Keys Report",
            "base_filename": "overdue_report",
            "headers": ["Transaction", "Borrower", "Contact", "Key / Room",
                        "Borrowed", "Expected Return", "Overdue By", "Released By"],
            "rows": [[
                r["transaction_no"],
                f"{r['borrower_name']}\nID {r['borrower_id']} · {r['borrower_type']}",
                "\n".join(x for x in (r["borrower_phone"], r["borrower_email"]) if x) or "—",
                f"{r['key_code']}\n{r['room_display']}",
                _fmt_dt(r["borrowed_at"]),
                _fmt_dt(r["expected_return_at"], "Not set"),
                r["overdue_duration"],
                r["released_by"],
            ] for r in data],
            "weights": [85, 120, 120, 70, 80, 80, 70, 80],
            "center": {4, 5, 6},
        }

    if report_type == "guard_activity":
        data = get_guard_activity_report(start_dt, end_dt)
        return {
            "title": "Per-Guard Activity Report",
            "base_filename": "guard_activity_report",
            "headers": ["Guard", "Username", "Role", "Keys Released", "Keys Received",
                        "Total Handled", "Shifts Logged", "Most Recent Activity"],
            "rows": [[
                r["guard_name"],
                r["username"],
                r["role"],
                r["keys_released"],
                r["keys_returned"],
                r["total_transactions"],
                r["shifts_count"],
                _fmt_dt(r["most_recent_activity"], "No activity"),
            ] for r in data],
            "weights": [120, 85, 80, 70, 70, 70, 65, 110],
            "center": {3, 4, 5, 6, 7},
        }

    if report_type == "room_usage":
        data = get_room_usage_report(start_dt, end_dt)
        return {
            "title": "Per-Room Usage Report",
            "base_filename": "room_usage_report",
            "headers": ["Room", "Building / Type", "Associated Keys", "Total Borrowings",
                        "Currently Out", "Last Used"],
            "rows": [[
                f"{r['room_display']}\n{r['room_name']}" if r["room_name"] else r["room_display"],
                f"{r['building_code']} · {r['room_type']}",
                r["associated_keys"],
                r["total_borrowings"],
                r["currently_borrowed"],
                _fmt_dt(r["last_used_at"], "Never"),
            ] for r in data],
            "weights": [120, 130, 170, 80, 75, 110],
            "center": {3, 4, 5},
        }

    if report_type == "borrower_frequency":
        data = get_borrower_frequency_report(start_dt, end_dt)
        return {
            "title": "Borrower Frequency Report",
            "base_filename": "borrower_frequency_report",
            "headers": ["Borrower", "Type / Department", "Contact", "Total Borrowings",
                        "Currently Out", "Returned", "Last Borrowed"],
            "rows": [[
                f"{r['borrower_name']}\nID {r['id_number']}",
                f"{r['borrower_type']} · {r['department']}",
                "\n".join(x for x in (r["contact_number"], r["email"]) if x and x != "—") or "—",
                r["total_borrowings"],
                r["currently_active"],
                r["returned_borrowings"],
                _fmt_dt(r["last_borrowed_at"], "Never"),
            ] for r in data],
            "weights": [130, 120, 140, 70, 65, 60, 110],
            "center": {3, 4, 5, 6},
        }

    raise ValueError(f"Unknown report type: {report_type}")


# What each report counts, printed under the table so a reader can check it.
REPORT_NOTES = {
    "key_utilisation": "Total Borrowings and Returned count loans borrowed within the date range. "
                       "Currently Out is live: keys not yet returned, on time or overdue. "
                       "Archived keys appear only if they were borrowed in the range.",
    "overdue": "Unreturned keys past their expected return time, as of the moment this report "
               "was generated. The date range filters by the date the key was borrowed.",
    "guard_activity": "Keys Released counts loans borrowed in the range; Keys Received counts "
                      "returns made in the range. Shifts Logged counts shifts started in the range.",
    "room_usage": "Total Borrowings counts loans borrowed within the date range. Currently Out is "
                  "live. Archived rooms appear only if used in the range.",
    "borrower_frequency": "Lists borrowers with at least one loan in the date range. Currently Out "
                          "is live: loans not yet returned, on time or overdue.",
}


def _pdf_styles():
    base = getSampleStyleSheet()["Normal"]
    td = ParagraphStyle("TD", parent=base, fontName="Helvetica", fontSize=7.5, leading=9.5,
                        textColor=colors.HexColor("#1F2937"))
    return {
        "title": ParagraphStyle("DocTitle", parent=base, fontName="Helvetica-Bold", fontSize=15,
                                leading=18, textColor=colors.HexColor(BRAND_DARK)),
        "subtitle": ParagraphStyle("DocSubtitle", parent=base, fontName="Helvetica-Bold",
                                   fontSize=11, leading=14, textColor=colors.HexColor(BRAND_GREEN)),
        "meta": ParagraphStyle("DocMeta", parent=base, fontName="Helvetica", fontSize=8.5,
                               leading=11, textColor=colors.HexColor("#4B5563")),
        "note": ParagraphStyle("DocNote", parent=base, fontName="Helvetica-Oblique", fontSize=7.5,
                               leading=10, textColor=colors.HexColor("#6B7280")),
        "th": ParagraphStyle("TH", parent=base, fontName="Helvetica-Bold", fontSize=7.5,
                             leading=9, textColor=colors.white, alignment=1),
        "td": td,
        "td_center": ParagraphStyle("TDCenter", parent=td, alignment=1),
    }


def _cell(value, style):
    """A table cell as a Paragraph. Text is escaped first: a name such as
    "R&D <Lab>" would otherwise break reportlab's mini-HTML parser."""
    text = "" if value is None else str(value)
    return Paragraph(escape(text).replace("\n", "<br/>"), style)


def _report_table(spec, st):
    """The striped data table for one report spec, scaled to the page width."""
    headers, rows = spec["headers"], spec["rows"]
    weights = spec["weights"]
    scale = PAGE_WIDTH / float(sum(weights))
    col_widths = [w * scale for w in weights]

    data = [[_cell(h, st["th"]) for h in headers]]
    if rows:
        for row in rows:
            data.append([
                _cell(val, st["td_center"] if i in spec["center"] else st["td"])
                for i, val in enumerate(row)
            ])
    else:
        data.append([_cell("No records found for the selected period.", st["td_center"])]
                    + [""] * (len(headers) - 1))

    table = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(BRAND_GREEN)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    if rows:
        style += [("BACKGROUND", (0, r), (-1, r), colors.HexColor("#F8FAF9"))
                  for r in range(2, len(data), 2)]
    else:
        style.append(("SPAN", (0, 1), (-1, 1)))
    table.setStyle(TableStyle(style))
    return table


def _letterhead(st, subtitle, meta_html):
    return [
        Paragraph("KEYTRACK — UNIVERSITY KEY MANAGEMENT SYSTEM", st["title"]),
        Spacer(1, 3),
        Paragraph(escape(subtitle.upper()), st["subtitle"]),
        Spacer(1, 3),
        Paragraph(meta_html, st["meta"]),
        Spacer(1, 6),
        HRFlowable(width="100%", thickness=1.5, color=colors.HexColor(BRAND_GREEN),
                   spaceBefore=2, spaceAfter=8),
    ]


def _pdf_response(filename, story):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter), leftMargin=36, rightMargin=36,
                            topMargin=40, bottomMargin=40, title=filename.rsplit(".", 1)[0],
                            author="KEYTRACK")
    doc.build(story, canvasmaker=NumberedCanvas)
    response = HttpResponse(buf.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def export_report_to_pdf(report_type, start_dt=None, end_dt=None, start_date_str="", end_date_str=""):
    """One report as a landscape PDF, or the whole suite for "all"."""
    if report_type in ("all", "summary"):
        return export_all_reports_to_pdf(start_dt, end_dt, start_date_str, end_date_str)

    st = _pdf_styles()
    date_label = get_date_filter_label(start_date_str, end_date_str)
    spec = get_report_data(report_type, start_dt, end_dt)
    now_str = timezone.localtime().strftime("%Y-%m-%d %I:%M %p")

    story = _letterhead(
        st, spec["title"],
        f"<b>Date Filter:</b> {escape(date_label)} &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"<b>Generated:</b> {now_str} &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"<b>Total Records:</b> {len(spec['rows'])}")
    story.append(_report_table(spec, st))
    story += [Spacer(1, 8), Paragraph(escape(REPORT_NOTES[report_type]), st["note"])]

    filename = f"{spec['base_filename']}{_date_suffix(start_date_str, end_date_str)}.pdf"
    return _pdf_response(filename, story)


def export_all_reports_to_pdf(start_dt=None, end_dt=None, start_date_str="", end_date_str=""):
    """The full suite: an executive overview page, then one page group per report."""
    st = _pdf_styles()
    date_label = get_date_filter_label(start_date_str, end_date_str)
    now_str = timezone.localtime().strftime("%Y-%m-%d %I:%M %p")

    story = _letterhead(
        st, "Executive Audit & Operations Report Suite",
        f"<b>Date Filter:</b> {escape(date_label)} &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"<b>Generated:</b> {now_str}")

    m = get_summary_metrics(start_dt, end_dt)
    overview = [
        ("Total Key Borrowings", m["total_borrowings"], "Checkout transactions borrowed in this period"),
        ("Currently Out", m["currently_borrowed"], "Keys not yet returned, on time or overdue"),
        ("Overdue Keys", m["overdue_count"], "Unreturned keys past their expected return time"),
        ("Completed Returns", m["returned_count"], "Loans borrowed in this period that were returned"),
        ("Active Borrowers", m["active_borrowers"], "Distinct borrowers with a loan in this period"),
        ("Active Guards", m["active_guards"], "Guards who released or received a key in this period"),
    ]
    rows = [[_cell("Operational Metric", st["th"]), _cell("Value", st["th"]),
             _cell("What it counts", st["th"])]]
    for label, value, desc in overview:
        rows.append([_cell(label, st["td"]), _cell(value, st["td_center"]), _cell(desc, st["td"])])
    table = Table(rows, colWidths=[200, 100, PAGE_WIDTH - 300], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(BRAND_GREEN)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(table)

    headings = {
        "key_utilisation": "1. Key Utilisation Report",
        "overdue": "2. Overdue Keys Report",
        "guard_activity": "3. Per-Guard Activity Report",
        "room_usage": "4. Per-Room Usage Report",
        "borrower_frequency": "5. Borrower Frequency Report",
    }
    for report_type in REPORT_TYPES:
        spec = get_report_data(report_type, start_dt, end_dt)
        story += [
            PageBreak(),
            Paragraph(escape(headings[report_type].upper()), st["subtitle"]),
            Spacer(1, 2),
            Paragraph(f"<b>Records:</b> {len(spec['rows'])} &nbsp;&nbsp;|&nbsp;&nbsp; "
                      f"<b>Date Range:</b> {escape(date_label)}", st["meta"]),
            Spacer(1, 6),
            _report_table(spec, st),
            Spacer(1, 8),
            Paragraph(escape(REPORT_NOTES[report_type]), st["note"]),
        ]

    filename = f"keytrack_complete_reports{_date_suffix(start_date_str, end_date_str)}.pdf"
    return _pdf_response(filename, story)


def export_report(report_type, start_dt=None, end_dt=None, start_date_str="", end_date_str=""):
    """Reports export as PDF only."""
    return export_report_to_pdf(report_type, start_dt, end_dt, start_date_str, end_date_str)


def export_table_pdf(*, filename, title, headers, rows, weights, center=(), meta_extra="",
                     note="", date_label="All Available Records"):
    """A single landscape table as a PDF, in the same letterhead as the reports.

    Other pages (e.g. the transaction history) use this so every PDF the app
    produces looks alike. Cells are plain text; a newline is a line break.
    """
    st = _pdf_styles()
    now_str = timezone.localtime().strftime("%Y-%m-%d %I:%M %p")
    spec = {"headers": headers, "rows": rows, "weights": weights, "center": set(center)}
    story = _letterhead(
        st, title,
        f"<b>Filters:</b> {escape(date_label)} &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"<b>Generated:</b> {now_str} &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"<b>Total Records:</b> {len(rows)}{meta_extra}")
    story.append(_report_table(spec, st))
    if note:
        story += [Spacer(1, 8), Paragraph(escape(note), st["note"])]
    return _pdf_response(filename, story)
