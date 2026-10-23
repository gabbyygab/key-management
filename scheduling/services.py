import json
from datetime import timedelta

from django.db.models import Q

from facilities.models import Room

from .models import ScheduleBlock


class ScheduleConflictError(ValueError):
    def __init__(self, conflicts=None):
        self.conflicts = list(conflicts or [])
        names = ", ".join(str(block) for block in self.conflicts) or "schedule block"
        super().__init__(f"Conflicts with: {names}")


def find_conflicts(room, day_of_week, start_time, end_time, term,
                   exclude_block_id=None):
    """Return active schedule blocks in the same room/day/term that overlap."""
    queryset = ScheduleBlock.objects.filter(
        room=room,
        day_of_week=day_of_week,
        term=term,
        is_active=True,
        start_time__lt=end_time,
        end_time__gt=start_time,
    ).select_related("term", "room", "room__building")

    if exclude_block_id:
        queryset = queryset.exclude(pk=exclude_block_id)

    return list(queryset.order_by("start_time", "end_time"))


def validate_block(block):
    """Validate times, term coverage and room usability for a schedule block."""
    if block.end_time <= block.start_time:
        raise ValueError("End time must be after start time.")

    term = block.term
    if term is None:
        raise ValueError("A term is required for scheduling.")
    if term.start_date and term.end_date and term.end_date < term.start_date:
        raise ValueError("Term end date must be after its start date.")

    weekday = block.day_of_week
    start_delta = (weekday - term.start_date.weekday()) % 7
    first_occurrence = term.start_date + timedelta(days=start_delta)
    last_occurrence = term.end_date - timedelta(days=(term.end_date.weekday() - weekday) % 7)
    if first_occurrence > term.end_date or last_occurrence < term.start_date:
        raise ValueError("Schedule block day falls outside the selected term date range.")

    room = block.room
    if room is None:
        raise ValueError("A room is required for scheduling.")
    if not room.is_active or room.status in {Room.Status.MAINTENANCE, Room.Status.INACTIVE}:
        raise ValueError("Selected room is not usable for scheduling.")

    conflicts = find_conflicts(
        room=room,
        day_of_week=block.day_of_week,
        start_time=block.start_time,
        end_time=block.end_time,
        term=term,
        exclude_block_id=getattr(block, "pk", None),
    )
    if conflicts:
        raise ScheduleConflictError(conflicts)


def find_free_rooms(*, day_of_week, start_time, end_time, term=None,
                    building=None, room_type=None, min_capacity=None):
    """Rooms available for a weekly slot."""
    qs = (Room.objects.select_related("building")
          .filter(is_active=True, building__is_active=True)
          .exclude(status__in=[Room.Status.MAINTENANCE, Room.Status.INACTIVE]))

    if building is not None:
        qs = qs.filter(building=building)
    if room_type:
        qs = qs.filter(room_type=room_type)
    if min_capacity:
        qs = qs.filter(capacity__gte=min_capacity)

    if term is not None:
        occupied = ScheduleBlock.objects.filter(
            term=term,
            day_of_week=day_of_week,
            is_active=True,
            start_time__lt=end_time,
            end_time__gt=start_time,
        ).values_list("room_id", flat=True)
        qs = qs.exclude(pk__in=occupied)

    return qs.order_by("building__code", "room_code")


PALETTES = [
    {
        "bg": "bg-emerald-50",
        "border": "border-emerald-300",
        "text": "text-emerald-950",
        "badge": "bg-emerald-100 text-emerald-800",
        "border_accent": "border-l-4 border-l-emerald-600",
    },
    {
        "bg": "bg-blue-50",
        "border": "border-blue-300",
        "text": "text-blue-950",
        "badge": "bg-blue-100 text-blue-800",
        "border_accent": "border-l-4 border-l-blue-600",
    },
    {
        "bg": "bg-violet-50",
        "border": "border-violet-300",
        "text": "text-violet-950",
        "badge": "bg-violet-100 text-violet-800",
        "border_accent": "border-l-4 border-l-violet-600",
    },
    {
        "bg": "bg-amber-50",
        "border": "border-amber-300",
        "text": "text-amber-950",
        "badge": "bg-amber-100 text-amber-800",
        "border_accent": "border-l-4 border-l-amber-600",
    },
    {
        "bg": "bg-rose-50",
        "border": "border-rose-300",
        "text": "text-rose-950",
        "badge": "bg-rose-100 text-rose-800",
        "border_accent": "border-l-4 border-l-rose-600",
    },
    {
        "bg": "bg-sky-50",
        "border": "border-sky-300",
        "text": "text-sky-950",
        "badge": "bg-sky-100 text-sky-800",
        "border_accent": "border-l-4 border-l-sky-600",
    },
    {
        "bg": "bg-indigo-50",
        "border": "border-indigo-300",
        "text": "text-indigo-950",
        "badge": "bg-indigo-100 text-indigo-800",
        "border_accent": "border-l-4 border-l-indigo-600",
    },
    {
        "bg": "bg-teal-50",
        "border": "border-teal-300",
        "text": "text-teal-950",
        "badge": "bg-teal-100 text-teal-800",
        "border_accent": "border-l-4 border-l-teal-600",
    },
]


