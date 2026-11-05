"""POS key endpoints (B3.4): read-only, JSON, GUARD/ADMIN-gated."""
from django.conf import settings
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from accounts.api import api_role_required
from .models import Key


def serialize_key(k):
    room = k.room
    return {
        "id": k.pk,
        "key_code": k.key_code,
        "room_id": room.pk,
        "room": f"{room.building.code}-{room.room_code}",
        "room_name": room.name,
        "building": room.building.code,
        "building_id": room.building_id,
        "category": k.category.name,
        "copy_number": k.copy_number,
        "is_original": k.is_original,
        "status": k.status,
        "status_display": k.get_status_display(),
        "is_available": k.is_available,
        "description": k.description,
    }


def room_holders(room_ids):
    """{room_id: {key_code, borrower, transaction_no}} for rooms with a key out.

    One query for the whole result list. The earliest open transaction wins, so
    the warning names who actually has the room.
    """
    from transactions.models import BorrowTransaction

    open_rows = (BorrowTransaction.objects
                 .filter(room_id__in=set(room_ids),
                         status__in=[BorrowTransaction.Status.ACTIVE,
                                     BorrowTransaction.Status.OVERDUE])
                 .select_related("key", "borrower")
                 .order_by("-borrowed_at"))
    holders = {}
    for t in open_rows:  # newest first, so the earliest overwrites last
        holders[t.room_id] = {
            "key_code": t.key.key_code,
            "borrower": t.borrower.full_name,
            "transaction_no": t.transaction_no,
        }
    return holders


@api_role_required("GUARD", "ADMIN")
def key_search(request):
    """GET /api/keys/search/?q=&building= — available keys only, max 30.

    A scanned key code arrives as q; an exact (case-insensitive) code match is
    flagged with "exact" so the POS can auto-select it on Enter.
    """
    q = request.GET.get("q", "").strip()
    building = request.GET.get("building", "").strip()
    qs = (Key.objects.select_related("room__building", "category")
          .filter(status=Key.Status.AVAILABLE, room__is_active=True))
    if q:
        qs = qs.filter(Q(key_code__icontains=q) | Q(room__room_code__icontains=q)
                       | Q(room__building__code__icontains=q) | Q(room__name__icontains=q))
    if building.isdigit():
        qs = qs.filter(room__building_id=int(building))
    keys = list(qs.order_by("room__building__code", "room__room_code", "copy_number")[:30])
    exact = next((k.pk for k in keys if q and k.key_code.lower() == q.lower()), None)
    holders = room_holders(k.room_id for k in keys)
    results = []
    for k in keys:
        row = serialize_key(k)
        row["room_holder"] = holders.get(k.room_id)
        results.append(row)
    return JsonResponse({
        "results": results,
        "exact": exact,
        "query": q,
    })


@api_role_required("GUARD", "ADMIN")
def key_detail(request, pk):
    """GET /api/keys/<pk>/ — one key plus today's schedule for its room, for
    the POS confirm panel (F3.5 / F5.6)."""
    from scheduling.services import schedule_check_for_room

    key = get_object_or_404(Key.objects.select_related("room__building", "category"), pk=pk)
    data = serialize_key(key)
    borrower_id = request.GET.get("borrower", "")
    check = schedule_check_for_room(
        key.room, borrower_id=int(borrower_id) if borrower_id.isdigit() else None)
    check.pop("_expected_return_dt", None)
    from scheduling.services import get_schedule_settings
    check["enforcement"] = get_schedule_settings().enforcement
    data["schedule"] = check
    data["room_holder"] = room_holders([key.room_id]).get(key.room_id)
    return JsonResponse(data)
