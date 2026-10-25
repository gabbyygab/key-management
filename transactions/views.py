import csv
import datetime
from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import admin_or_guard_required, admin_required, guard_required
from accounts.permissions import admin_or_guard_required, guard_required, admin_required
from accounts.models import Borrower, User
from audit.models import ActivityLog
from facilities.models import Building, Room
from keys.models import Key
from scheduling.models import ScheduleBlock, ScheduleException
from .models import BorrowTransaction, GuardShift
from .services import (
    BorrowError,
    active_transactions,
    borrow_key,
    get_active_shift,
    return_key,
)


@admin_or_guard_required
def dashboard(request):
    """KEYTRACK dashboard: Role-split view.

    - ADMIN: Live stat cards (total keys, available, borrowed, overdue,
             rooms, scheduled blocks today, pending approvals),
             per-building availability bars, today's schedule overview,
             overdue keys table, recent audit activity, guards on duty.
    - GUARD: Shift status + elapsed time, keys released this shift,
             all keys currently out, quick action through to the POS.
    """
    is_admin = request.user.is_superuser or request.user.role == "ADMIN"
    now = timezone.now()
    today = timezone.localdate()

    if is_admin:
        total_keys = Key.objects.count()
        available = Key.objects.filter(status=Key.Status.AVAILABLE).count()
        borrowed = Key.objects.filter(status=Key.Status.BORROWED).count()
        overdue = BorrowTransaction.objects.filter(status=BorrowTransaction.Status.OVERDUE).count()
        total_rooms = Room.objects.filter(is_active=True).count()
        pending_approvals = User.objects.filter(approval_status=User.Approval.PENDING).count()

        # Schedule blocks for today
        today_blocks_qs = (
            ScheduleBlock.objects.filter(
                is_active=True,
                day_of_week=today.weekday(),
                term__is_active=True,
            )
            .select_related("room__building", "subject", "instructor")
        )
        cancelled_block_ids = ScheduleException.objects.filter(
            date=today,
            exception_type=ScheduleException.ExceptionType.CANCELLED,
        ).values_list("schedule_block_id", flat=True)
        today_blocks = today_blocks_qs.exclude(id__in=cancelled_block_ids).order_by("start_time")
        blocks_scheduled_today = today_blocks.count()
        today_schedule = today_blocks[:10]

        # Buildings availability bars
        buildings = Building.objects.filter(is_active=True).order_by("code")[:8]
        building_rows = []
        for b in buildings:
            qs = Key.objects.filter(room__building=b)
            a = qs.filter(status=Key.Status.AVAILABLE).count()
            br = qs.filter(status=Key.Status.BORROWED).count()
            total = qs.count()
            un = max(total - a - br, 0)
            building_rows.append({
                "code": b.code,
                "available": a,
                "borrowed": br,
                "unavailable": un,
                "total": total or 1,
            })

        # Guards currently on duty
        guards_on_duty = (
            GuardShift.objects.filter(is_on_duty=True)
            .select_related("guard")
            .order_by("-shift_start")
        )

        # Overdue keys
        overdue_keys = (
            BorrowTransaction.objects.filter(status=BorrowTransaction.Status.OVERDUE)
            .select_related("key__room__building", "borrower__department", "released_by")
            .order_by("-expected_return_at", "-borrowed_at")[:10]
        )

        # Recent audit activity
        try:
            recent_audit = (
                ActivityLog.objects.select_related("user")
                .order_by("-created_at")[:10]
            )
        except Exception:
            recent_audit = []

        recent = (
            BorrowTransaction.objects
            .select_related("key__room__building", "borrower", "released_by")
            .order_by("-borrowed_at")[:6]
        )

        return render(request, "dashboard.html", {
            "is_admin": True,
            "total_keys": total_keys,
            "available": available,
            "borrowed": borrowed,
            "overdue": overdue,
            "total_rooms": total_rooms,
            "blocks_scheduled_today": blocks_scheduled_today,
            "pending_approvals": pending_approvals,
            "today_schedule": today_schedule,
            "today_date": today,
            "today_name": today.strftime("%A"),
            "building_rows": building_rows,
            "building_count": buildings.count(),
            "guards_on_duty": guards_on_duty,
            "overdue_keys": overdue_keys,
            "recent_audit": recent_audit,
            "recent": recent,
        })

    # Guard Dashboard
    shift = get_active_shift(request.user)
    shift_elapsed = ""
    if shift:
        delta = now - shift.shift_start
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)
        shift_elapsed = f"{hours}h {minutes:02d}m"

    if shift:
        my_released_txns = (
            BorrowTransaction.objects.filter(guard_shift=shift)
            .select_related("key__room__building", "borrower__department")
            .order_by("-borrowed_at")
        )
    else:
        my_released_txns = BorrowTransaction.objects.none()

    currently_out = active_transactions()[:20]
    currently_out_count = BorrowTransaction.objects.filter(
        status__in=[BorrowTransaction.Status.ACTIVE, BorrowTransaction.Status.OVERDUE]
    ).count()
    overdue_count = BorrowTransaction.objects.filter(
        status=BorrowTransaction.Status.OVERDUE
    ).count()

    return render(request, "dashboard.html", {
        "is_admin": False,
        "shift": shift,
        "shift_elapsed": shift_elapsed,
        "my_released_txns": my_released_txns,
        "my_released_count": my_released_txns.count(),
        "currently_out": currently_out,
        "currently_out_count": currently_out_count,
        "overdue_count": overdue_count,
    })


