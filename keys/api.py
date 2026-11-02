"""Guard POS key endpoints (B3.4): read-only, JSON, GUARD-gated."""
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


@api_role_required("GUARD")
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
    return JsonResponse({
        "results": [serialize_key(k) for k in keys],
        "exact": exact,
        "query": q,
    })


@api_role_required("GUARD")
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
    check["enforcement"] = settings.SCHEDULE_ENFORCEMENT
    data["schedule"] = check
    return JsonResponse(data)