def get_room_timetable(room, term):
    """
    Return grid data and schedule blocks for one room and term.
    Window is 07:00 to 21:00 in 30-min increments across Monday to Saturday (0 to 5).
    """
    if room is None or term is None:
        return {
            "blocks": [],
            "total_blocks": 0,
            "total_hours": 0.0,
        }

    room_id = room.pk if hasattr(room, "pk") else room
    term_id = term.pk if hasattr(term, "pk") else term

    blocks_qs = (
        ScheduleBlock.objects.filter(
            room_id=room_id,
            term_id=term_id,
            day_of_week__lte=5,  # Mon-Sat
            is_active=True,
        )
        .select_related("subject", "instructor", "room", "room__building", "term")
        .prefetch_related("exceptions")
        .order_by("day_of_week", "start_time")
    )

    START_MINUTE = 7 * 60   # 07:00 = 420
    END_MINUTE = 21 * 60     # 21:00 = 1260
    SLOT_MINUTES = 30

    processed_blocks = []
    total_minutes = 0

    for block in blocks_qs:
        start_min = block.start_time.hour * 60 + block.start_time.minute
        end_min = block.end_time.hour * 60 + block.end_time.minute
        if end_min <= start_min:
            continue

        duration = end_min - start_min
        total_minutes += duration

        clamped_start = max(start_min, START_MINUTE)
        clamped_end = min(end_min, END_MINUTE)

        row_start = (clamped_start - START_MINUTE) // SLOT_MINUTES + 1
        row_end = (clamped_end - START_MINUTE + SLOT_MINUTES - 1) // SLOT_MINUTES + 1
        span = max(1, row_end - row_start)

        # Mon=0 -> col 2, Tue=1 -> col 3, ..., Sat=5 -> col 7
        col = block.day_of_week + 2

        # Color palette
        if block.subject and block.subject.code:
            palette_idx = sum(ord(c) for c in block.subject.code) % len(PALETTES)
        else:
            palette_idx = sum(ord(c) for c in block.title) % len(PALETTES)
        palette = PALETTES[palette_idx]

        time_display = f"{block.start_time.strftime('%H:%M')} – {block.end_time.strftime('%H:%M')}"

        exceptions = list(block.exceptions.all())
        has_exception = len(exceptions) > 0
        exception_text = exceptions[0].get_exception_type_display() if has_exception else ""

        instructor_display = "Unassigned"
        if block.instructor:
            instructor_display = block.instructor.full_name or str(block.instructor)

        raw_json = json.dumps({
            "id": block.pk,
            "title": block.title,
            "subject_id": block.subject_id or "",
            "instructor_id": block.instructor_id or "",
            "day_of_week": block.day_of_week,
            "start_time": block.start_time.strftime("%H:%M"),
            "end_time": block.end_time.strftime("%H:%M"),
            "is_active": block.is_active,
            "room_id": block.room_id,
            "term_id": block.term_id,
        })

        processed_blocks.append({
            "block": block,
            "id": block.pk,
            "title": block.title,
            "subject_code": block.subject.code if block.subject else "",
            "subject_title": block.subject.title if block.subject else "",
            "instructor_name": instructor_display,
            "instructor_id": block.instructor_id or "",
            "subject_id": block.subject_id or "",
            "room_id": block.room_id,
            "term_id": block.term_id,
            "day_of_week": block.day_of_week,
            "start_time_str": block.start_time.strftime("%H:%M"),
            "end_time_str": block.end_time.strftime("%H:%M"),
            "time_display": time_display,
            "row_start": row_start,
            "row_end": row_end,
            "span": span,
            "col": col,
            "palette": palette,
            "is_active": block.is_active,
            "has_exception": has_exception,
            "exception_text": exception_text,
            "json_data": raw_json,
        })

    return {
        "blocks": processed_blocks,
        "total_blocks": len(processed_blocks),
        "total_hours": round(total_minutes / 60, 1),
    }
