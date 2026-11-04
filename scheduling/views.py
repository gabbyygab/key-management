from datetime import timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction as db_transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.api import api_role_required, read_body
from accounts.crud import (BULK_ACTIONS, bulk_delete_forever, bulk_response,
                           bulk_selection, bulk_summary, delete_forever,
                           form_view, is_modal, paginate, post_redirect,
                           querystring_without_page)
from accounts.models import Borrower, Department
from accounts.permissions import admin_required
from audit.services import Action, form_changes, log_activity
from facilities.models import Building, Room
from . import curriculum as curriculum_rules
from .forms import (AutoScheduleForm, BlockSwapForm, CurriculumAddForm, DepartmentForm, FreeRoomQueryForm,
                    ScheduleBlockForm, ScheduleExceptionForm, SectionForm,
                    SubjectForm, SubjectOfferingForm, TermForm)
from .models import (CurriculumSubject, ScheduleBlock, ScheduleException, Section, Subject,
                     SubjectOffering, Term)
from .planner import (PlaceError, SwapError, auto_schedule, clear_term_schedule,
                      export_section_timetable_csv, file_offering,
                      get_section_timetable, group_labels, move_block,
                      offering_queue, placeable_subjects, place_class,
                      restore_blocker, section_scope_ids,
                      shared_label, sharing_info, swap_blocks,
                      unschedule_offering)
from .services import (
    effective_term, faculty_schedule, find_conflicts, find_free_rooms, get_room_timetable,
    get_room_today_schedule, live_blocks,
)

DURATIONS = [(60, "1h"), (90, "1.5h"), (120, "2h"), (180, "3h")]
DAY_LABELS = [(str(v), label) for v, label in ScheduleBlock.DayOfWeek.choices]


def slot_label_12h(h, m):
    """Return a 12-hour slot label.
    On-the-hour shows AM/PM (e.g. '7:00 AM'); half-hours show just h:mm
    (e.g. '7:30') to keep the gutter compact.
    """
    hour12 = h % 12 or 12
    if m == 0:
        suffix = "AM" if h < 12 else "PM"
        return f"{hour12}:{m:02d} {suffix}"
    return f"{hour12}:{m:02d}"



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
    terms = Term.objects.filter(is_active=True).order_by("-start_date")
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


def _time_slots():
    """The 28 half-hour rows, 07:00 to 21:00, shared by both timetables.

    ``label`` is the 12-hour text for people; ``hhmm`` is the 24-hour value the
    cells carry for the scripts and the API."""
    slots = []
    for i in range(28):
        minute = 420 + i * 30
        h, m = divmod(minute, 60)
        slots.append({"index": i, "row": i + 1, "minute": minute,
                      "label": slot_label_12h(h, m), "hhmm": f"{h:02d}:{m:02d}",
                      "is_hour": m == 0})
    return slots


def _class_modal_context():
    """Pickers the add/edit class modal needs (it is shared by both grids)."""
    return {
        "modal_sections": _section_options(
            Section.objects.filter(is_active=True).select_related("department")
            .order_by("program", "year_level", "block", "group")),
        "modal_rooms": (Room.objects.filter(is_active=True, building__is_active=True)
                        .exclude(status__in=[Room.Status.MAINTENANCE, Room.Status.INACTIVE])
                        .select_related("building").order_by("building__code", "room_code")),
        "modal_borrowers": Borrower.objects.filter(is_active=True)
                                           .order_by("last_name", "first_name"),
        "modal_days": [(c, l) for c, l in ScheduleBlock.DayOfWeek.choices if c <= 5],
        "options_url": reverse("scheduling:class_options"),
        "place_url": reverse("scheduling:class_place"),
        "move_url": reverse("scheduling:block_move", args=[0]),
        "delete_url": reverse("scheduling:schedule_block_delete", args=[0]),
        "swap_url": reverse("scheduling:block_swap"),
    }


@admin_required
def schedule_legacy(request):
    """/scheduling/ used to be the schedule list; the room schedule and the
    section timetable replaced it, so old links land on the room schedule."""
    return redirect("scheduling:room_timetable")


