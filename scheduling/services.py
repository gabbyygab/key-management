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
