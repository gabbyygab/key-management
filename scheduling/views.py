import json
from datetime import timedelta
from functools import wraps
from urllib.parse import urlencode

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.decorators import role_required
from accounts.models import Borrower
from facilities.models import Building, Room
from .forms import FreeRoomQueryForm, ScheduleBlockForm
from .models import ScheduleBlock, Subject, Term
from .services import find_conflicts, find_free_rooms, get_room_timetable


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
    """Fallback schedule rows used until scheduling models are populated."""
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
def room_timetable(request):
    """Weekly timetable grid for one room and term (F5.1)."""
    terms = Term.objects.all().order_by("-is_active", "-start_date")
    rooms = (
        Room.objects.filter(is_active=True, building__is_active=True)
        .select_related("building")
        .order_by("building__code", "room_code")
    )

    term_param = request.GET.get("term", "").strip()
    selected_term = None
    if term_param:
        selected_term = terms.filter(pk=term_param).first()
    if not selected_term:
        selected_term = terms.filter(is_active=True).first() or terms.first()

    room_param = request.GET.get("room", "").strip()
    selected_room = None
    if room_param:
        selected_room = rooms.filter(pk=room_param).first()
    if not selected_room:
        selected_room = rooms.first()

    # Progressive enhancement: handle form POST when JS is disabled
    if request.method == "POST":
        block_id = request.POST.get("block_id")
        instance = ScheduleBlock.objects.filter(pk=block_id).first() if block_id else None
        form = ScheduleBlockForm(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save(commit=False)
            if not instance and not obj.created_by:
                obj.created_by = request.user
            obj.save()
            messages.success(request, f"Schedule block '{obj.title}' saved successfully.")
            redirect_url = reverse("scheduling:room_timetable")
            params = urlencode({"room": obj.room_id, "term": obj.term_id})
            return redirect(f"{redirect_url}?{params}")
        else:
            messages.error(request, "Please correct the errors in the schedule form.")

    timetable_data = get_room_timetable(selected_room, selected_term)

    # 28 half-hour slots: 07:00 to 20:30 (ending at 21:00)
    time_slots = []
    for i in range(28):
        minute = 420 + i * 30
        h = minute // 60
        m = minute % 60
        time_slots.append({
            "index": i,
            "row": i + 1,
            "minute": minute,
            "label": f"{h:02d}:{m:02d}",
            "is_hour": (m == 0),
        })

    days = [
        {"code": 0, "col": 2, "short": "Mon", "name": "Monday"},
        {"code": 1, "col": 3, "short": "Tue", "name": "Tuesday"},
        {"code": 2, "col": 4, "short": "Wed", "name": "Wednesday"},
        {"code": 3, "col": 5, "short": "Thu", "name": "Thursday"},
        {"code": 4, "col": 6, "short": "Fri", "name": "Friday"},
        {"code": 5, "col": 7, "short": "Sat", "name": "Saturday"},
    ]

    subjects = Subject.objects.filter(is_active=True).order_by("code")
    borrowers = Borrower.objects.filter(is_active=True).order_by("last_name", "first_name")

    form = ScheduleBlockForm(initial={
        "term": selected_term.pk if selected_term else None,
        "room": selected_room.pk if selected_room else None,
        "start_time": "09:00",
        "end_time": "10:30",
        "day_of_week": 0,
        "is_active": True,
    })

    ctx = {
        "terms": terms,
        "selected_term": selected_term,
        "rooms": rooms,
        "selected_room": selected_room,
        "timetable": timetable_data,
        "time_slots": time_slots,
        "days": days,
        "subjects": subjects,
        "borrowers": borrowers,
        "form": form,
    }
    return render(request, "scheduling/room_timetable.html", ctx)


@api_role_required("ADMIN")
def conflict_check_api(request):
    """
    POST /api/scheduling/check-conflict/
    Payload: {room, day_of_week, start_time, end_time, term, exclude_block_id}
    Returns: {available: bool, conflicts: [...]}
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    data = None
    if request.content_type == "application/json" or (request.body and request.body.startswith(b"{")):
        try:
            data = json.loads(request.body.decode("utf-8"))
        except Exception:
            data = None
    if data is None:
        data = request.POST

    room_id = data.get("room")
    term_id = data.get("term")
    day_of_week = data.get("day_of_week")
    start_time = data.get("start_time")
    end_time = data.get("end_time")
    exclude_block_id = data.get("exclude_block_id") or None

    if not all([room_id, term_id, day_of_week is not None, start_time, end_time]):
        return JsonResponse({"error": "Missing required fields"}, status=422)

    try:
        room = Room.objects.get(pk=room_id)
        term = Term.objects.get(pk=term_id)
        day_of_week = int(day_of_week)
    except (Room.DoesNotExist, Term.DoesNotExist, ValueError):
        return JsonResponse({"error": "Invalid room, term, or day"}, status=422)

    # Client error check if end <= start
    if str(end_time) <= str(start_time):
        return JsonResponse({
            "available": False,
            "conflicts": [],
            "clientError": "End time must be after start time.",
        })

    conflicts = find_conflicts(
        room=room,
        day_of_week=day_of_week,
        start_time=start_time,
        end_time=end_time,
        term=term,
        exclude_block_id=exclude_block_id,
    )

    conflict_list = []
    for c in conflicts:
        conflict_list.append({
            "id": c.pk,
            "title": c.title,
            "instructor": c.instructor.full_name if c.instructor else "",
            "start_time": c.start_time.strftime("%H:%M"),
            "end_time": c.end_time.strftime("%H:%M"),
            "day_of_week": c.get_day_of_week_display(),
        })

    return JsonResponse({
        "available": len(conflicts) == 0,
        "conflicts": conflict_list,
    })


@api_role_required("ADMIN")
def schedule_block_save_api(request):
    """
    POST /api/scheduling/blocks/
    Creates or updates a ScheduleBlock via JSON or multipart/form-data.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    data = None
    if request.content_type == "application/json" or (request.body and request.body.startswith(b"{")):
        try:
            data = json.loads(request.body.decode("utf-8"))
        except Exception:
            data = None
    if data is None:
        data = request.POST

    block_id = data.get("block_id") or data.get("id")
    instance = None
    if block_id:
        instance = ScheduleBlock.objects.filter(pk=block_id).first()

    form = ScheduleBlockForm(data, instance=instance)
    if not form.is_valid():
        return JsonResponse(form.errors, status=422)

    obj = form.save(commit=False)
    if not instance and not obj.created_by:
        obj.created_by = request.user
    obj.save()

    status_code = 200 if instance else 201
    return JsonResponse({
        "success": True,
        "block": {
            "id": obj.pk,
            "title": obj.title,
            "day_of_week": obj.day_of_week,
            "start_time": obj.start_time.strftime("%H:%M"),
            "end_time": obj.end_time.strftime("%H:%M"),
            "is_active": obj.is_active,
        }
    }, status=status_code)


@api_role_required("ADMIN")
def schedule_block_delete_api(request, pk):
    """
    POST /api/scheduling/blocks/<int:pk>/delete/
    Deletes a ScheduleBlock.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    block = ScheduleBlock.objects.filter(pk=pk).first()
    if not block:
        return JsonResponse({"error": "Schedule block not found."}, status=404)

    block.delete()
    return JsonResponse({"success": True})


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
