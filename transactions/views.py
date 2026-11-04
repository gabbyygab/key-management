import datetime
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction as db_transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.cache_utils import remember
from accounts.api import serialize_borrower
from accounts.crud import is_modal, paginate, querystring_without_page
from accounts.models import Borrower, User
from accounts.permissions import admin_or_guard_required, admin_required, guard_required
from audit.models import ActivityLog
from audit.services import Action, log_activity
from facilities.models import Building, Room
from keys.models import Key
from scheduling.models import ScheduleBlock, ScheduleException
from scheduling.services import effective_term, get_room_today_schedule, get_schedule_settings
from .forms import TransactionCorrectionForm
from .models import BorrowTransaction, GuardShift
from .services import (
    BorrowError,
    ScheduleWarning,
    OPEN_STATUSES,
    active_transactions,
    admin_set_status,
    due_state,
    get_active_shift,
    log_return,
    parse_due,
    pos_borrow,
    return_key_with_condition,
)


def _admin_dashboard_data(today):
    """Everything the admin dashboard shows, as plain lists and numbers so it
    can be cached (accounts.cache_utils). Kept to a handful of queries: each
    one is a round trip to the remote database."""
    key_counts = Key.objects.aggregate(
        total=Count("pk"),
        available=Count("pk", filter=Q(status=Key.Status.AVAILABLE)),
        borrowed=Count("pk", filter=Q(status=Key.Status.BORROWED)),
    )
    overdue = BorrowTransaction.objects.filter(status=BorrowTransaction.Status.OVERDUE).count()
    total_rooms = Room.objects.filter(is_active=True).count()

    cancelled_block_ids = ScheduleException.objects.filter(
        date=today, exception_type=ScheduleException.ExceptionType.CANCELLED,
    ).values_list("schedule_block_id", flat=True)
    today_blocks = (
        ScheduleBlock.objects.filter(is_active=True, day_of_week=today.weekday(), term__is_active=True)
        .exclude(id__in=cancelled_block_ids)
        .select_related("room__building", "subject", "instructor")
        .order_by("start_time")
    )
    blocks_scheduled_today = today_blocks.count()
    today_schedule = list(today_blocks[:10])

    # Per-building availability: one grouped query instead of three per building.
    buildings = list(Building.objects.filter(is_active=True).order_by("code")[:8])
    per_building = {
        row["room__building"]: row
        for row in Key.objects.filter(room__building__in=[b.pk for b in buildings])
        .values("room__building")
        .annotate(
            total=Count("pk"),
            available=Count("pk", filter=Q(status=Key.Status.AVAILABLE)),
            borrowed=Count("pk", filter=Q(status=Key.Status.BORROWED)),
        )
    }
    building_rows = []
    for b in buildings:
        row = per_building.get(b.pk, {})
        total, a, br = row.get("total", 0), row.get("available", 0), row.get("borrowed", 0)
        building_rows.append({
            "code": b.code,
            "available": a,
            "borrowed": br,
            "unavailable": max(total - a - br, 0),
            "total": total or 1,
        })

    return {
        "total_keys": key_counts["total"],
        "available": key_counts["available"],
        "borrowed": key_counts["borrowed"],
        "overdue": overdue,
        "total_rooms": total_rooms,
        "blocks_scheduled_today": blocks_scheduled_today,
        "today_schedule": today_schedule,
        "today_date": today,
        "today_name": today.strftime("%A"),
        "building_rows": building_rows,
        "building_count": len(buildings),
        "guards_on_duty": list(
            GuardShift.objects.filter(is_on_duty=True).select_related("guard").order_by("-shift_start")
        ),
        "overdue_keys": list(
            BorrowTransaction.objects.filter(status=BorrowTransaction.Status.OVERDUE)
            .select_related("key__room__building", "borrower__department", "released_by")
            .order_by("-expected_return_at", "-borrowed_at")[:10]
        ),
        "recent_audit": list(ActivityLog.objects.select_related("user").order_by("-created_at")[:10]),
        "recent": list(
            BorrowTransaction.objects
            .select_related("key__room__building", "borrower", "released_by")
            .order_by("-borrowed_at")[:6]
        ),
    }


