from datetime import timedelta
from functools import wraps

from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from accounts.decorators import role_required
from .forms import FreeRoomQueryForm
from .services import find_free_rooms


def api_role_required(*roles):
    """role_required for JSON endpoints: 403 JSON, never a login redirect."""
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            user = request.user
            if user.is_authenticated and (user.is_superuser or user.role in roles):
                return view(request, *args, **kwargs)
            return JsonResponse({"error": "You do not have permission."}, status=403)
        return wrapper
    return decorator


DURATIONS = [(60, "1h"), (90, "1.5h"), (120, "2h"), (180, "3h")]


def _default_slot():
    """Today, from the next half hour, for one hour."""
    now = timezone.localtime()
    start = (now + timedelta(minutes=30 - now.minute % 30)).replace(second=0, microsecond=0)
    if start.date() != now.date() or start.hour >= 22:
        start = start.replace(hour=7, minute=0)
    end = start + timedelta(hours=1)
    return {"day": now.weekday(), "start": start.strftime("%H:%M"), "end": end.strftime("%H:%M")}


def _search(form):
    d = form.cleaned_data
    return find_free_rooms(day_of_week=d["day"], start_time=d["start"], end_time=d["end"],
                           building=d["building"], room_type=d["room_type"],
                           min_capacity=d["min_capacity"])


@role_required("ADMIN")
def free_rooms(request):
    # GET with filters renders results server-side too (no-JS fallback).
    if "day" in request.GET:
        form = FreeRoomQueryForm(request.GET)
        rooms = _search(form) if form.is_valid() else None
    else:
        form = FreeRoomQueryForm(initial=_default_slot())
        rooms = None
    return render(request, "scheduling/free_rooms.html", {"form": form, "rooms": rooms, "durations": DURATIONS})


@api_role_required("ADMIN")
def free_rooms_api(request):
    form = FreeRoomQueryForm(request.GET)
    if not form.is_valid():
        return JsonResponse(form.errors, status=422)
    results = [{
        "id": r.pk,
        "room_code": r.room_code,
        "name": r.name,
        "building": {"id": r.building_id, "code": r.building.code, "name": r.building.name},
        "room_type": r.room_type,
        "room_type_display": r.get_room_type_display(),
        "capacity": r.capacity,
        "floor": r.floor,
    } for r in _search(form)]
    return JsonResponse({"results": results, "count": len(results), "stub": True})