@guard_required
def borrow_dashboard(request):
    """Borrow Management: search person -> select -> search available key -> confirm.
    Right panel: currently borrowed keys with return button.
    """
    q_borrower = request.GET.get("q_borrower", "").strip()
    q_key = request.GET.get("q_key", "").strip()
    borrower_id = request.GET.get("borrower_id", "").strip()

    borrowers = Borrower.objects.select_related("department").filter(is_active=True)
    if q_borrower:
        borrowers = borrowers.filter(
            Q(first_name__icontains=q_borrower) | Q(last_name__icontains=q_borrower)
            | Q(id_number__icontains=q_borrower))
    borrowers = borrowers[:20]

    selected = None
    active_txn = None
    available_keys = []
    if borrower_id:
        selected = Borrower.objects.filter(pk=borrower_id).first()
        if selected:
            active_txn = selected.active_transaction()
            if not active_txn and selected.can_borrow:
                keys = Key.objects.select_related(
                    "room__building", "category").filter(status=Key.Status.AVAILABLE)
                if q_key:
                    keys = keys.filter(
                        Q(key_code__icontains=q_key) | Q(room__room_code__icontains=q_key)
                        | Q(room__building__code__icontains=q_key)
                        | Q(description__icontains=q_key))
                available_keys = keys[:30]

    borrowed = (BorrowTransaction.objects
                .select_related("key__room__building", "borrower", "released_by")
                .filter(status=BorrowTransaction.Status.ACTIVE)[:100])

    shift = get_active_shift(request.user)

    return render(request, "transactions/borrow_dashboard.html", {
        "q_borrower": q_borrower, "q_key": q_key,
        "borrowers": borrowers, "selected": selected,
        "active_txn": active_txn, "available_keys": available_keys,
        "borrowed": borrowed, "shift": shift,
    })


@guard_required
@require_POST
def borrow_confirm(request):
    borrower_id = request.POST.get("borrower_id")
    key_id = request.POST.get("key_id")
    purpose = request.POST.get("purpose", "").strip()
    borrower = get_object_or_404(Borrower, pk=borrower_id)
    try:
        txn = borrow_key(key_id=key_id, borrower=borrower,
                          guard=request.user, purpose=purpose, request=request)
        messages.success(request, f"Released {txn.key.key_code} to {borrower.full_name} ({txn.transaction_no}).")
    except BorrowError as e:
        messages.error(request, str(e))
    except Key.DoesNotExist:
        messages.error(request, "Key not found.")
    url = reverse("transactions:borrow_dashboard")
    return redirect(f"{url}?{urlencode({'borrower_id': borrower_id})}")


@guard_required
@require_POST
def return_confirm(request, pk):
    remarks = request.POST.get("remarks", "").strip()
    try:
        txn = return_key(transaction_id=pk, guard=request.user, remarks=remarks)
        messages.success(request, f"Key {txn.key.key_code} returned.")
    except BorrowError as e:
        messages.error(request, str(e))
    return redirect("transactions:borrow_dashboard")


