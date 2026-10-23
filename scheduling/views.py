from datetime import timedelta
from functools import wraps

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from accounts.decorators import role_required
from facilities.models import Building, Room
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


def _sample_schedule_rows():
    """Fallback schedule rows used until scheduling models are implemented."""
    return [
        {
            "pk": 1,
            "term": "2026 Fall",
            "room": "CCS-204",
            "building": "CCS",
            "subject": "CS 101",
            "title": "Intro to Programming",
            "instructor": "P. Nguyen",
            "day": 0,
            "day_label": "Mon",
            "start_time": "09:00",
            "end_time": "10:30",
            "is_active": True,
        },
        {
            "pk": 2,
            "term": "2026 Fall",
            "room": "ENG-118",
            "building": "ENG",
            "subject": "MTH 210",
            "title": "Discrete Math",
            "instructor": "A. Patel",
            "day": 2,
            "day_label": "Wed",
            "start_time": "13:00",
            "end_time": "14:30",
            "is_active": True,
        },
        {
            "pk": 3,
            "term": "2027 Spring",
            "room": "LAB-3",
            "building": "SCI",
            "subject": "BIO 220",
            "title": "Lab II",
            "instructor": "R. Garcia",
            "day": 4,
            "day_label": "Fri",
            "start_time": "10:00",
            "end_time": "12:00",
            "is_active": False,
        },
    ]


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
def schedule_list(request):
    rows = _sample_schedule_rows()

    if request.method == "POST":
        action = request.POST.get("bulk_action")
        selected = request.POST.getlist("selected_schedule")
        if action in {"activate", "deactivate"}:
            if selected:
                messages.success(request, f"{len(selected)} schedule block(s) marked {action}.")
            else:
                messages.warning(request, "Select at least one schedule block to apply the action.")
        else:
            messages.warning(request, "Choose an action before submitting.")
        return redirect("scheduling:schedule_list")

    term = request.GET.get("term", "")
    building = request.GET.get("building", "")
    room = request.GET.get("room", "")
    day = request.GET.get("day", "")
    instructor = (request.GET.get("instructor", "") or "").strip()

    filtered = []
    for row in rows:
        if term and row["term"] != term:
            continue
        if building and row["building"] != building:
            continue
        if room and row["room"] != room:
            continue
        if day and str(row["day"]) != str(day):
            continue
        if instructor and instructor.lower() not in row["instructor"].lower():
            continue
        filtered.append(row)

    building_choices = list(Building.objects.filter(is_active=True).values_list("code", "name"))
    room_choices = list(Room.objects.filter(is_active=True).values_list("room_code", "room_code"))
    day_choices = [("", "Any day")] + [
        (str(i), label) for i, label in enumerate(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])
    ]

    ctx = {
        "rows": filtered,
        "term": term,
        "building": building,
        "room": room,
        "day": day,
        "instructor": instructor,
        "terms": ["2026 Fall", "2027 Spring", "2027 Summer"],
        "buildings": [
            {"code": code, "name": name} for code, name in building_choices
        ] + [{"code": "CCS", "name": "Computing"}, {"code": "ENG", "name": "Engineering"}],
        "rooms": [
            {"code": code, "label": label} for code, label in room_choices
        ] + [{"code": "CCS-204", "label": "CCS-204"}, {"code": "ENG-118", "label": "ENG-118"}],
        "days": day_choices,
    }
    return render(request, "scheduling/schedule_list.html", ctx)


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