@admin_or_guard_required
def dashboard(request):
    """KEYTRACK dashboard: Role-split view.

    - ADMIN: Live stat cards (total keys, available, borrowed, overdue,
             rooms, scheduled blocks today),
             per-building availability bars, today's schedule overview,
             overdue keys table, recent audit activity, guards on duty.
    - GUARD: Shift status + elapsed time, keys released this shift,
             all keys currently out, quick action through to the POS.
    """
    is_admin = request.user.is_superuser or request.user.role == "ADMIN"
    now = timezone.now()
    today = timezone.localdate()

    if is_admin:
        data = remember("dashboard-admin", ["inventory", "txn", "shift", "schedule", "audit"],
                        lambda: _admin_dashboard_data(today), parts=[today.isoformat()])
        return render(request, "dashboard.html", {"is_admin": True, **data})

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
    key_id = request.GET.get("key_id", "").strip()

    borrowers = Borrower.objects.select_related("department").filter(is_active=True)
    if q_borrower:
        borrowers = borrowers.filter(
            Q(first_name__icontains=q_borrower) | Q(last_name__icontains=q_borrower)
            | Q(id_number__icontains=q_borrower))
    borrowers = borrowers[:20]

    selected = None
    active_txn = None
    available_keys = []
    selected_key = None
    selected_key_schedule = None
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
                available_keys = list(keys[:30])
                room_schedules = {}
                for k in available_keys:
                    if k.room_id not in room_schedules:
                        room_schedules[k.room_id] = get_room_today_schedule(k.room)
                    k.today_schedule = room_schedules[k.room_id]

            if key_id:
                selected_key = Key.objects.select_related("room__building", "category").filter(pk=key_id).first()
                if selected_key:
                    selected_key_schedule = get_room_today_schedule(selected_key.room)

    # B8.11: replace the hard borrowed[:100] slice with real pagination so the
    # currently-out list stays complete and pageable (noscript fallback too).
    borrowed_page = paginate(
        request,
        BorrowTransaction.objects
        .select_related("key__room__building", "borrower", "released_by")
        .filter(status=BorrowTransaction.Status.ACTIVE),
        per_page=25,
    )

    # Keep search/borrower state in pager links (drop only the page param).
    params = request.GET.copy()
    if "page" in params:
        del params["page"]
    querystring = params.urlencode()

    schedule_cfg = get_schedule_settings()
    shift = get_active_shift(request.user)
    buildings = Building.objects.filter(is_active=True).order_by("code")

    return render(request, "transactions/borrow_dashboard.html", {
        "buildings": buildings,
        "schedule_enforcement": schedule_cfg.enforcement,
        "schedule_term": effective_term(timezone.localdate(), schedule_cfg),
        "q_borrower": q_borrower, "q_key": q_key,
        "borrowers": borrowers, "selected": selected,
        "active_txn": active_txn, "available_keys": available_keys,
        "selected_key": selected_key, "selected_key_schedule": selected_key_schedule,
        "borrowed": borrowed_page, "page_obj": borrowed_page,
        "querystring": querystring, "shift": shift,
        # pos.js picks up a borrower chosen through the no-JS links.
        "selected_json": serialize_borrower(selected, active=active_txn) if selected else None,
    })


@guard_required
@require_POST
def borrow_confirm(request):
    """No-JS fallback for the POS release. Same wrapper as the JSON API."""
    borrower_id = request.POST.get("borrower_id", "")
    key_id = request.POST.get("key_id", "")
    purpose = request.POST.get("purpose", "").strip()
    borrower = get_object_or_404(Borrower, pk=borrower_id if borrower_id.isdigit() else 0)
    due, due_error = None, None
    try:
        due = parse_due(request.POST.get("expected_return_at"))
    except ValueError as e:
        due_error = str(e)
    if due_error:
        messages.error(request, due_error)
    elif not key_id.isdigit():
        messages.error(request, "Key not found.")
    else:
        try:
            txn = pos_borrow(key_id=int(key_id), borrower=borrower, guard=request.user,
                             purpose=purpose, expected_return_at=due,
                             override_reason=request.POST.get("override_reason", ""),
                             request=request)
            messages.success(request, f"Released {txn.key.key_code} to {borrower.full_name} ({txn.transaction_no}).")
        except ScheduleWarning as e:
            messages.warning(request, str(e))
        except BorrowError as e:
            messages.error(request, str(e))
    url = reverse("transactions:borrow_dashboard")
    return redirect(f"{url}?{urlencode({'borrower_id': borrower_id})}")