@admin_or_guard_required
def shift_history(request):
    """Read-only shift list. Shifts start on login and end on logout.

    Admins see all shifts with a guard filter; guards see only their own.
    Columns: guard, post, start, end, duration, transaction count, on-duty pill.
    """
    is_admin = request.user.is_superuser or request.user.role == "ADMIN"

    shifts = (
        GuardShift.objects.select_related("guard")
        .annotate(transaction_count=Count("transactions"))
        .order_by("-shift_start")
    )

    guard_filter = ""
    guard_users = []

    if is_admin:
        guard_filter = request.GET.get("guard", "").strip()
        if guard_filter:
            if guard_filter.isdigit():
                shifts = shifts.filter(guard_id=int(guard_filter))
            else:
                shifts = shifts.filter(
                    Q(guard__username__icontains=guard_filter)
                    | Q(guard__first_name__icontains=guard_filter)
                    | Q(guard__last_name__icontains=guard_filter)
                )
        guard_users = (
            User.objects.filter(
                Q(role=User.Role.GUARD) | Q(shifts__isnull=False)
            )
            .distinct()
            .order_by("first_name", "last_name", "username")
        )
    else:
        # Guards see ONLY their own shifts
        shifts = shifts.filter(guard=request.user)

    paginator = Paginator(shifts, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    try:
        elided_pages = list(paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1))
    except Exception:
        elided_pages = list(paginator.page_range)

    return render(
        request,
        "transactions/shift_history.html",
        {
            "is_admin": is_admin,
            "shifts": page_obj,
            "page_obj": page_obj,
            "guard_filter": guard_filter,
            "guard_users": guard_users,
            "elided_pages": elided_pages,
        },
    )


def get_filtered_transactions(request):
    """Filter BorrowTransaction queryset based on GET query parameters.

    Supports:
      - q: search transaction_no, key code, borrower name, borrower ID, room code
      - start_date / date_from: borrowed_at >= start_date
      - end_date / date_to: borrowed_at <= end_date
      - status: transaction status choice (ACTIVE, RETURNED, OVERDUE, LOST, DAMAGED)
      - building: building code or ID
      - guard: guard User ID or username (matches released_by or received_by)
    """
    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    building_filter = request.GET.get("building", "").strip()
    guard_filter = request.GET.get("guard", "").strip()
    start_date = request.GET.get("start_date", "").strip() or request.GET.get("date_from", "").strip()
    end_date = request.GET.get("end_date", "").strip() or request.GET.get("date_to", "").strip()

    txns = (
        BorrowTransaction.objects
        .select_related(
            "key__room__building",
            "key__category",
            "room__building",
            "borrower__department",
            "released_by",
            "received_by",
            "guard_shift",
        )
        .all()
    )

    if q:
        txns = txns.filter(
            Q(transaction_no__icontains=q)
            | Q(key__key_code__icontains=q)
            | Q(borrower__first_name__icontains=q)
            | Q(borrower__last_name__icontains=q)
            | Q(borrower__id_number__icontains=q)
            | Q(room__room_code__icontains=q)
            | Q(room__building__code__icontains=q)
        )

    if status_filter:
        txns = txns.filter(status=status_filter)

    if building_filter:
        if building_filter.isdigit():
            txns = txns.filter(
                Q(room__building_id=building_filter) | Q(key__room__building_id=building_filter)
            )
        else:
            txns = txns.filter(
                Q(room__building__code__iexact=building_filter) | Q(key__room__building__code__iexact=building_filter)
            )

    if guard_filter:
        if guard_filter.isdigit():
            txns = txns.filter(
                Q(released_by_id=guard_filter) | Q(received_by_id=guard_filter)
            )
        else:
            txns = txns.filter(
                Q(released_by__username__iexact=guard_filter) | Q(received_by__username__iexact=guard_filter)
            )

    if start_date:
        try:
            d_start = datetime.date.fromisoformat(start_date)
            txns = txns.filter(borrowed_at__date__gte=d_start)
        except ValueError:
            pass

    if end_date:
        try:
            d_end = datetime.date.fromisoformat(end_date)
            txns = txns.filter(borrowed_at__date__lte=d_end)
        except ValueError:
            pass

    return txns.order_by("-borrowed_at")


