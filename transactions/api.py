"""JSON endpoints for the POS screen.

Mounted at /api/transactions/ (see transactions/api_urls.py). Only the two
endpoints the "Currently Out" panel needs live here (backend B3.4's
/api/transactions/active/ and B4.2's return endpoint); the borrower and key
search endpoints belong to their own apps.

Contract, per GUIDE.md §4: JsonResponse, select_related, 403 (not 302) for the
wrong role, 422 for a business-rule violation as {"error": "<message>"},
CSRF on, no user emails in the payload.
"""
import json

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.api import api_role_required, read_body
from accounts.models import Borrower
from .models import BorrowTransaction
from .services import (
    BorrowError, RETURN_CONDITIONS, ScheduleWarning, active_transactions, due_state,
    log_return, parse_due, pos_borrow, return_key_with_condition,
)

# role_required("GUARD", "ADMIN") for a JSON path: a wrong role or an expired
# session answers 403 JSON instead of login_required's redirect, so the POS
# panel can tell "no permission" from "server said something weird". Admins use
# the same POS as guards, so they are allowed here too.
pos_api = api_role_required("GUARD", "ADMIN")


def _serialize(txn, *, now):
    room = txn.key.room
    borrower = txn.borrower
    return {
        "id": txn.pk,
        "transaction_no": txn.transaction_no,
        "key_code": txn.key.key_code,
        "room": str(room),
        "room_name": room.name,
        "building": room.building.code,
        "category": txn.key.category.name,
        "borrower": {
            "id": borrower.pk,
            "full_name": borrower.full_name,
            "sort_name": f"{borrower.last_name}, {borrower.first_name}",
            "id_number": borrower.id_number,
            "type": borrower.get_borrower_type_display(),
            "department": borrower.department.code if borrower.department else "",
        },
        "purpose": txn.purpose,
        "borrowed_at": txn.borrowed_at.isoformat(),
        "expected_return_at": (txn.expected_return_at.isoformat()
                               if txn.expected_return_at else None),
        "elapsed_seconds": int((now - txn.borrowed_at).total_seconds()),
        "due_state": due_state(txn, now=now),
        "released_by": txn.released_by.get_full_name() or txn.released_by.username,
    }


def _counts(states):
    return {
        "count": len(states),
        "overdue_count": states.count("overdue"),
        "due_soon_count": states.count("due_soon"),
    }


def panel_counts(*, now=None):
    """Counters over every out key, ignoring the panel's own search filter."""
    now = now or timezone.now()
    return _counts([due_state(t, now=now) for t in active_transactions()])


@pos_api
def pos_snapshot(request):
    """GET /api/transactions/pos-snapshot/ — keys, borrowers and today's room
    schedules for the POS to filter in the browser (see pos_snapshot.py)."""
    from .pos_snapshot import build_snapshot
    return JsonResponse(build_snapshot())


@pos_api
def active_list(request):
    """GET /api/transactions/active/?q= — currently-out keys + panel counters."""
    now = timezone.now()
    rows = [_serialize(t, now=now)
            for t in active_transactions(q=request.GET.get("q", "").strip())]
    return JsonResponse({
        "results": rows,
        "server_time": now.isoformat(),
        "matched": len(rows),
        **panel_counts(now=now),
    })


@pos_api
@require_POST
def transaction_return(request, pk):
    """POST /api/transactions/<pk>/return/ — body: remarks, condition."""
    if request.content_type == "application/json":
        try:
            body = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": "Malformed request body."}, status=422)
    else:
        body = request.POST

    condition = (body.get("condition") or "OK").upper()
    if condition not in RETURN_CONDITIONS:
        return JsonResponse({"error": f"Unknown key condition: {condition}."}, status=422)

    try:
        txn = return_key_with_condition(
            transaction_id=pk, guard=request.user,
            remarks=(body.get("remarks") or "").strip(), condition=condition)
    except BorrowTransaction.DoesNotExist:
        return JsonResponse({"error": "Transaction not found."}, status=404)
    except BorrowError as e:
        return JsonResponse({"error": str(e)}, status=422)

    log_return(txn, guard=request.user, condition=condition, request=request)
    return JsonResponse({
        "transaction_no": txn.transaction_no,
        "returned_at": txn.returned_at.isoformat(),
        "key_code": txn.key.key_code,
        "key_status": txn.key.status,
        "status": txn.status,
        "condition": condition,
        "borrower": txn.borrower.full_name,
        "counts": panel_counts(),
    })


@pos_api
@require_POST
def transaction_borrow(request):
    """POST /api/transactions/borrow/ — B4.1.

    Body: borrower_id, key_id, purpose, expected_return_at (optional),
    override_reason (required only when the room is scheduled to someone else).

    201 on success; 422 {"error"} for a business rule (no shift, unauthorized,
    already holding, key unavailable); 422 {"error", "schedule_warning": true,
    "schedule": {...}} when the timetable needs a typed override (F3.6).
    """
    try:
        body = read_body(request)
        due = parse_due(body.get("expected_return_at"))
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=422)

    borrower_id = str(body.get("borrower_id") or "")
    key_id = str(body.get("key_id") or "")
    if not borrower_id.isdigit() or not key_id.isdigit():
        return JsonResponse({"error": "Choose a borrower and a key first."}, status=422)

    borrower = (Borrower.objects.select_related("department")
                .filter(pk=int(borrower_id)).first())
    if borrower is None:
        return JsonResponse({"error": "Borrower not found."}, status=422)

    try:
        txn = pos_borrow(
            key_id=int(key_id), borrower=borrower, guard=request.user,
            purpose=(body.get("purpose") or "").strip()[:255],
            expected_return_at=due,
            override_reason=(body.get("override_reason") or "").strip(),
            request=request,
        )
    except ScheduleWarning as e:
        return JsonResponse({"error": str(e), "schedule_warning": True,
                             "schedule": e.schedule}, status=422)
    except BorrowError as e:
        return JsonResponse({"error": str(e)}, status=422)

    txn = (BorrowTransaction.objects
           .select_related("key__room__building", "key__category", "borrower__department",
                           "released_by")
           .get(pk=txn.pk))
    now = timezone.now()
    return JsonResponse({
        "transaction_no": txn.transaction_no,
        "key_code": txn.key.key_code,
        "borrower": borrower.full_name,
        "borrowed_at": txn.borrowed_at.isoformat(),
        "expected_return_at": (txn.expected_return_at.isoformat()
                               if txn.expected_return_at else None),
        "key_status": txn.key.status,
        "overridden": txn.remarks.startswith("Override:"),
        "row": _serialize(txn, now=now),
        "counts": panel_counts(now=now),
    }, status=201)
