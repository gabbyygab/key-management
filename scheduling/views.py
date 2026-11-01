from datetime import timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction as db_transaction
from django.db.models import Exists, OuterRef, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.api import api_role_required, read_body
from accounts.crud import is_modal, querystring_without_page
from accounts.models import Borrower
from accounts.permissions import admin_required
from audit.services import Action, form_changes, log_activity
from facilities.models import Building, Room
from .forms import FreeRoomQueryForm, ScheduleBlockForm, ScheduleExceptionForm
from .models import ScheduleBlock, ScheduleException, Subject, Term
from .services import (
    find_conflicts, find_free_rooms, get_room_timetable, get_room_today_schedule,
)

DURATIONS = [(60, "1h"), (90, "1.5h"), (120, "2h"), (180, "3h")]
DAY_LABELS = [(str(v), label) for v, label in ScheduleBlock.DayOfWeek.choices]


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
    term = d.get("term") or Term.objects.filter(is_active=True).order_by("-start_date").first()
    return find_free_rooms(day_of_week=d["day"], start_time=d["start"], end_time=d["end"],
                           term=term, building=d["building"], room_type=d["room_type"],
                           min_capacity=d["min_capacity"])


def _form_context(form=None, **initial):
    """Everything the shared schedule-block modal needs."""
    terms = Term.objects.all().order_by("-is_active", "-start_date")
    if form is None:
        form = ScheduleBlockForm(initial={"is_active": True, "start_time": "09:00",
                                          "end_time": "10:30", "day_of_week": 0, **initial})
    return {"form": form, "terms": terms}


def _block_queryset():
    return (ScheduleBlock.objects
            .select_related("term", "room__building", "subject", "instructor"))


def _conflicting_ids():
    """Active blocks that overlap another active block in the same room, day
    and term — one query (correlated EXISTS), not a loop."""
    overlap = ScheduleBlock.objects.filter(
        room=OuterRef("room"), term=OuterRef("term"), day_of_week=OuterRef("day_of_week"),
        is_active=True, start_time__lt=OuterRef("end_time"), end_time__gt=OuterRef("start_time"),
    ).exclude(pk=OuterRef("pk"))
    return (ScheduleBlock.objects.filter(is_active=True)
            .annotate(clash=Exists(overlap)).filter(clash=True).values_list("pk", flat=True))


@admin_required
def schedule_list(request):
    """F5.4 table view of real ScheduleBlocks, with filters + bulk toggle.

    The "Conflicts" sidebar entry lands here with ?conflicts=1, which narrows
    the list to blocks that overlap another active block (only possible for
    rows created before validation existed, or via raw imports)."""
    if request.method == "POST":
        return _bulk_toggle(request)

    term = request.GET.get("term", "").strip()
    building = request.GET.get("building", "").strip()
    room = request.GET.get("room", "").strip()
    day = request.GET.get("day", "").strip()
    instructor = request.GET.get("instructor", "").strip()
    state = request.GET.get("state", "").strip()
    only_conflicts = (request.GET.get("conflicts") == "1"
                      or request.resolver_match.url_name == "conflicts")

    blocks = _block_queryset()
    if term.isdigit():
        blocks = blocks.filter(term_id=int(term))
    if building.isdigit():
        blocks = blocks.filter(room__building_id=int(building))
    if room.isdigit():
        blocks = blocks.filter(room_id=int(room))
    if day.isdigit():
        blocks = blocks.filter(day_of_week=int(day))
    if instructor:
        blocks = blocks.filter(Q(instructor__first_name__icontains=instructor)
                               | Q(instructor__last_name__icontains=instructor)
                               | Q(instructor__id_number__icontains=instructor))
    if state == "active":
        blocks = blocks.filter(is_active=True)
    elif state == "inactive":
        blocks = blocks.filter(is_active=False)
    conflict_ids = set(_conflicting_ids())
    if only_conflicts:
        blocks = blocks.filter(pk__in=conflict_ids)
    blocks = blocks.order_by("term__start_date", "day_of_week", "start_time", "room__room_code")

    page_obj = Paginator(blocks, 30).get_page(request.GET.get("page"))
    for b in page_obj:
        b.has_conflict = b.pk in conflict_ids

    ctx = {
        "blocks": page_obj,
        "page_obj": page_obj,
        "querystring": querystring_without_page(request),
        "term": term, "building": building, "room": room, "day": day,
        "instructor": instructor, "state": state, "only_conflicts": only_conflicts,
        "conflict_count": len(conflict_ids),
        "buildings": Building.objects.filter(is_active=True).order_by("code"),
        "rooms": Room.objects.filter(is_active=True).select_related("building")
                     .order_by("building__code", "room_code"),
        "days": DAY_LABELS,
        **_form_context(),
    }
    return render(request, "scheduling/schedule_list.html", ctx)


