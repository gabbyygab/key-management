"""Room finder for a subject: which rooms can hold one meeting of an offering.

A subject on its own says nothing about rooms. The *offering* (this subject, to
this section, by this professor, in this term) does: the meeting length, the
room type (a lecture + lab offering needs a classroom once and a laboratory
once), the seats (the section's head count) and the department that owns it.

The answer is built from the same rules the scheduler and the manual "Add a
class" dialog enforce, so a window shown here is a window ``place_class``
accepts:

* rooms: ``planner.candidate_rooms`` (usable, right type, big enough, the
  department rule, preferred room first, smallest fit first);
* clashes: active blocks of the term on three dimensions, exactly as
  ``planner.slot_blockers`` / ``busy_slots`` read them — the room, the
  professor, and the section through ``Section.clashing_section_ids`` (a whole
  block and its groups block each other, sibling groups do not);
* the grid: Monday to Saturday, 07:00-21:00, half-hour starts
  (``planner.check_grid``).

Blocks overlap when ``a.start < b.end and a.end > b.start``; touching is fine.
"""
from dataclasses import dataclass, field
from datetime import time as dtime

from django.db.models import Q

from facilities.models import Room
from .models import ScheduleBlock
from .planner import (DAY_END, DAY_START, SCHEDULABLE_DAYS, SLOT_MINUTES,
                      _fmt_time_range, candidate_rooms, minutes_of,
                      offering_department_id, time_of)

DAY_NAMES = dict(ScheduleBlock.DayOfWeek.choices)

# Room cards per page. Fixed (no ?per_page= override): each card carries a
# week of timelines, so four is what fits a screen.
ROOMS_PER_PAGE = 4
USABLE_EXCLUDED = (Room.Status.MAINTENANCE, Room.Status.INACTIVE)


