"""JSON helpers shared by every /api/ endpoint, plus the borrower search.

Contract (guide.md §4): JsonResponse, 403 JSON (never a login redirect) for a
wrong role or expired session, 422 {"error": ...} for a business-rule failure,
no password hashes or user emails in any payload.
"""
import json
from functools import wraps

from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import JsonResponse

from .decorators import role_required
from .models import Borrower


def api_role_required(*roles):
    """role_required() for JSON paths: 403 JSON instead of a 302 to login."""
    def decorator(view):
        gated = role_required(*roles)(view)

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
    return decorator


def read_body(request):
    """JSON or form-encoded body as a dict-like. Raises ValueError on bad JSON."""
    if (request.content_type or "").startswith("application/json"):
        try:
            data = json.loads(request.body or b"{}")
        except json.JSONDecodeError as exc:
            raise ValueError("Malformed request body.") from exc
        if not isinstance(data, dict):
            raise ValueError("Request body must be a JSON object.")
        return data
    return request.POST


def serialize_borrower(b, *, active=None):
    """The POS borrower shape. `active` is the open transaction, if known."""
    return {
        "id": b.pk,
        "id_number": b.id_number,
        "full_name": b.full_name,
        "first_name": b.first_name,
        "last_name": b.last_name,
        "borrower_type": b.borrower_type,
        "borrower_type_display": b.get_borrower_type_display(),
        "department": b.department.code if b.department else "",
        "can_borrow": b.can_borrow,
        "is_authorized": b.is_authorized,
        "authorized_until": b.authorized_until.isoformat() if b.authorized_until else None,
        "active_transaction": ({
            "id": active.pk,
            "transaction_no": active.transaction_no,
            "key_code": active.key.key_code,
            "room": str(active.key.room),
            "borrowed_at": active.borrowed_at.isoformat(),
            "status": active.status,
        } if active else None),
    }


@api_role_required("GUARD")
def borrower_search(request):
    """GET /api/borrowers/search/?q= — B3.4. Active borrowers only, max 20."""
    from transactions.models import BorrowTransaction

    q = request.GET.get("q", "").strip()
    qs = Borrower.objects.select_related("department").filter(is_active=True)
    if q:
        qs = qs.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q)
                       | Q(id_number__icontains=q))
    borrowers = list(qs.order_by("last_name", "first_name")[:20])

    # One query for every open transaction among the results, not one each.
    open_txns = {
        t.borrower_id: t for t in BorrowTransaction.objects
        .filter(borrower__in=borrowers,
                status__in=[BorrowTransaction.Status.ACTIVE, BorrowTransaction.Status.OVERDUE])
        .select_related("key__room__building")
    }
    return JsonResponse({
        "results": [serialize_borrower(b, active=open_txns.get(b.pk)) for b in borrowers],
        "query": q,
    })