@admin_or_guard_required
def transaction_history(request):
    """Transaction history with search, filters, pagination, and detail views (TASK F6.2)."""
    txns = get_filtered_transactions(request)

    # Real pagination: 20 per page
    paginator = Paginator(txns, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # Preserve query string (without 'page') for pagination and export links
    params = request.GET.copy()
    if "page" in params:
        del params["page"]
    querystring = params.urlencode()

    buildings = Building.objects.filter(is_active=True).order_by("code")
    guards = (
        User.objects
        .filter(Q(role=User.Role.GUARD) | Q(keys_released__isnull=False) | Q(keys_received__isnull=False))
        .distinct()
        .order_by("first_name", "last_name", "username")
    )

    return render(request, "transactions/history.html", {
        "page_obj": page_obj,
        "txns": page_obj.object_list,
        "q": request.GET.get("q", "").strip(),
        "status": request.GET.get("status", "").strip(),
        "building": request.GET.get("building", "").strip(),
        "guard": request.GET.get("guard", "").strip(),
        "start_date": request.GET.get("start_date", "").strip() or request.GET.get("date_from", "").strip(),
        "end_date": request.GET.get("end_date", "").strip() or request.GET.get("date_to", "").strip(),
        "buildings": buildings,
        "guards": guards,
        "statuses": BorrowTransaction.Status.choices,
        "querystring": querystring,
    today_only = request.GET.get("today", "").strip()
    txns = BorrowTransaction.objects.select_related(
        "key__room__building", "borrower", "released_by", "received_by").all()
    if today_only:
        txns = txns.filter(borrowed_at__date=timezone.localdate())
    if q:
        txns = txns.filter(
            Q(transaction_no__icontains=q) | Q(key__key_code__icontains=q)
            | Q(borrower__first_name__icontains=q) | Q(borrower__last_name__icontains=q)
            | Q(borrower__id_number__icontains=q))
    return render(request, "transactions/history.html", {
        "txns": txns[:200],
        "q": q,
        "today_only": bool(today_only),
    })


@admin_required
def export_transactions_csv(request):
    """Export filtered transaction history as a CSV file (Admin only, TASK F6.2)."""
    txns = get_filtered_transactions(request)

    timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
    filename = f"keytrack_transactions_{timestamp}.csv"

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow([
        "Transaction No",
        "Status",
        "Key Code",
        "Key Category",
        "Building",
        "Room",
        "Borrower Name",
        "Borrower ID",
        "Borrower Type",
        "Department",
        "Purpose",
        "Borrowed At",
        "Expected Return At",
        "Released By",
        "Guard Shift Post",
        "Returned At",
        "Received By",
        "Return Confirmed",
        "Remarks",
        "Override Reason",
    ])

    for t in txns:
        room_bldg = t.room.building.code if (t.room and t.room.building) else ""
        room_code = t.room.room_code if t.room else ""
        writer.writerow([
            t.transaction_no,
            t.status,
            t.key.key_code if t.key else "",
            t.key.category.name if (t.key and t.key.category) else "",
            room_bldg,
            room_code,
            t.borrower.full_name if t.borrower else "",
            t.borrower.id_number if t.borrower else "",
            t.borrower.get_borrower_type_display() if t.borrower else "",
            t.borrower.department.code if (t.borrower and t.borrower.department) else "",
            t.purpose,
            timezone.localtime(t.borrowed_at).strftime("%Y-%m-%d %H:%M:%S") if t.borrowed_at else "",
            timezone.localtime(t.expected_return_at).strftime("%Y-%m-%d %H:%M:%S") if t.expected_return_at else "",
            t.released_by.get_full_name() or t.released_by.username if t.released_by else "",
            t.guard_shift.post if t.guard_shift else "",
            timezone.localtime(t.returned_at).strftime("%Y-%m-%d %H:%M:%S") if t.returned_at else "",
            t.received_by.get_full_name() or t.received_by.username if t.received_by else "",
            "Yes" if t.return_confirmed else "No",
            t.remarks,
            t.override_reason,
        ])

    return response
def reports_view(request):
    total_txns = BorrowTransaction.objects.count()
    active_txns = BorrowTransaction.objects.filter(status=BorrowTransaction.Status.ACTIVE).count()
    overdue_txns = BorrowTransaction.objects.filter(status=BorrowTransaction.Status.OVERDUE).count()
    returned_txns = BorrowTransaction.objects.filter(status=BorrowTransaction.Status.RETURNED).count()
    return render(request, "reports/index.html", {
        "total_txns": total_txns,
        "active_txns": active_txns,
        "overdue_txns": overdue_txns,
        "returned_txns": returned_txns,
    })