def _ceil_slot(minutes):
    return -(-minutes // SLOT_MINUTES) * SLOT_MINUTES


def _floor_slot(minutes):
    return minutes // SLOT_MINUTES * SLOT_MINUTES


def _hours(minutes):
    h, m = divmod(int(minutes or 0), 60)
    if not m:
        return f"{h} h"
    return f"{h}.5 h" if m == 30 else f"{h} h {m} min"


# ---------------------------------------------------------------------------
# What the meeting needs
# ---------------------------------------------------------------------------

def meeting_options(offering):
    """The meetings an admin can look for, as ``[{kind, label, minutes, room_type}]``.

    A lecture + lab offering has two (lecture in a classroom, lab in a
    laboratory); any other offering has one kind of meeting.
    """
    options = []
    for kind, minutes, room_type in offering.meeting_plan():
        if any(o["kind"] == kind for o in options):
            continue
        label = ScheduleBlock.MeetingKind(kind).label if kind else "Meeting"
        options.append({"kind": kind, "label": label, "minutes": minutes or 0,
                        "duration_label": _hours(minutes), "room_type": room_type or ""})
    if not options:   # meetings_per_week = 0: still let the admin look
        options.append({"kind": "", "label": "Meeting",
                        "minutes": offering.minutes_per_meeting or 0,
                        "duration_label": _hours(offering.minutes_per_meeting),
                        "room_type": offering.room_type or ""})
    return options


@dataclass
class Requirement:
    offering: object
    kind: str
    kind_label: str
    minutes: int
    room_type: str          # "" = any teaching room (classroom or laboratory)
    seats: int | None
    department: object
    placed: list = field(default_factory=list)   # this meeting kind's blocks
    notice: str = ""

    @property
    def room_type_label(self):
        if not self.room_type:
            return "Classroom or laboratory"
        return Room.RoomType(self.room_type).label

    @property
    def duration_label(self):
        return _hours(self.minutes)


def requirement_for(offering, kind=""):
    """The rules one meeting of ``offering`` must satisfy."""
    options = meeting_options(offering)
    option = next((o for o in options if o["kind"] == kind), options[0])
    section = offering.section
    seats = section.headcount if section is not None else None
    placed = [b for b in offering.scheduled_blocks().select_related("room__building")
              .order_by("day_of_week", "start_time")
              if not option["kind"] or b.meeting_kind == option["kind"]]
    notice = ""
    if option["kind"] and placed:
        b = placed[0]
        notice = (f"The {option['label'].lower()} is already on the timetable "
                  f"({DAY_NAMES[b.day_of_week]} {_fmt_time_range(b.start_time, b.end_time)}, "
                  f"{b.room}). Move it from the timetable instead of adding another.")
    elif not option["kind"] and offering.meetings_per_week and \
            len(placed) >= offering.meetings_per_week:
        notice = (f"All {offering.meetings_per_week} meetings a week are already placed. "
                  "Scheduling another one adds an extra meeting to the offering.")
    dept_id = offering_department_id(offering)
    department = None
    if dept_id:
        department = (section.department if section and section.department_id == dept_id
                      else offering.subject.department)
    return Requirement(offering=offering, kind=option["kind"], kind_label=option["label"],
                       minutes=option["minutes"], room_type=option["room_type"],
                       seats=seats, department=department, placed=placed, notice=notice)


# ---------------------------------------------------------------------------
# The search
# ---------------------------------------------------------------------------

@dataclass
class RoomResult:
    room: object
    fit: str                      # "preferred" | "department" | "shared" | "any"
    days: list                    # [{day, name, short, segments, windows}]
    windows: list                 # every usable window, in week order
    free_minutes: int = 0
    seats: int | None = None      # the head count the room must hold

    @property
    def earliest(self):
        return self.windows[0] if self.windows else None

    @property
    def spare_seats(self):
        if self.seats and self.room.capacity:
            return self.room.capacity - self.seats
        return None

    @property
    def free_display(self):
        return _hours(self.free_minutes)


@dataclass
class FinderResult:
    requirement: Requirement
    days: list
    window_start: dtime
    window_end: dtime
    rooms: list                   # RoomResult with at least one window
    full_rooms: list              # RoomResult that fit but are booked all window
    hidden: dict                  # why other usable rooms are not listed
    people_busy: list             # [{day, name, time, label}] section/professor


def _busy_label(block, dims):
    who = []
    if "section" in dims:
        who.append(block.section.code if block.section_id else "Section")
    if "instructor" in dims:
        who.append(block.instructor.full_name if block.instructor_id else "Professor")
    return f"{' & '.join(who)}: {block.title}"


def _day_segments(*, window, room_busy, people_busy, minutes):
    """Split ``window`` (start, end minutes) into coloured runs.

    States, strongest first: ``room`` (another class is in this room),
    ``people`` (the section or the professor is in class elsewhere), ``free``
    (a run long enough for the meeting, on half-hour starts) and ``short``
    (free, but too short). Returns ``(segments, windows)``; times are minutes.
    """
    w_start, w_end = window
    points = {w_start, w_end}
    for s, e, _ in room_busy + people_busy:
        if s < w_end and e > w_start:
            points.update((max(s, w_start), min(e, w_end)))
    points = sorted(points)

    runs = []
    for a, b in zip(points, points[1:]):
        room = [lbl for s, e, lbl in room_busy if s < b and e > a]
        people = [lbl for s, e, lbl in people_busy if s < b and e > a]
        state = "room" if room else "people" if people else "free"
        labels = room or people
        if runs and runs[-1]["state"] == state and runs[-1]["labels"] == labels:
            runs[-1]["end"] = b
        else:
            runs.append({"state": state, "start": a, "end": b, "labels": labels})

    windows = []
    for run in runs:
        if run["state"] != "free":
            continue
        start, end = _ceil_slot(run["start"]), _floor_slot(run["end"])
        if minutes and end - start >= minutes:
            windows.append((start, end))
            run["window"] = (start, end)
        else:
            run["state"] = "short"
    span = (w_end - w_start) or 1
    segments = []
    for run in runs:
        seg = {
            "state": run["state"],
            "start": time_of(run["start"]), "end": time_of(run["end"]),
            "left": round((run["start"] - w_start) * 100 / span, 3),
            "width": round((run["end"] - run["start"]) * 100 / span, 3),
            "time_display": _fmt_time_range(time_of(run["start"]), time_of(run["end"])),
            "label": "; ".join(dict.fromkeys(run["labels"])),
        }
        if "window" in run:
            # What a click books: the meeting at the first half-hour start.
            s, e = run["window"]
            seg.update(book_start=f"{time_of(s):%H:%M}",
                       book_end=f"{time_of(s + minutes):%H:%M}",
                       book_display=_fmt_time_range(time_of(s), time_of(s + minutes)),
                       window_display=_fmt_time_range(time_of(s), time_of(e)))
        segments.append(seg)
    return segments, windows


def find_rooms_for_offering(*, offering, kind="", days=None, window_start=DAY_START,
                            window_end=DAY_END, building=None):
    """Every room that can hold one meeting of ``offering``, with its free windows.

    ``days`` limits the week (Mon-Sat by default); ``window_start`` /
    ``window_end`` limit the hours (07:00-21:00 by default, snapped to the
    half-hour grid). A room is listed when at least one window of the meeting's
    length is free in the room *and* for the section *and* for the professor.
    """
    req = requirement_for(offering, kind)
    term = offering.term
    days = sorted({int(d) for d in (days or SCHEDULABLE_DAYS)} & set(SCHEDULABLE_DAYS))
    w_start = max(_ceil_slot(minutes_of(window_start)), minutes_of(DAY_START))
    w_end = min(_floor_slot(minutes_of(window_end)), minutes_of(DAY_END))

    rooms = candidate_rooms(offering, room_type=req.room_type)
    if building is not None:
        rooms = [r for r in rooms if r.building_id == building.pk]
    dept_id = offering_department_id(offering)
    preferred_id = (offering.preferred_lab_room_id if req.kind == ScheduleBlock.MeetingKind.LAB
                    else offering.preferred_room_id)
    if req.kind == ScheduleBlock.MeetingKind.LAB and preferred_id:
        # candidate_rooms only knows the lecture's preferred room; the lab has its own.
        pref = next((r for r in rooms if r.pk == preferred_id), None)
        if pref:
            rooms = [pref] + [r for r in rooms if r.pk != preferred_id]

    # One query: every block that could block any listed room, the section or the professor.
    section_ids = offering.section.clashing_section_ids() if offering.section_id else set()
    q = Q(room_id__in=[r.pk for r in rooms])
    if offering.instructor_id:
        q |= Q(instructor_id=offering.instructor_id)
    if section_ids:
        q |= Q(section_id__in=section_ids)
    blocks = list(ScheduleBlock.objects.filter(q, term=term, is_active=True, day_of_week__in=days)
                  .select_related("section", "instructor", "room__building")
                  .order_by("day_of_week", "start_time")) if days and w_end > w_start else []

    by_room, people, people_rows = {}, {}, []
    for b in blocks:
        span = (minutes_of(b.start_time), minutes_of(b.end_time))
        by_room.setdefault((b.room_id, b.day_of_week), []).append((*span, b.title))
        dims = []
        if b.section_id in section_ids:
            dims.append("section")
        if offering.instructor_id and b.instructor_id == offering.instructor_id:
            dims.append("instructor")
        if dims:
            label = _busy_label(b, dims)
            people.setdefault(b.day_of_week, []).append((*span, label))
            if span[0] < w_end and span[1] > w_start:
                people_rows.append({"day": b.day_of_week, "name": DAY_NAMES[b.day_of_week],
                                    "time": _fmt_time_range(b.start_time, b.end_time),
                                    "label": label, "room": str(b.room)})

    listed, full = [], []
    for room in rooms:
        day_rows, all_windows, free = [], [], 0
        for day in days:
            if w_end <= w_start:
                break
            segments, windows = _day_segments(
                window=(w_start, w_end), room_busy=by_room.get((room.pk, day), []),
                people_busy=people.get(day, []), minutes=req.minutes)
            day_windows = [{
                "day": day, "name": DAY_NAMES[day],
                "start": f"{time_of(s):%H:%M}", "end": f"{time_of(e):%H:%M}",
                "book_end": f"{time_of(s + req.minutes):%H:%M}",
                "time_display": _fmt_time_range(time_of(s), time_of(e)),
                "book_display": _fmt_time_range(time_of(s), time_of(s + req.minutes)),
                "minutes": e - s,
            } for s, e in windows]
            free += sum(w["minutes"] for w in day_windows)
            all_windows += day_windows
            day_rows.append({"day": day, "name": DAY_NAMES[day], "short": DAY_NAMES[day][:3],
                             "segments": segments, "windows": day_windows})
        if room.pk == preferred_id:
            fit = "preferred"
        elif dept_id and room.department_id == dept_id:
            fit = "department"
        elif dept_id:
            fit = "shared"
        else:
            fit = "any"
        result = RoomResult(room=room, fit=fit, days=day_rows, windows=all_windows,
                            free_minutes=free, seats=req.seats)
        (listed if all_windows else full).append(result)

    return FinderResult(
        requirement=req, days=[{"day": d, "name": DAY_NAMES[d]} for d in days],
        window_start=time_of(w_start), window_end=time_of(max(w_end, w_start)),
        rooms=listed, full_rooms=full, hidden=hidden_room_counts(offering, req, building),
        people_busy=people_rows)


def hidden_room_counts(offering, req, building=None):
    """Why the other rooms are not listed, as ``{reason: count}`` (non-zero only).

    Checked in the order ``candidate_rooms`` filters, so each room is counted
    once, under the first rule it fails.
    """
    qs = Room.objects.filter(is_active=True, building__is_active=True)
    if building is not None:
        qs = qs.filter(building=building)
    dept_id = offering_department_id(offering)
    wanted = ([req.room_type] if req.room_type
              else [Room.RoomType.CLASSROOM, Room.RoomType.LABORATORY])
    counts = {"unavailable": 0, "wrong_type": 0, "too_small": 0, "other_department": 0}
    for status, room_type, capacity, room_dept in qs.values_list(
            "status", "room_type", "capacity", "department_id"):
        if status in USABLE_EXCLUDED:
            key = "unavailable"
        elif room_type not in wanted:
            key = "wrong_type"
        elif req.seats and capacity is not None and capacity < req.seats:
            key = "too_small"
        elif dept_id and room_dept is not None and room_dept != dept_id:
            key = "other_department"
        else:
            continue
        counts[key] += 1
    labels = {"unavailable": "under maintenance", "wrong_type": "wrong room type",
              "too_small": "too small", "other_department": "another department's"}
    return [{"key": k, "count": n, "label": labels[k]} for k, n in counts.items() if n]


def axis_ticks(window_start, window_end):
    """Hour marks for the timeline header: ``[{left, label}]``."""
    s, e = minutes_of(window_start), minutes_of(window_end)
    span = (e - s) or 1
    step = 60 if span <= 8 * 60 else 120
    first = -(-s // 60) * 60
    ticks = []
    for m in range(first, e + 1, step):
        t = time_of(min(m, 23 * 60 + 59))
        hour12 = t.hour % 12 or 12
        ticks.append({"left": round((m - s) * 100 / span, 3),
                      "label": f"{hour12}{'a' if t.hour < 12 else 'p'}"})
    return ticks
