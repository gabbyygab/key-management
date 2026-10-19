"""JSON endpoints for the guard POS screen.

Mounted at /api/transactions/ (see transactions/api_urls.py). Only the two
endpoints the "Currently Out" panel needs live here (backend B3.4's
/api/transactions/active/ and B4.2's return endpoint); the borrower and key
search endpoints belong to their own apps.

Contract, per GUIDE.md §4: JsonResponse, select_related, 403 (not 302) for the
wrong role, 422 for a business-rule violation as {"error": "<message>"},
CSRF on, no user emails in the payload.
"""
import json
from functools import wraps

from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from .models import BorrowTransaction
from .services import (
    BorrowError, RETURN_CONDITIONS, active_transactions, due_state,
    return_key_with_condition,
)


def guard_api(view):
    """role_required("GUARD") for a JSON path.

    Same role check as every other view, but a wrong role or an expired session
    answers 403 JSON instead of login_required's redirect, so the POS panel can
    tell "no permission" from "server said something weird".
    """
    gated = role_required("GUARD")(view)

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "Your session has expired. Sign in again."},
                                status=403)
        try:
            return gated(request, *args, **kwargs)
        except PermissionDenied:
            return JsonResponse({"error": "You do not have permission."}, status=403)
    return wrapper


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


@guard_api
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


@guard_api
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