def _bulk_toggle(request):
    action = request.POST.get("bulk_action")
    ids = [int(i) for i in request.POST.getlist("selected_schedule") if i.isdigit()]
    if action not in {"activate", "deactivate"}:
        messages.warning(request, "Choose an action before submitting.")
    elif not ids:
        messages.warning(request, "Select at least one schedule block to apply the action.")
    else:
        activate = action == "activate"
        skipped = []
        changed = 0
        with db_transaction.atomic():
            for block in ScheduleBlock.objects.select_for_update().filter(pk__in=ids):
                if block.is_active == activate:
                    continue
                if activate and find_conflicts(block.room, block.day_of_week, block.start_time,
                                               block.end_time, block.term,
                                               exclude_block_id=block.pk):
                    skipped.append(block.title)
                    continue
                block.is_active = activate
                block.save(update_fields=["is_active", "updated_at"])
                changed += 1
                log_activity(request.user, Action.UPDATE, obj=block, request=request,
                             description=f"Schedule block '{block.title}' {action}d (bulk).")
        messages.success(request, f"{changed} schedule block(s) {action}d.")
        if skipped:
            messages.warning(request, "Not activated because they would conflict: "
                                      + ", ".join(skipped))
    url = reverse("scheduling:schedule_list")
    qs = request.POST.get("return_qs", "")
    return redirect(f"{url}?{qs}" if qs else url)


@admin_required
@require_POST
def schedule_toggle(request, pk):
    block = get_object_or_404(_block_queryset(), pk=pk)
    if not block.is_active:
        clash = find_conflicts(block.room, block.day_of_week, block.start_time, block.end_time,
                               block.term, exclude_block_id=block.pk)
        if clash:
            messages.error(request, f"Cannot reactivate '{block.title}': it overlaps "
                                    + ", ".join(c.title for c in clash) + ".")
            return redirect("scheduling:schedule_list")
    block.is_active = not block.is_active
    block.save(update_fields=["is_active", "updated_at"])
    state = "activated" if block.is_active else "deactivated"
    log_activity(request.user, Action.UPDATE, obj=block, request=request,
                 description=f"Schedule block '{block.title}' {state}.")
    messages.success(request, f"'{block.title}' {state}.")
    nxt = request.POST.get("next", "")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
        return redirect(nxt)
    return redirect("scheduling:schedule_list")


@admin_required
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
    if term_param.isdigit():
        selected_term = terms.filter(pk=term_param).first()
    if not selected_term:
        selected_term = terms.filter(is_active=True).first() or terms.first()

    room_param = request.GET.get("room", "").strip()
    selected_room = None
    if room_param.isdigit():
        selected_room = rooms.filter(pk=room_param).first()
    if not selected_room:
        selected_room = rooms.first()

    form = None

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
            log_activity(request.user, Action.UPDATE if instance else Action.CREATE, obj=obj,
                         request=request, description=f"Schedule block '{obj.title}' saved.",
                         changes=form_changes(form) if instance else None)
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

    if form is None:
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
        "form_invalid": form.is_bound and bool(form.errors),
    }
    return render(request, "scheduling/room_timetable.html", ctx)


@api_role_required("ADMIN")
@require_POST
def conflict_check_api(request):
    """
    POST /api/scheduling/check-conflict/
    Payload: {room, day_of_week, start_time, end_time, term, exclude_block_id}
    Returns: {available: bool, conflicts: [...]}
    """
    try:
        data = read_body(request)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=422)

    room_id = data.get("room")
    term_id = data.get("term")
    day_of_week = data.get("day_of_week")
    start_time = data.get("start_time")
    end_time = data.get("end_time")
    exclude_block_id = data.get("exclude_block_id") or None

    if not all([room_id, term_id, day_of_week not in (None, ""), start_time, end_time]):
        return JsonResponse({"error": "Missing required fields"}, status=422)

    try:
        room = Room.objects.get(pk=room_id)
        term = Term.objects.get(pk=term_id)
        day_of_week = int(day_of_week)
    except (Room.DoesNotExist, Term.DoesNotExist, ValueError, TypeError):
        return JsonResponse({"error": "Invalid room, term, or day"}, status=422)

    # Client error check if end <= start ("HH:MM" strings compare correctly)
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

    return JsonResponse({
        "available": len(conflicts) == 0,
        "conflicts": [_conflict_payload(c) for c in conflicts],
    })