@admin_required
def room_timetable(request):
    """Weekly timetable grid for one room and term (F5.1).

    Classes are added by section + curriculum subject, edited from the card and
    moved by drag and drop; every change goes through ``planner`` so the
    section timetable and the subject offerings stay in step.
    """
    terms = Term.objects.filter(is_active=True).order_by("-start_date")
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

    timetable_data = get_room_timetable(selected_room, selected_term)

    days = [
        {"code": 0, "col": 2, "short": "Mon", "name": "Monday"},
        {"code": 1, "col": 3, "short": "Tue", "name": "Tuesday"},
        {"code": 2, "col": 4, "short": "Wed", "name": "Wednesday"},
        {"code": 3, "col": 5, "short": "Thu", "name": "Thursday"},
        {"code": 4, "col": 6, "short": "Fri", "name": "Friday"},
        {"code": 5, "col": 7, "short": "Sat", "name": "Saturday"},
    ]

    ctx = {
        "terms": terms,
        "selected_term": selected_term,
        "rooms": rooms,
        "selected_room": selected_room,
        "timetable": timetable_data,
        "time_slots": _time_slots(),
        "days": days,
        **_class_modal_context(),
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
            return redirect("scheduling:room_timetable")
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
    _, selected_term = _selected_term(request)
    return render(request, "scheduling/free_rooms.html", {
        "form": form, "rooms": rooms, "durations": DURATIONS, **_form_context(),
        "selected_term": selected_term, **_class_modal_context(),
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


# ===========================================================================
# Subjects, sections and the offering queue
#
# The class-timetable workflow, in the order an admin walks it:
#
#   1. Subjects   - the catalogue (subject_list).
#   2. Sections   - BSIT-4D and its groups BSIT-4DG1 / BSIT-4DG2 (section_list).
#   3. Offerings  - "this subject, to this section, by this professor", queued
#                   first come first served (offering_list).
#   4. Auto-run   - planner.auto_schedule turns the queue into ScheduleBlocks,
#                   earliest free slot first (autoschedule).
#   5. Timetable  - per-section grid, with swap and CSV export
#                   (section_timetable, block_swap, section_export).
#
# Every rule lives in scheduling/planner.py; these views only translate HTTP.
# ===========================================================================



def _set_archived(request, obj, *, archived, label):
    """Flip one catalogue row and log it. ``archived_at`` follows through
    ArchiveClockMixin.save, so the 30-day clock starts and stops with it."""
    obj.is_active = not archived
    obj.save(update_fields=["is_active"])
    verb = "archived" if archived else "restored"
    log_activity(request.user, Action.UPDATE, obj=obj, request=request,
                 description=f"{label} {verb}.")
    return verb


def _bulk_catalogue(request, model, *, noun, label, list_name, archived_name):
    """Archive, restore or delete several terms/subjects/sections in one post
    (bulk-actions.js). Nothing here is ever blocked: offerings and blocks are
    history of the row itself, and Delete forever removes them with it."""
    action = request.POST.get("action", "")
    back = post_redirect(request, reverse(f"scheduling:{list_name}"))
    if action not in BULK_ACTIONS:
        return bulk_response(request, message="Unknown bulk action.", ok=False,
                             redirect_to=back)
    rows = bulk_selection(request, model.objects.all())
    if action == "delete":
        return bulk_delete_forever(
            request, rows, noun=noun,
            redirect_to=post_redirect(request, reverse(f"scheduling:{archived_name}")))
    archive = action == "archive"
    done = [o for o in rows if o.is_active is archive]
    for obj in done:
        _set_archived(request, obj, archived=archive, label=label(obj))
    message = bulk_summary(done=done, blocked=[], noun=noun,
                           verb="archived" if archive else "restored")
    return bulk_response(request, message=message or "Nothing to update.",
                         ok=bool(done), redirect_to=back)


@admin_required
def term_list(request):
    """Academic terms (``2025-2026 1st Semester``), with modal add/edit."""
    archived = request.resolver_match.url_name == "term_archived"
    q = request.GET.get("q", "").strip()
    terms = Term.objects.annotate(
        offering_count=Count("offerings", filter=Q(offerings__is_active=True), distinct=True),
        block_count=Count("schedule_blocks", filter=Q(schedule_blocks__is_active=True),
                          distinct=True),
    ).filter(is_active=not archived)
    if q:
        terms = terms.filter(Q(name__icontains=q) | Q(academic_year__icontains=q))
    page_obj = paginate(request, terms.order_by("-start_date", "-pk"))
    return render(request, "scheduling/term_list.html", {
        "terms": page_obj, "page_obj": page_obj, "q": q, "archived": archived,
        "archived_count": Term.objects.filter(is_active=False).count(),
        "querystring": querystring_without_page(request),
        "create_url": reverse("scheduling:term_create"),
    })


def _term_form_view(request, form, *, title, action_url, created, instance=None):
    return form_view(
        request, form=form,
        full_template="scheduling/term_form.html",
        modal_template="scheduling/_term_form_modal.html",
        context={"title": title, "term": instance,
                 "subtitle": "File the academic year and semester.",
                 "cancel_url": reverse("scheduling:term_list"), "action_url": action_url},
        redirect_to="scheduling:term_list",
        success_message=lambda t: f"Term '{t.name}' {'added' if created else 'updated'}.",
        created=created)


@admin_required
def term_create(request):
    form = TermForm(request.POST or None, initial={"is_active": True})
    return _term_form_view(request, form, title="Add term",
                           action_url=reverse("scheduling:term_create"), created=True)


@admin_required
def term_update(request, pk):
    obj = get_object_or_404(Term, pk=pk)
    form = TermForm(request.POST or None, instance=obj)
    return _term_form_view(request, form, title=f"Edit term: {obj.name}",
                           action_url=reverse("scheduling:term_update", args=[obj.pk]),
                           created=False, instance=obj)


@admin_required
@require_POST
def term_toggle(request, pk):
    """Archive / restore. Offerings and blocks PROTECT the term, so it is never deleted."""
    term = get_object_or_404(Term, pk=pk)
    term.is_active = not term.is_active
    term.save(update_fields=["is_active", "updated_at"])
    verb = "restored" if term.is_active else "archived"
    log_activity(request.user, Action.UPDATE, obj=term, request=request,
                 description=f"Term '{term.name}' {verb}.")
    messages.success(request, f"Term '{term.name}' {verb}.")
    return redirect(post_redirect(request, reverse("scheduling:term_list")))

@admin_required
@require_POST
def term_bulk(request):
    return _bulk_catalogue(request, Term, noun="term(s)",
                           label=lambda o: f"Term '{o.name}'", list_name="term_list",
                           archived_name="term_archived")


@admin_required
@require_POST
def term_delete(request, pk):
    """Delete an archived term for good, with its offerings and timetable
    blocks (retention)."""
    obj = get_object_or_404(Term, pk=pk)
    return delete_forever(request, obj, noun="term(s)",
                          redirect_to=post_redirect(request, reverse("scheduling:term_archived")))



@admin_required
def schedule_settings(request):
    """How borrowing reacts to the timetable, and the semester guards work from."""
    from .forms import ScheduleSettingsForm
    from .models import ScheduleSettings
    from .services import effective_term, get_schedule_settings, term_has_schedule

    row = ScheduleSettings.objects.first()
    cfg = get_schedule_settings()
    form = ScheduleSettingsForm(
        request.POST or None, instance=row,
        initial=None if row else {"enforcement": cfg.enforcement,
                                  "no_schedule_policy": cfg.no_schedule_policy})
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        log_activity(request.user, Action.UPDATE, obj=saved, request=request,
                     description=(f"Schedule settings: enforcement={saved.enforcement}, "
                                  f"no timetable={saved.no_schedule_policy}, semester="
                                  f"{saved.current_term or 'automatic'}."))
        messages.success(request, "Schedule settings saved.")
        return redirect("scheduling:settings")
    term = effective_term(timezone.localdate())
    return render(request, "scheduling/settings.html", {
        "form": form, "effective_term": term,
        "term_has_schedule": term_has_schedule(term),
    })


@admin_required
def department_list(request):
    """Department catalogue (live or archived), with modal add/edit."""
    archived = request.resolver_match.url_name == "department_archived"
    q = request.GET.get("q", "").strip()
    departments = Department.objects.annotate(
        subject_count=Count("subjects", distinct=True),
        section_count=Count("sections", distinct=True),
    ).filter(is_active=not archived)
    if q:
        departments = departments.filter(Q(code__icontains=q) | Q(name__icontains=q))
    page_obj = paginate(request, departments.order_by("code", "-pk"))
    return render(request, "scheduling/department_list.html", {
        "departments": page_obj, "page_obj": page_obj, "q": q, "archived": archived,
        "archived_count": Department.objects.filter(is_active=False).count(),
        "querystring": querystring_without_page(request),
        "create_url": reverse("scheduling:department_create"),
    })


@admin_required
def department_detail(request, pk):
    """Faculty and subjects of a department, with the times each faculty holds."""
    dept = get_object_or_404(Department, pk=pk)
    people = Borrower.objects.filter(department=dept, is_active=True).order_by("last_name", "first_name")
    faculty = [p for p in people if p.borrower_type == Borrower.BorrowerType.FACULTY]
    staff = [p for p in people if p.borrower_type == Borrower.BorrowerType.STAFF]
    held = faculty_schedule(faculty)
    for f in faculty:
        f.blocks = held[f.pk]
        f.taught_subjects = list({b.subject_id: b.subject for b in f.blocks if b.subject}.values())
    subjects = (Subject.objects.filter(department=dept, is_active=True)
                .annotate(teacher_count=Count("schedule_blocks__instructor", distinct=True))
                .order_by("code"))
    template = ("scheduling/_department_detail_modal.html" if is_modal(request)
                else "scheduling/department_detail.html")
    return render(request, template, {"dept": dept, "faculty": faculty, "staff": staff,
                                      "subjects": subjects})


def _department_form(request, obj=None):
    form = DepartmentForm(request.POST or None, instance=obj)
    url = (reverse("scheduling:department_update", args=[obj.pk]) if obj
           else reverse("scheduling:department_create"))
    return form_view(
        request, form=form,
        full_template="scheduling/department_form.html",
        modal_template="scheduling/_department_form_modal.html",
        context={"title": f"Edit department: {obj.code}" if obj else "Add department",
                 "subtitle": "Owns subjects, sections and rooms.",
                 "cancel_url": reverse("scheduling:department_list"),
                 "action_url": url},
        redirect_to="scheduling:department_list",
        success_message=lambda o: f"Department '{o.code}' {'updated' if obj else 'added'}.",
        created=obj is None)


@admin_required
def department_create(request):
    return _department_form(request)


@admin_required
def department_update(request, pk):
    return _department_form(request, get_object_or_404(Department, pk=pk))


@admin_required
@require_POST
def department_toggle(request, pk):
    """Archive / restore. Never a delete: subjects, sections, rooms and people
    point at the department."""
    dept = get_object_or_404(Department, pk=pk)
    dept.is_active = not dept.is_active
    dept.save(update_fields=["is_active"])
    verb = "restored" if dept.is_active else "archived"
    log_activity(request.user, Action.UPDATE, obj=dept, request=request,
                 description=f"Department '{dept.code}' {verb}.")
    messages.success(request, f"Department '{dept.code}' {verb}.")
    return redirect(post_redirect(request, reverse("scheduling:department_list")))


@admin_required
@require_POST
def department_bulk(request):
    return _bulk_catalogue(request, Department, noun="department(s)",
                           label=lambda o: f"Department '{o.code}'",
                           list_name="department_list",
                           archived_name="department_archived")


@admin_required
@require_POST
def department_delete(request, pk):
    """Delete an archived department for good (retention). Subjects, sections,
    rooms and people that pointed at it are detached, not deleted."""
    obj = get_object_or_404(Department, pk=pk)
    return delete_forever(request, obj, noun="department(s)",
                          redirect_to=post_redirect(request, reverse("scheduling:department_archived")))


@admin_required
def subject_list(request):
    """Subject catalogue (live or archived), with modal add/edit."""
    archived = request.resolver_match.url_name == "subject_archived"
    q = request.GET.get("q", "").strip()
    department = request.GET.get("department", "").strip()

    subjects = Subject.objects.select_related("department").annotate(
        offering_count=Count("offerings", filter=Q(offerings__is_active=True)))
    subjects = subjects.filter(is_active=not archived)
    if q:
        subjects = subjects.filter(Q(code__icontains=q) | Q(title__icontains=q))
    if department.isdigit():
        subjects = subjects.filter(department_id=int(department))

    page_obj = paginate(request, subjects.order_by("code", "-pk"))
    return render(request, "scheduling/subject_list.html", {
        "subjects": page_obj, "page_obj": page_obj, "q": q, "department": department,
        "archived": archived,
        "archived_count": Subject.objects.filter(is_active=False).count(),
        "departments": Department.objects.filter(is_active=True),
        "querystring": querystring_without_page(request),
        "create_url": reverse("scheduling:subject_create"),
    })


@admin_required
def subject_create(request):
    form = SubjectForm(request.POST or None)
    return form_view(
        request, form=form,
        full_template="scheduling/subject_form.html",
        modal_template="scheduling/_subject_form_modal.html",
        context={"title": "Add subject",
                 "subtitle": "The catalogue an offering picks from.",
                 "cancel_url": reverse("scheduling:subject_list"),
                 "action_url": reverse("scheduling:subject_create")},
        redirect_to="scheduling:subject_list",
        success_message=lambda obj: f"Subject '{obj.code}' added.",
        created=True)


@admin_required
def subject_update(request, pk):
    obj = get_object_or_404(Subject, pk=pk)
    form = SubjectForm(request.POST or None, instance=obj)
    return form_view(
        request, form=form,
        full_template="scheduling/subject_form.html",
        modal_template="scheduling/_subject_form_modal.html",
        context={"title": f"Edit subject: {obj.code}", "subject": obj,
                 "subtitle": "The catalogue an offering picks from.",
                 "cancel_url": reverse("scheduling:subject_list"),
                 "action_url": reverse("scheduling:subject_update", args=[obj.pk])},
        redirect_to="scheduling:subject_list",
        success_message=lambda o: f"Subject '{o.code}' updated.",
        created=False)


@admin_required
@require_POST
def subject_toggle(request, pk):
    """Archive / restore. Never a delete: offerings and blocks PROTECT or point
    at the subject, so the row has to stay for the history to stay readable."""
    subject = get_object_or_404(Subject, pk=pk)
    subject.is_active = not subject.is_active
    subject.save(update_fields=["is_active"])
    verb = "restored" if subject.is_active else "archived"
    log_activity(request.user, Action.UPDATE, obj=subject, request=request,
                 description=f"Subject '{subject.code}' {verb}.")
    messages.success(request, f"Subject '{subject.code}' {verb}.")
    return redirect(post_redirect(request, reverse("scheduling:subject_list")))

@admin_required
@require_POST
def subject_bulk(request):
    return _bulk_catalogue(request, Subject, noun="subject(s)",
                           label=lambda o: f"Subject '{o.code}'", list_name="subject_list",
                           archived_name="subject_archived")


@admin_required
@require_POST
def subject_delete(request, pk):
    """Delete an archived subject for good, with its offerings and timetable
    blocks (retention)."""
    obj = get_object_or_404(Subject, pk=pk)
    return delete_forever(request, obj, noun="subject(s)",
                          redirect_to=post_redirect(request, reverse("scheduling:subject_archived")))



@admin_required
def section_list(request):
    """Sections, block rows and group rows together, newest filters applied."""
    archived = request.resolver_match.url_name == "section_archived"
    q = request.GET.get("q", "").strip()
    program = request.GET.get("program", "").strip()
    year = request.GET.get("year", "").strip()
    split = request.GET.get("split", "").strip()

    sections = Section.objects.select_related("department").annotate(
        offering_count=Count("offerings", filter=Q(offerings__is_active=True)),
        block_count=Count("schedule_blocks", filter=Q(schedule_blocks__is_active=True)),
    )
    sections = sections.filter(is_active=not archived)
    if q:
        # The code is assembled from four columns, so "BSIT", "4D" and "G1" each
        # have to find the row on their own.
        sections = sections.filter(
            Q(program__icontains=q) | Q(block__icontains=q) | Q(group__icontains=q))
    if program:
        sections = sections.filter(program__iexact=program)
    if year.isdigit():
        sections = sections.filter(year_level=int(year))
    if split == "block":
        sections = sections.filter(group="")
    elif split == "group":
        sections = sections.exclude(group="")

    page_obj = paginate(request, sections.order_by(
        "program", "year_level", "block", "group", "-pk"))
    programs = (Section.objects.order_by("program")
                .values_list("program", flat=True).distinct())
    years = (Section.objects.order_by("year_level")
             .values_list("year_level", flat=True).distinct())
    return render(request, "scheduling/section_list.html", {
        "sections": page_obj, "page_obj": page_obj, "q": q, "program": program,
        "year": year, "split": split, "archived": archived,
        "archived_count": Section.objects.filter(is_active=False).count(),
        "programs": programs, "years": years,
        "querystring": querystring_without_page(request),
        "create_url": reverse("scheduling:section_create"),
    })


@admin_required
def section_create(request):
    form = SectionForm(request.POST or None)
    return form_view(
        request, form=form,
        full_template="scheduling/section_form.html",
        modal_template="scheduling/_section_form_modal.html",
        context={"title": "Add section",
                 "subtitle": "A whole block such as BSIT-4D, or one of its groups such as BSIT-4DG1.",
                 "cancel_url": reverse("scheduling:section_list"),
                 "action_url": reverse("scheduling:section_create")},
        redirect_to="scheduling:section_list",
        success_message=lambda obj: f"Section {obj.code} added.",
        created=True)


@admin_required
def section_update(request, pk):
    obj = get_object_or_404(Section, pk=pk)
    form = SectionForm(request.POST or None, instance=obj)
    return form_view(
        request, form=form,
        full_template="scheduling/section_form.html",
        modal_template="scheduling/_section_form_modal.html",
        context={"title": f"Edit section: {obj.code}", "section": obj,
                 "subtitle": "A whole block such as BSIT-4D, or one of its groups such as BSIT-4DG1.",
                 "cancel_url": reverse("scheduling:section_list"),
                 "action_url": reverse("scheduling:section_update", args=[obj.pk])},
        redirect_to="scheduling:section_list",
        success_message=lambda o: f"Section {o.code} updated.",
        created=False)


@admin_required
@require_POST
def section_toggle(request, pk):
    section = get_object_or_404(Section, pk=pk)
    section.is_active = not section.is_active
    section.save(update_fields=["is_active", "updated_at"])
    verb = "restored" if section.is_active else "archived"
    log_activity(request.user, Action.UPDATE, obj=section, request=request,
                 description=f"Section {section.code} {verb}.")
    messages.success(request, f"Section {section.code} {verb}.")
    return redirect(post_redirect(request, reverse("scheduling:section_list")))


def _selected_term(request, param="term"):
    """The term the page is about: the one asked for, else the active one."""
    terms = Term.objects.filter(is_active=True).order_by("-start_date")
    raw = (request.GET.get(param, "") or request.POST.get(param, "")).strip()
    if raw.isdigit():
        found = terms.filter(pk=int(raw)).first()
        if found:
            return terms, found
    return terms, terms.filter(is_active=True).first() or terms.first()

@admin_required
@require_POST
def section_bulk(request):
    return _bulk_catalogue(request, Section, noun="section(s)",
                           label=lambda o: f"Section {o.code}", list_name="section_list",
                           archived_name="section_archived")


@admin_required
@require_POST
def section_delete(request, pk):
    """Delete an archived section for good, with its offerings and timetable
    blocks (retention)."""
    obj = get_object_or_404(Section, pk=pk)
    return delete_forever(request, obj, noun="section(s)",
                          redirect_to=post_redirect(request, reverse("scheduling:section_archived")))



@admin_required
def offering_list(request):
    """The FCFS queue: what still has to be scheduled, in the order it will be.

    ``queue_position`` is the row's place in that order, which is the point of
    the page - it says who wins a contested slot before the run happens.
    """
    archived = request.resolver_match.url_name == "offering_archived"
    terms, term = _selected_term(request)
    q = request.GET.get("q", "").strip()
    state = request.GET.get("state", "").strip()
    section_id = request.GET.get("section", "").strip()

    offerings = (SubjectOffering.objects
                 .select_related("term", "subject", "section", "instructor",
                                 "preferred_room__building")
                 .annotate(placed=Count("blocks", filter=Q(blocks__is_active=True)))
                 .filter(is_active=not archived))
    if not archived:
        offerings = offerings.exclude(Q(subject__is_active=False)
                                      | Q(section__is_active=False))
    if term:
        offerings = offerings.filter(term=term)
    if q:
        offerings = offerings.filter(
            Q(subject__code__icontains=q) | Q(subject__title__icontains=q)
            | Q(section__program__icontains=q) | Q(section__block__icontains=q)
            | Q(instructor__last_name__icontains=q)
            | Q(instructor__first_name__icontains=q))
    if section_id.isdigit():
        # A group's list includes the shared classes of its whole block, and a
        # whole block's includes its groups' own: each is that section's week.
        picked = Section.objects.filter(pk=int(section_id)).first()
        offerings = offerings.filter(
            section_id__in=section_scope_ids(picked) if picked else [int(section_id)])
    offerings = offerings.order_by(*SubjectOffering.QUEUE_ORDER)

    # Queue position is numbered over the whole term queue, not the filtered
    # page, so a filter never makes the numbering lie.
    full_order = list(SubjectOffering.objects.filter(term=term, is_active=True)
                      .order_by(*SubjectOffering.QUEUE_ORDER)
                      .values_list("pk", flat=True)) if term else []
    position = {pk: i + 1 for i, pk in enumerate(full_order)}

    rows = list(offerings)
    labels = group_labels([o.section for o in rows])
    for o in rows:
        o._scheduled_count = o.placed
        o.queue_position = position.get(o.pk)
        o.shared_groups = shared_label(o.section, labels)
    if state == "scheduled":
        rows = [o for o in rows if o.is_fully_scheduled]
    elif state == "pending":
        rows = [o for o in rows if not o.is_fully_scheduled]

    pending = 0
    if term:
        for o in (SubjectOffering.objects.filter(term=term, is_active=True)
                  .annotate(placed=Count("blocks", filter=Q(blocks__is_active=True)))):
            if o.placed < o.meetings_per_week:
                pending += 1

    page_obj = paginate(request, rows)
    return render(request, "scheduling/offering_list.html", {
        "offerings": page_obj, "page_obj": page_obj, "terms": terms,
        "selected_term": term, "q": q, "state": state, "section": section_id,
        "sections": _section_options(Section.objects.filter(is_active=True)),
        "pending_count": pending, "queue_total": len(full_order),
        "archived": archived,
        "archived_count": SubjectOffering.objects.filter(
            is_active=False, **({"term": term} if term else {})).count(),
        "querystring": querystring_without_page(request),
        "create_url": f"{reverse('scheduling:offering_create')}?term={term.pk}" if term else reverse("scheduling:offering_create"),
    })


@admin_required
def offering_create(request):
    initial = {}
    _, term = _selected_term(request)
    if term:
        initial["term"] = term.pk
    form = SubjectOfferingForm(request.POST or None, initial=initial)
    filed = {}

    def save(f):
        obj = f.save(commit=False)
        if not obj.created_by_id:
            obj.created_by = request.user
        filed["result"] = _file_offering(request, f, obj)
        return obj

    return form_view(
        request, form=form,
        full_template="scheduling/offering_form.html",
        modal_template="scheduling/_offering_form_modal.html",
        context={"title": "Add subject offering",
                 "subtitle": "One subject, one section, one professor, queued first come first served.",
                 "cancel_url": reverse("scheduling:offering_list"),
                 "action_url": reverse("scheduling:offering_create")},
        redirect_to="scheduling:offering_list",
        success_message=lambda obj: (f"{obj.subject.code} queued for {_section_display(obj.section)}."
                                     + filed["result"].message_suffix),
        created=True, save=save)


@admin_required
def offering_update(request, pk):
    obj = get_object_or_404(SubjectOffering, pk=pk)
    form = SubjectOfferingForm(request.POST or None, instance=obj)
    filed = {}

    def save(f):
        filed["result"] = _file_offering(request, f, f.save(commit=False))
        return filed["result"].offering

    return form_view(
        request, form=form,
        full_template="scheduling/offering_form.html",
        modal_template="scheduling/_offering_form_modal.html",
        context={"title": f"Edit offering: {obj}", "offering": obj,
                 "subtitle": "One subject, one section, one professor, queued first come first served.",
                 "cancel_url": reverse("scheduling:offering_list"),
                 "action_url": reverse("scheduling:offering_update", args=[obj.pk])},
        redirect_to="scheduling:offering_list",
        success_message=lambda o: f"Offering {o} updated." + filed["result"].message_suffix,
        created=False, save=save)


def _section_display(section):
    """``BSIT-4D (G1 + G2 together)`` for a shared class, else the plain code."""
    groups = shared_label(section)
    return f"{section.code} ({groups} together)" if groups else section.code


def _file_offering(request, form, obj):
    """Save through ``planner.file_offering`` and audit the merged offerings
    (the offering itself is audited by ``form_view``)."""
    result = file_offering(offering=obj, share_with_groups=form.cleaned_data.get("shared"),
                           user=request.user)
    for dup in result.merged:
        log_activity(request.user, Action.UPDATE, obj=dup, request=request,
                     description=f"Offering '{dup}' archived and unscheduled: merged into "
                                 f"the shared class '{result.offering}'.")
    return result


@admin_required
@require_POST
def offering_unschedule(request, pk):
    """Take an offering's placed meetings back off the timetable.

    This is how an admin frees a slot for someone else: the offering drops back
    into the queue as pending and the next run can place it elsewhere.
    """
    offering = get_object_or_404(
        SubjectOffering.objects.select_related("subject", "section"), pk=pk)
    blocks = unschedule_offering(offering, user=request.user)
    log_activity(request.user, Action.UPDATE, obj=offering, request=request,
                 description=f"{len(blocks)} placed meeting(s) of '{offering}' "
                             "removed from the timetable.")
    messages.success(request, f"{len(blocks)} meeting(s) of {offering} unscheduled.")
    return redirect(post_redirect(request, reverse("scheduling:offering_list")))


@admin_required
@require_POST
def offering_archive(request, pk):
    """Archive an offering (clearing what it had placed), or restore it."""
    offering = get_object_or_404(
        SubjectOffering.objects.select_related("subject", "section"), pk=pk)
    verb = _set_offering_archived(request, offering, archived=offering.is_active)
    if verb is None:
        messages.error(request, f"{offering} cannot be restored: {restore_blocker(offering)}.")
    else:
        messages.success(request, f"Offering {offering} {verb}.")
    return redirect(post_redirect(request, reverse("scheduling:offering_list")))


def _set_offering_archived(request, offering, *, archived):
    """Archiving also takes the offering's placed meetings off the timetable;
    restoring puts it back in the queue unplaced (run the auto-scheduler).

    Returns the verb, or None when a restore is refused because the subject is
    now taken through a shared class (``planner.restore_blocker``)."""
    if not archived and restore_blocker(offering):
        return None
    offering.is_active = not archived
    offering.save(update_fields=["is_active", "updated_at"])
    if archived:
        unschedule_offering(offering, user=request.user)
    verb = "archived" if archived else "restored"
    log_activity(request.user, Action.UPDATE, obj=offering, request=request,
                 description=f"Offering '{offering}' {verb}"
                             f"{' and unscheduled' if archived else ''}.")
    return verb


@admin_required
@require_POST
def offering_bulk(request):
    action = request.POST.get("action", "")
    back = post_redirect(request, reverse("scheduling:offering_list"))
    if action not in BULK_ACTIONS:
        return bulk_response(request, message="Unknown bulk action.", ok=False,
                             redirect_to=back)
    rows = bulk_selection(request, SubjectOffering.objects.select_related(
        "subject", "section"))
    if action == "delete":
        return bulk_delete_forever(
            request, rows, noun="offering(s)",
            redirect_to=post_redirect(request, reverse("scheduling:offering_archived")))
    archive = action == "archive"
    done, blocked = [], []
    for obj in rows:
        if obj.is_active is not archive:
            continue
        reason = None if archive else restore_blocker(obj)
        if reason:
            blocked.append(f"{obj}: {reason}")
            continue
        _set_offering_archived(request, obj, archived=archive)
        done.append(obj)
    message = bulk_summary(done=done, blocked=blocked, noun="offering(s)",
                           verb="archived" if archive else "restored")
    return bulk_response(request, message=message or "Nothing to update.",
                         ok=bool(done), redirect_to=back)


@admin_required
@require_POST
def offering_delete(request, pk):
    """Delete an archived offering for good (retention). Its timetable blocks
    stay, detached from it."""
    obj = get_object_or_404(SubjectOffering.objects.select_related(
        "subject", "section"), pk=pk)
    return delete_forever(request, obj, noun="offering(s)",
                          redirect_to=post_redirect(request, reverse("scheduling:offering_archived")))


@admin_required
def autoschedule(request):
    """Preview and run the first-come-first-served scheduler.

    GET shows the options and the queue. POST runs it: ``apply=1`` commits,
    anything else is a dry run - the same algorithm inside a transaction that
    rolls back, so the preview is exactly what the real run will do.
    """
    terms, term = _selected_term(request)
    form = AutoScheduleForm(request.POST or None,
                            initial={"term": term.pk if term else None})
    run = None
    applied = False

    if request.method == "POST":
        if form.is_valid():
            run_term = form.cleaned_data["term"]
            commit = request.POST.get("apply") == "1"
            rebuild = bool(form.cleaned_data.get("rebuild"))
            if commit and rebuild:
                cleared = clear_term_schedule(run_term)
                log_activity(request.user, Action.UPDATE, obj=run_term, request=request,
                             description=f"{cleared} auto-placed block(s) cleared before "
                                         f"re-running the scheduler for {run_term}.")
            run = auto_schedule(
                term=run_term,
                dry_run=not commit,
                user=request.user,
                days=form.days,
                day_start=form.cleaned_data["day_start"],
                day_end=form.cleaned_data["day_end"],
                one_meeting_per_day=form.cleaned_data.get("one_meeting_per_day", True),
                # A committed rebuild re-places everything; otherwise fill gaps.
                only_unscheduled=not (commit and rebuild),
            )
            term = run_term
            applied = commit
            if commit:
                log_activity(request.user, Action.CREATE, obj=run_term, request=request,
                             description=f"Auto-schedule run for {run_term}: {run.summary}")
                if run.unplaced:
                    messages.warning(request, f"Saved to the timetable: {run.summary}")
                else:
                    messages.success(request, f"Auto-schedule saved: {run.summary}")
            elif not run.placements and not run.unplaced:
                messages.info(request, "Nothing left to schedule in this term.")
        else:
            messages.error(request, "Check the auto-schedule options.")

    # The queue list is long, so it pages like every other list. The queue is
    # already in service order, which is a total order, so it pages as it is.
    queue_page = paginate(request, offering_queue(term, only_unscheduled=False) if term else [])

    return render(request, "scheduling/autoschedule.html", {
        "form": form, "terms": terms, "selected_term": term, "run": run,
        "applied": applied,
        "queue": queue_page, "page_obj": queue_page,
        "querystring": querystring_without_page(request),
        "offering_create_url": (
            f"{reverse('scheduling:offering_list')}?term={term.pk}&add=1"
            if term
            else f"{reverse('scheduling:offering_list')}?add=1"
        ),
    })


# ---------------------------------------------------------------------------
# Curriculum: which subjects each program/year takes in each semester
# ---------------------------------------------------------------------------

def _curriculum_term(request):
    """The term whose semester the curriculum page highlights: the one asked
    for, else the term the guards work from today, else the newest live one."""
    terms = Term.objects.filter(is_active=True).order_by("-start_date")
    raw = request.GET.get("term", "").strip()
    if raw.isdigit():
        found = terms.filter(pk=int(raw)).first()
        if found:
            return terms, found
    return terms, effective_term(timezone.localdate()) or terms.first()


@admin_required
def curriculum(request):
    """One program's curriculum for one term: a card per year level.

    Every term owns its curriculum (a new term starts as a copy of the previous
    one's), so the term picker switches which curriculum is shown and edited.
    """
    programs = curriculum_rules.known_programs()
    program = curriculum_rules.normalise_program(request.GET.get("program", ""))
    if not program and programs:
        program = programs[0]
    if program and program not in programs:
        programs = sorted({*programs, program})
    terms, term = _curriculum_term(request)
    semester = term.semester if term else ""
    board = curriculum_rules.program_board(program, term) if program else []
    sections = list(Section.objects.filter(program=program, is_active=True)
                    .order_by("year_level", "block", "group")) if program else []
    for year in board:
        year["sections"] = [s for s in sections if s.year_level == year["year"]]
    return render(request, "scheduling/curriculum.html", {
        "programs": programs, "program": program, "board": board,
        "terms": terms, "selected_term": term, "semester": semester,
        "semester_label": dict(Term.Semester.choices).get(semester, ""),
        "total": sum(y["count"] for y in board),
    })


@admin_required
def curriculum_add(request):
    """Add subjects to a curriculum slot (modal, with the full-page fallback).

    Same contract as accounts.crud.form_view: a modal POST answers 201 with a
    one-line message, or 422 with the partial re-rendered.
    """
    _, default_term = _curriculum_term(request)
    initial = {
        "program": curriculum_rules.normalise_program(request.GET.get("program", "")),
        "year_level": request.GET.get("year_level", "") or None,
        "term": default_term.pk if default_term else None,
    }
    form = CurriculumAddForm(request.POST or None, initial=initial)
    modal = is_modal(request)

    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        slot = curriculum_rules.slot_label(data["program"], data["year_level"],
                                           data["term"])
        added, already = curriculum_rules.add_subjects(
            program=data["program"], year_level=data["year_level"],
            term=data["term"], subjects=list(data["subjects"]), user=request.user)
        if added:
            codes = ", ".join(s.code for s in added)
            log_activity(request.user, Action.CREATE, request=request,
                         description=f"Curriculum {slot}: added {codes}.")
            noun = "subject" if len(added) == 1 else "subjects"
            msg = f"{len(added)} {noun} added to {slot}."
            if modal:
                return HttpResponse(msg, status=201)
            messages.success(request, msg)
            return redirect(f"{reverse('scheduling:curriculum')}?"
                            f"{urlencode({'program': data['program'], 'term': data['term'].pk})}")
        form.add_error("subjects", f"Every subject you picked is already in {slot}.")

    ctx = {
        "form": form, "is_modal": modal,
        "title": "Add subjects to the curriculum",
        "action_url": reverse("scheduling:curriculum_add"),
        "cancel_url": reverse("scheduling:curriculum"),
        "programs": curriculum_rules.known_programs(),
        "subject_rows": _curriculum_subject_rows(form),
    }
    status = 422 if request.method == "POST" else 200
    if modal:
        return render(request, "scheduling/_curriculum_form_modal.html", ctx, status=status)
    return render(request, "scheduling/curriculum_form.html", ctx, status=status)


def _curriculum_subject_rows(form):
    """The add modal's checklist: every live subject, the slots it is already
    in (for curriculum.js to re-check as the slot fields change) and whether it
    is already in the slot the form is on."""
    slots = curriculum_rules.subject_slots()
    key = "|".join(form.slot)
    picked = form.selected_ids()
    rows = []
    for subject in (Subject.objects.filter(is_active=True)
                    .select_related("department").order_by("code")):
        mine = slots.get(subject.pk, [])
        rows.append({"subject": subject, "slots": " ".join(mine),
                     "in_slot": key in mine, "checked": subject.pk in picked})
    return rows


@admin_required
@require_POST
def curriculum_remove(request, pk):
    """Take a subject out of its slot. Archives the row; adding it back restores it."""
    entry = get_object_or_404(CurriculumSubject.objects.select_related("subject", "term"), pk=pk)
    curriculum_rules.remove_subject(entry)
    log_activity(request.user, Action.UPDATE, obj=entry, request=request,
                 description=f"{entry.subject.code} removed from the "
                             f"{entry.slot_label} curriculum.")
    messages.success(request, f"{entry.subject.code} removed from {entry.slot_label}.")
    back = (f"{reverse('scheduling:curriculum')}?"
            f"{urlencode({'program': entry.program, 'term': entry.term_id})}")
    return redirect(post_redirect(request, back))


@api_role_required("ADMIN")
def offerable_subjects_api(request):
    """The subject options for the offering form, for one section and term.

    ``offering`` is the offering being edited, so its own subject stays listed.
    """
    def pick(model, name):
        raw = request.GET.get(name, "").strip()
        return model.objects.filter(pk=int(raw)).first() if raw.isdigit() else None

    section = pick(Section, "section")
    term = pick(Term, "term")
    offering = pick(SubjectOffering, "offering")
    keep_subject = pick(Subject, "keep_subject")
    keep_subject_id = offering.subject_id if offering else (keep_subject.pk if keep_subject else None)
    offer = curriculum_rules.offerable_subjects(
        section=section, term=term,
        keep_subject_id=keep_subject_id)
    curriculum_url = reverse("scheduling:curriculum")
    if section:
        curriculum_url += "?" + urlencode({"program": section.program})
    return JsonResponse({
        "restricted": offer.restricted,
        "slot": offer.slot,
        "message": offer.message,
        "curriculum_url": curriculum_url,
        "subjects": [{"id": s.pk, "label": str(s)} for s in offer.subjects],
        "sharing": sharing_info(section),
    })


def _section_options(sections):
    """Sections for a picker, each with ``option_label``: a whole block reads
    ``BSIT-4D (G1 + G2 together)`` when it has groups, else ``(whole block)``."""
    sections = list(sections)
    labels = group_labels(sections)
    for s in sections:
        groups = shared_label(s, labels)
        if groups:
            s.option_label = f"{s.code} ({groups} together)"
        elif s.is_whole_block:
            s.option_label = f"{s.code} (whole block)"
        else:
            s.option_label = s.code
    return sections


def _selected_section(request):
    sections = Section.objects.filter(is_active=True).select_related("department")
    raw = request.GET.get("section", "").strip()
    if raw.isdigit():
        found = sections.filter(pk=int(raw)).first()
        if found:
            return sections, found
    return sections, sections.first()


@admin_required
def section_timetable(request):
    """Weekly grid for one section, exportable.

    Picking the whole block ``BSIT-4D`` shows the block's own classes *and* its
    groups' - that is a 4D student's real week. Picking ``BSIT-4DG1`` shows G1
    plus the block-wide classes, never G2. "Block only" turns the group rows
    off, which is the export for the block-wide subjects alone.
    """
    terms, term = _selected_term(request)
    sections, section = _selected_section(request)
    include_groups = request.GET.get("groups", "1") != "0"

    timetable = get_section_timetable(section, term, include_groups=include_groups)

    time_slots = _time_slots()
    days = [{"code": d, "col": d + 2, "short": str(label)[:3], "name": str(label)}
            for d, label in list(ScheduleBlock.DayOfWeek.choices)[:6]]

    export_qs = urlencode({
        "section": section.pk if section else "",
        "term": term.pk if term else "",
        "groups": "1" if include_groups else "0",
    })
    # Curriculum subjects for this section + semester, with placement progress.
    subject_rows, subjects_restricted = placeable_subjects(section=section, term=term)
    for r in subject_rows:
        r["placed"] = r["scheduled"] > 0
        r["complete"] = r["meetings"] > 0 and r["scheduled"] >= r["meetings"]
    placed_count = sum(1 for r in subject_rows if r["placed"])
    return render(request, "scheduling/section_timetable.html", {
        "subject_rows": subject_rows, "subjects_restricted": subjects_restricted,
        "subjects_placed": placed_count,
        "subjects_unplaced": len(subject_rows) - placed_count,
        "terms": terms, "selected_term": term,
        "sections": _section_options(sections), "selected_section": section,
        "include_groups": include_groups,
        "timetable": timetable, "time_slots": time_slots, "days": days,
        "group_rows": section.groups() if section else [],
        "export_qs": export_qs,
        "swap_form": BlockSwapForm(term=term),
        "default_room_id": (timetable["blocks"][0]["block"].room_id
                            if timetable["blocks"] else ""),
        **_class_modal_context(),
        # Targets for the empty states, which cannot call {% url %} through an
        # include's `with`.
        "sections_url": reverse("scheduling:section_list"),
        "autoschedule_url": reverse("scheduling:autoschedule"),
    })


@admin_required
def section_export(request):
    """CSV of one section's timetable (``?section=&term=&groups=1``)."""
    _, term = _selected_term(request)
    _, section = _selected_section(request)
    if section is None or term is None:
        messages.error(request, "Pick a section and a term to export.")
        return redirect("scheduling:section_timetable")
    # No audit row: ActivityLog has no VIEW/EXPORT action, and reading a
    # timetable is not a change. The reports exports behave the same way.
    include_groups = request.GET.get("groups", "1") != "0"
    return export_section_timetable_csv(section, term, include_groups=include_groups)


@admin_required
def room_export(request):
    """CSV of one room's timetable (``?room=&term=``)."""
    from .planner import export_room_timetable_csv
    room = Room.objects.select_related("building").filter(
        pk=request.GET.get("room", "").strip() or 0).first()
    term = Term.objects.filter(is_active=True,
                               pk=request.GET.get("term", "").strip() or 0).first()
    if room is None or term is None:
        messages.error(request, "Pick a room and a term to export.")
        return redirect("scheduling:room_timetable")
    return export_room_timetable_csv(room, term)


@admin_required
@require_POST
def block_swap(request):
    """Swap the day/time of two placed blocks (``planner.swap_blocks``).

    This is the manual override of FCFS order: the queue decided who got
    Monday 9am, and this is how an admin trades it away without rebuilding.
    """
    form = BlockSwapForm(request.POST, term=None)
    ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"
    back = post_redirect(request, reverse("scheduling:section_timetable"))

    if not form.is_valid():
        first = next(iter(form.errors.values()))[0]
        if ajax:
            return JsonResponse({"error": first, "errors": form.errors}, status=422)
        messages.error(request, first)
        return redirect(back)

    try:
        a, b = swap_blocks(form.cleaned_data["block_a"], form.cleaned_data["block_b"],
                           include_rooms=form.cleaned_data.get("include_rooms", False),
                           user=request.user)
    except SwapError as e:
        if ajax:
            return JsonResponse({"error": str(e)}, status=422)
        messages.error(request, str(e))
        return redirect(back)

    detail = (f"Swapped: '{a.title}' is now {a.get_day_of_week_display()} "
              f"{a.start_time:%H:%M}-{a.end_time:%H:%M} in {a.room}; "
              f"'{b.title}' is now {b.get_day_of_week_display()} "
              f"{b.start_time:%H:%M}-{b.end_time:%H:%M} in {b.room}.")
    for block in (a, b):
        log_activity(request.user, Action.UPDATE, obj=block, request=request,
                     description=detail)
    if ajax:
        return JsonResponse({"success": True, "message": detail})
    messages.success(request, detail)
    return redirect(back)


@api_role_required("ADMIN")
@require_POST
def block_move_api(request, pk):
    """POST /api/scheduling/blocks/<pk>/move/ - {day_of_week, start_time, end_time, room}.

    All three clash dimensions are validated; 422 carries the reason.
    """
    try:
        data = read_body(request)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=422)

    block = ScheduleBlock.objects.filter(pk=pk).first()
    if block is None:
        return JsonResponse({"error": "Schedule block not found."}, status=404)

    room = None
    if data.get("room"):
        room = Room.objects.filter(pk=data["room"]).first()
        if room is None:
            return JsonResponse({"error": "Room not found."}, status=422)
    try:
        moved = move_block(block, day_of_week=data.get("day_of_week"),
                           start_time=data.get("start_time") or None,
                           end_time=data.get("end_time") or None, room=room)
    except SwapError as e:
        return JsonResponse({"error": str(e)}, status=422)
    except (TypeError, ValueError) as e:
        return JsonResponse({"error": str(e)}, status=422)

    log_activity(request.user, Action.UPDATE, obj=moved, request=request,
                 description=f"'{moved.title}' moved to {moved.get_day_of_week_display()} "
                             f"{moved.start_time:%H:%M}-{moved.end_time:%H:%M} in {moved.room}.")
    return JsonResponse({
        "success": True,
        "block": {"id": moved.pk, "day_of_week": moved.day_of_week,
                  "start_time": f"{moved.start_time:%H:%M}",
                  "end_time": f"{moved.end_time:%H:%M}",
                  "room": str(moved.room)},
    })


def _parse_time(value):
    from datetime import datetime
    try:
        return datetime.strptime(str(value)[:5], "%H:%M").time()
    except ValueError:
        raise PlaceError("Use HH:MM for the start and end time.")


@api_role_required("ADMIN")
def class_options_api(request):
    """GET ?section=&term= - the curriculum subjects an admin may place for a
    section (with how many meetings each already has), for the add modal."""
    def pick(model, name):
        raw = request.GET.get(name, "").strip()
        return model.objects.filter(pk=int(raw)).first() if raw.isdigit() else None

    section, term = pick(Section, "section"), pick(Term, "term")
    rows, restricted = placeable_subjects(section=section, term=term)
    if section is None:
        message = "Pick a section to see the subjects of its curriculum."
    elif restricted:
        message = f"{curriculum_rules.slot_label(section.program, section.year_level, term)} curriculum."
    else:
        message = ("No curriculum is set for this program yet, so every subject is "
                   "listed. Add one on the Curriculum page to narrow this list.")
    return JsonResponse({"subjects": rows, "restricted": restricted, "message": message})


@api_role_required("ADMIN")
@require_POST
def class_place_api(request):
    """POST /api/scheduling/classes/ - place a class by hand.

    {term, section, subject, room, day_of_week, start_time, end_time,
    instructor?, repeat_days?}. Every meeting goes through
    ``planner.place_class`` in one transaction: any clash rolls the lot back
    and answers 422 with the reason."""
    try:
        data = read_body(request)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=422)

    def get(model, key):
        raw = data.get(key)
        return model.objects.filter(pk=int(raw)).first() if str(raw or "").isdigit() else None

    term, section = get(Term, "term"), get(Section, "section")
    subject, room = get(Subject, "subject"), get(Room, "room")
    instructor = get(Borrower, "instructor")
    try:
        start, end = _parse_time(data.get("start_time")), _parse_time(data.get("end_time"))
        days = [int(data.get("day_of_week"))]
        raw = data.get("repeat_days") or []
        if hasattr(data, "getlist"):
            raw = data.getlist("repeat_days")
        days += sorted({int(d) for d in raw} - set(days))
    except (TypeError, ValueError, PlaceError) as e:
        return JsonResponse({"error": str(e) if isinstance(e, PlaceError)
                             else "Pick a day."}, status=422)

    placed = []
    try:
        with db_transaction.atomic():
            for day in days:
                placed.append(place_class(
                    term=term, section=section, subject=subject, room=room,
                    day_of_week=day, start_time=start, end_time=end,
                    instructor=instructor, user=request.user))
    except PlaceError as e:
        return JsonResponse({"error": str(e)}, status=422)

    for block, offering, created in placed:
        log_activity(request.user, Action.CREATE, obj=block, request=request,
                     description=f"'{block.title}' placed on {block.get_day_of_week_display()} "
                                 f"{block.start_time:%H:%M}-{block.end_time:%H:%M} in {block.room}"
                                 + (f"; offering filed for {section.code}." if created else "."))
    first = placed[0][1]
    return JsonResponse({
        "success": True, "created": len(placed),
        "offering": {"id": first.pk, "scheduled": first.scheduled_count,
                     "meetings": first.meetings_per_week},
    }, status=201)