@guard_required
@require_POST
def return_confirm(request, pk):
    remarks = request.POST.get("remarks", "").strip()
    condition = request.POST.get("condition", "OK")
    try:
        txn = return_key_with_condition(transaction_id=pk, guard=request.user,
                                        remarks=remarks, condition=condition)
        log_return(txn, guard=request.user, condition=condition, request=request)
        messages.success(request, f"Key {txn.key.key_code} returned.")
    except BorrowTransaction.DoesNotExist:
        messages.error(request, "Transaction not found.")
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
        .order_by("-shift_start", "-pk")
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

    page_obj = paginate(request, shifts)

    return render(
        request,
        "transactions/shift_history.html",
        {
            "is_admin": is_admin,
            "shifts": page_obj,
            "page_obj": page_obj,
            "guard_filter": guard_filter,
            "guard_users": guard_users,
            "querystring": querystring_without_page(request),
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

    if request.GET.get("today"):
        txns = txns.filter(borrowed_at__date=timezone.localdate())

    if request.GET.get("off_schedule"):
        txns = txns.filter(off_schedule=True)

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

    return txns.order_by("-borrowed_at", "-pk")


@admin_or_guard_required
def transaction_history(request):
    """Transaction history with search, filters, pagination, and detail views (TASK F6.2)."""
    txns = get_filtered_transactions(request)

    # 10 per page (accounts.crud.LIST_PAGE_SIZE), shared list design.
    page_obj = paginate(request, txns)

    # Preserve query string (without 'page') for pagination and export links.
    querystring = querystring_without_page(request)

    buildings = remember("history-filter-buildings", ["inventory"],
                         lambda: list(Building.objects.filter(is_active=True).order_by("code")))
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
        "off_schedule": bool(request.GET.get("off_schedule")),
        "querystring": querystring,
    })


def _fmt_local(value):
    return timezone.localtime(value).strftime("%Y-%m-%d %H:%M") if value else ""


def _person(user):
    return (user.get_full_name() or user.username) if user else ""


NL = chr(10)  # a line break inside a PDF cell


def transaction_pdf_rows(txns):
    """Rows for the history PDF, one per transaction, laid out for a landscape page."""
    rows = []
    for t in txns:
        room = f"{t.room.building.code}-{t.room.room_code}" if t.room else ""
        borrower = ""
        if t.borrower:
            dept = t.borrower.department.code if t.borrower.department else ""
            kind = " · ".join(x for x in (t.borrower.get_borrower_type_display(), dept) if x)
            borrower = f"{t.borrower.full_name}{NL}ID {t.borrower.id_number} · {kind}"
        returned = _fmt_local(t.returned_at) or "Not returned"
        if t.received_by:
            returned += f"{NL}by {_person(t.received_by)}"
        override = t.override_reason
        notes = NL.join(x for x in (
            t.remarks.strip(),
            f"Override: {override}" if override and override not in (t.remarks or "") else "",
        ) if x)
        borrowed = _fmt_local(t.borrowed_at)
        if t.released_by:
            borrowed += f"{NL}by {_person(t.released_by)}"
        rows.append([
            t.transaction_no,
            t.get_status_display(),
            f"{t.key.key_code}{NL}{room}" if t.key else room,
            borrower,
            t.purpose or "",
            borrowed,
            _fmt_local(t.expected_return_at) or "—",
            returned,
            notes or "—",
        ])
    return rows