def _conflict_payload(c):
    return {
        "id": c.pk,
        "title": c.title,
        "instructor": c.instructor.full_name if c.instructor else "",
        "start_time": c.start_time.strftime("%H:%M"),
        "end_time": c.end_time.strftime("%H:%M"),
        "day_of_week": c.get_day_of_week_display(),
    }


@api_role_required("ADMIN")
@require_POST
def schedule_block_save_api(request):
    """
    POST /api/scheduling/blocks/
    Creates or updates a ScheduleBlock via JSON or form data.

    Optional "repeat_days": [int, ...] creates one block per extra day (F5.2
    "Repeat on"). Every copy is validated; any conflict rolls the whole batch
    back and answers 422 with the conflicting blocks.
    """
    try:
        data = read_body(request)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=422)

    block_id = data.get("block_id") or data.get("id")
    instance = None
    if block_id:
        instance = ScheduleBlock.objects.filter(pk=block_id).first()
        if instance is None:
            return JsonResponse({"error": "Schedule block not found."}, status=404)

    form = ScheduleBlockForm(data, instance=instance)
    if not form.is_valid():
        return JsonResponse(form.errors, status=422)

    repeat_days = []
    if not instance:
        raw = data.get("repeat_days") or []
        if hasattr(data, "getlist"):
            raw = data.getlist("repeat_days")
        try:
            repeat_days = sorted({int(d) for d in raw} - {form.cleaned_data["day_of_week"]})
        except (TypeError, ValueError):
            return JsonResponse({"repeat_days": ["Invalid day."]}, status=422)
        if any(d < 0 or d > 6 for d in repeat_days):
            return JsonResponse({"repeat_days": ["Invalid day."]}, status=422)

    with db_transaction.atomic():
        obj = form.save(commit=False)
        if not instance and not obj.created_by:
            obj.created_by = request.user
        obj.save()
        created = [obj]
        for day in repeat_days:
            clash = find_conflicts(obj.room, day, obj.start_time, obj.end_time, obj.term)
            if clash:
                db_transaction.set_rollback(True)
                return JsonResponse({
                    "error": f"Repeat on {ScheduleBlock.DayOfWeek(day).label} conflicts with: "
                             + ", ".join(f"{c.title} ({c.start_time:%H:%M}–{c.end_time:%H:%M})"
                                         for c in clash),
                    "conflicts": [_conflict_payload(c) for c in clash],
                }, status=422)
            copy = ScheduleBlock.objects.create(
                term=obj.term, room=obj.room, subject=obj.subject, title=obj.title,
                instructor=obj.instructor, day_of_week=day, start_time=obj.start_time,
                end_time=obj.end_time, is_active=obj.is_active, created_by=request.user)
            created.append(copy)

    for b in created:
        log_activity(request.user, Action.UPDATE if instance else Action.CREATE, obj=b,
                     request=request,
                     description=f"Schedule block '{b.title}' "
                                 f"({b.get_day_of_week_display()} {b.start_time:%H:%M}–{b.end_time:%H:%M}) "
                                 f"{'updated' if instance else 'created'}.",
                     changes=form_changes(form) if instance else None)

    status_code = 200 if instance else 201
    return JsonResponse({
        "success": True,
        "created": len(created),
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
@require_POST
def schedule_block_delete_api(request, pk):
    """
    POST /api/scheduling/blocks/<int:pk>/delete/

    Soft delete: the block is deactivated (it drops off the timetable and out
    of conflict checks) but stays in the database with its exceptions and the
    audit trail. Reactivate it from the schedule list.
    """
    block = ScheduleBlock.objects.filter(pk=pk).first()
    if not block:
        return JsonResponse({"error": "Schedule block not found."}, status=404)

    block.is_active = False
    block.save(update_fields=["is_active", "updated_at"])
    log_activity(request.user, Action.DELETE, obj=block, request=request,
                 description=f"Schedule block '{block.title}' removed from the timetable "
                             "(deactivated).")
    return JsonResponse({"success": True, "is_active": False})


@admin_required
def exception_create(request, pk):
    """F5.5: cancel / move / change the room of one date's meeting."""
    block = get_object_or_404(_block_queryset(), pk=pk)
    initial = {}
    if request.GET.get("date"):
        initial["date"] = request.GET["date"]
    form = ScheduleExceptionForm(request.POST or None, block=block, initial=initial)
    modal = is_modal(request)
    ctx = {"form": form, "block": block, "is_modal": modal,
           "title": f"Exception · {block.title}",
           "exceptions": block.exceptions.select_related("replacement_room__building")
                              .order_by("-date")[:10]}
    if request.method == "POST":
        if form.is_valid():
            exc = form.save(commit=False)
            exc.schedule_block = block
            exc.save()
            log_activity(request.user, Action.UPDATE, obj=block, request=request,
                         description=f"{exc.get_exception_type_display()} on {exc.date:%Y-%m-%d}"
                                     f" for '{block.title}'. {exc.reason}".strip())
            msg = f"{exc.get_exception_type_display()} recorded for {exc.date:%b %d}."
            if modal:
                return HttpResponse(msg, status=201)
            messages.success(request, msg)
            return redirect("scheduling:schedule_list")
        if modal:
            return render(request, "scheduling/_exception_modal.html", ctx, status=422)
    if modal:
        return render(request, "scheduling/_exception_modal.html", ctx)
    return render(request, "scheduling/exception_form.html", ctx)


@admin_required
def free_rooms(request):
    # GET with filters renders results server-side too (no-JS fallback).
    if "day" in request.GET:
        form = FreeRoomQueryForm(request.GET)
        rooms = _search(form) if form.is_valid() else None
    else:
        form = FreeRoomQueryForm(initial=_default_slot())
        rooms = None
    return render(request, "scheduling/free_rooms.html", {
        "form": form, "rooms": rooms, "durations": DURATIONS, **_form_context(),
    })


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
    return JsonResponse({"results": results, "count": len(results)})


@api_role_required("ADMIN", "GUARD")
def room_today_schedule_api(request, pk):
    """
    GET /api/scheduling/rooms/<pk>/today/
    Returns JSON and rendered HTML partial for the 'today in this room' widget.
    Accessible to admins and guards (for POS screen key selection).
    """
    from django.template.loader import render_to_string

    room = Room.objects.select_related("building").filter(pk=pk).first()
    if not room:
        return JsonResponse({"error": "Room not found."}, status=404)

    schedule = get_room_today_schedule(room)
    cb = schedule.get("current_block")
    nb = schedule.get("next_block")

    html = render_to_string("scheduling/_today_in_room.html", {
        "schedule": schedule,
        "read_only": True,
        "compact": True,
        "user": request.user,
    }, request=request)

    return JsonResponse({
        "room_id": room.pk,
        "room_code": room.room_code,
        "building_code": room.building.code,
        "room_label": f"{room.building.code}-{room.room_code}",
        "date": schedule["date"].isoformat(),
        "date_str": schedule["date_str"],
        "day_name": schedule["day_name"],
        "current_time": schedule["current_time_str"],
        "is_occupied": schedule["is_occupied"],
        "has_schedule_today": schedule["has_schedule_today"],
        "all_day_free": schedule["all_day_free"],
        "done_for_day": schedule["done_for_day"],
        "current_block": {
            "id": cb["id"],
            "title": cb["title"],
            "display_title": cb["display_title"],
            "subject_code": cb["subject_code"],
            "instructor": cb["instructor_name"],
            "start_time": cb["start_time_str"],
            "end_time": cb["end_time_str"],
            "time_display": cb["time_display"],
            "time_remaining_min": cb["time_remaining_min"],
        } if cb else None,
        "next_block": {
            "id": nb["id"],
            "title": nb["title"],
            "display_title": nb["display_title"],
            "subject_code": nb["subject_code"],
            "instructor": nb["instructor_name"],
            "start_time": nb["start_time_str"],
            "end_time": nb["end_time_str"],
            "time_display": nb["time_display"],
            "time_until_start_min": nb["time_until_start_min"],
        } if nb else None,
        "html": html,
    })