@admin_required
def export_transactions_pdf(request):
    """Export the filtered transaction history as a landscape PDF (Admin only).

    Honours the same search, status, building, guard and date filters as the list."""
    from reports.services import export_table_pdf

    txns = (get_filtered_transactions(request)
            .select_related("key__category", "room__building", "borrower__department",
                            "released_by", "received_by", "guard_shift"))
    filters = []
    for label, key in (("Search", "q"), ("Status", "status"), ("Building", "building"),
                       ("Guard", "guard")):
        val = request.GET.get(key, "").strip()
        if val:
            filters.append(f"{label}: {val}")
    start = (request.GET.get("start_date") or request.GET.get("date_from") or "").strip()
    end = (request.GET.get("end_date") or request.GET.get("date_to") or "").strip()
    if start or end:
        filters.append(f"Dates: {start or '…'} to {end or '…'}")
    if request.GET.get("off_schedule"):
        filters.append("Off-schedule only")

    timestamp = timezone.localtime().strftime("%Y%m%d_%H%M%S")
    return export_table_pdf(
        filename=f"keytrack_transactions_{timestamp}.pdf",
        title="Key Transaction History",
        headers=["Transaction", "Status", "Key / Room", "Borrower", "Purpose",
                 "Borrowed", "Expected Return", "Returned", "Remarks"],
        rows=transaction_pdf_rows(txns),
        weights=[85, 50, 65, 135, 85, 85, 75, 85, 110],
        center=(1, 5, 6, 7),
        date_label="; ".join(filters) or "None (all records)",
        note="Times are shown in local time. Status Overdue means the key was not returned by "
             "its expected return time.",
    )
def _transaction_detail_context(txn):
    """Everything the detail modal shows beyond the row itself."""
    timeline = (
        ActivityLog.objects.filter(
            model_name="BorrowTransaction", object_id=str(txn.pk)
        )
        .select_related("user")
        .order_by("created_at")
    )
    return {
        "txn": txn,
        "timeline": timeline,
        # due_state() reads expected_return_at, which stays in the past on a
        # closed record: only a key that is still out can be overdue.
        "due_state": due_state(txn) if txn.status in OPEN_STATUSES else "ok",
        "statuses": BorrowTransaction.Status.choices,
    }


@admin_or_guard_required
def transaction_detail(request, pk):
    """Full record for one transaction: parties, timing, custody and the audit
    trail. Served as the modal partial for `?modal=1`/XHR and as a plain page
    otherwise, so the row stays openable without JS."""
    txn = get_object_or_404(
        BorrowTransaction.objects.select_related(
            "key__room__building", "key__category", "room__building",
            "borrower__department", "released_by", "received_by", "guard_shift__guard",
        ),
        pk=pk,
    )
    ctx = _transaction_detail_context(txn)
    if is_modal(request):
        return render(request, "transactions/_detail_modal.html", ctx)
    return render(request, "transactions/detail.html", ctx)


@admin_required
def transaction_correct(request, pk):
    """B4.6: only an ADMIN may amend a transaction record, and every correction
    is written to the audit log with a before/after diff and a reason. Guards
    have no edit path at all: they log records, they do not amend them.

    The status moves through services.admin_set_status() so the key is re-filed
    in the same database transaction; a move it refuses comes back as a 422 with
    the reason on the field, and the other edits are rolled back with it."""
    txn = get_object_or_404(
        BorrowTransaction.objects.select_related("key__room__building", "borrower"), pk=pk)
    form = TransactionCorrectionForm(request.POST or None, instance=txn)
    modal = is_modal(request)
    ctx = {"form": form, "txn": txn, "is_modal": modal,
           "title": f"Correct {txn.transaction_no}"}
    if request.method == "POST":
        if form.is_valid():
            changes = {
                name: [str(form.initial.get(name) or ""), str(form.cleaned_data.get(name) or "")]
                for name in form.changed_data if name not in ("reason", "status")
            }
            new_status = form.cleaned_data.get("status") or ""
            try:
                with db_transaction.atomic():
                    form.save()
                    if new_status:
                        _, status_changes = admin_set_status(
                            transaction_id=txn.pk, new_status=new_status,
                            admin=request.user)
                        changes.update(status_changes)
            except BorrowError as e:
                form.add_error("status", str(e))
                if modal:
                    return render(request, "transactions/_correction_modal.html", ctx,
                                  status=422)
                messages.error(request, str(e))
                return render(request, "transactions/correction_form.html", ctx)
            txn.refresh_from_db()
            log_activity(request.user, Action.UPDATE, obj=txn, request=request,
                         description=f"Correction: {form.cleaned_data['reason']}",
                         changes=changes)
            msg = f"{txn.transaction_no} corrected."
            if modal:
                return HttpResponse(msg, status=200)
            messages.success(request, msg)
            return redirect("transactions:history")
        if modal:
            return render(request, "transactions/_correction_modal.html", ctx, status=422)
    if modal:
        return render(request, "transactions/_correction_modal.html", ctx)
    return render(request, "transactions/correction_form.html", ctx)
