"""Section-aware timetable planning: the FCFS auto-scheduler, swaps and exports.

This sits on top of ``scheduling/services.py`` (which owns room conflicts and
``validate_block``) and adds the three things a class timetable needs that a
per-room view does not:

* **Three clash dimensions, not one.** A room can only host one class at a time
  (``services.find_conflicts``), a professor can only teach one class at a time
  (``instructor_conflicts``) and a section can only attend one class at a time
  (``section_conflicts``). The section dimension understands the split: the
  whole block ``BSIT-4D`` clashes with both ``BSIT-4DG1`` and ``BSIT-4DG2``,
  while the two groups may meet at once in different rooms
  (``Section.clashing_section_ids``).

* **First come, first served.** ``auto_schedule`` walks the offerings of a term
  in queue order (``SubjectOffering.QUEUE_ORDER``: priority, then the
  ``requested_at`` stamp, then pk) and gives each one the earliest slot and room
  that breaks none of the three rules. Nothing already on the timetable is ever
  moved to make room for a later offering — a later offering that cannot fit
  comes back as *unplaced with a reason*, which is the report the admin acts on.
  The run is one transaction: a dry run rolls back and reports, so the preview
  and the real thing use the same code path.

* **Swapping.** ``swap_blocks`` exchanges the day and time (optionally the room
  too) of two placed blocks and re-validates both afterwards, atomically. This
  is the manual escape hatch from FCFS: the admin fixes what the queue order got
  wrong without rebuilding the week.

Rules live here; views only translate HTTP.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import time as dtime

from django.db import transaction
from django.db.models import Count, F, Q
from django.http import HttpResponse

from facilities.models import Room
from .models import ScheduleBlock, Section, Subject, SubjectOffering, Term


def _fmt_time(t):
    """Format a time object as 12-hour clock, e.g. '7:00 AM', '12:30 PM'."""
    h = t.hour % 12 or 12
    suffix = "AM" if t.hour < 12 else "PM"
    return f"{h}:{t.minute:02d} {suffix}"


def _fmt_time_range(start, end):
    """Format a start-end pair as '7:00 AM – 8:30 AM'."""
    return f"{_fmt_time(start)} – {_fmt_time(end)}"

from .services import find_conflicts

# The timetable window, shared with the grid in views.room_timetable: 07:00 to
# 21:00 in half-hour steps, Monday to Saturday.
DAY_START = dtime(7, 0)
DAY_END = dtime(21, 0)
SLOT_MINUTES = 30
SCHEDULABLE_DAYS = (0, 1, 2, 3, 4, 5)


def minutes_of(t):
    return t.hour * 60 + t.minute


def time_of(total_minutes):
    return dtime(total_minutes // 60, total_minutes % 60)


# ---------------------------------------------------------------------------
# Clash checks
# ---------------------------------------------------------------------------

def _overlapping(term, day_of_week, start_time, end_time, exclude_block_id=None):
    qs = ScheduleBlock.objects.filter(
        term=term, day_of_week=day_of_week, is_active=True,
        start_time__lt=end_time, end_time__gt=start_time,
    )
    if exclude_block_id:
        qs = qs.exclude(pk=exclude_block_id)
    return qs


def instructor_conflicts(instructor, day_of_week, start_time, end_time, term,
                         exclude_block_id=None):
    """Active blocks where this professor is already teaching at that time."""
    if instructor is None:
        return []
    return list(
        _overlapping(term, day_of_week, start_time, end_time, exclude_block_id)
        .filter(instructor=instructor)
        .select_related("room__building", "subject", "section")
        .order_by("start_time")
    )


def section_conflicts(section, day_of_week, start_time, end_time, term,
                      exclude_block_id=None):
    """Active blocks the same students already have at that time.

    Uses ``Section.clashing_section_ids`` so a whole block and its groups block
    each other while sibling groups do not.
    """
    if section is None:
        return []
    ids = section.clashing_section_ids()
    if not ids:
        return []
    return list(
        _overlapping(term, day_of_week, start_time, end_time, exclude_block_id)
        .filter(section_id__in=ids)
        .select_related("room__building", "subject", "section", "instructor")
        .order_by("start_time")
    )


def describe_conflicts(blocks):
    return ", ".join(
        f"{b.title} ({ScheduleBlock.DayOfWeek(b.day_of_week).label} "
        f"{b.start_time:%H:%M}–{b.end_time:%H:%M}"
        + (f", {b.room}" if b.room_id else "") + ")"
        for b in blocks
    )


def slot_blockers(*, term, section, instructor, room, day_of_week, start_time,
                  end_time, exclude_block_id=None):
    """Every reason this slot cannot be used, as ``{dimension: [blocks]}``.

    An empty dict means the slot is free on all three dimensions.
    """
    blockers = {}
    if room is not None:
        clash = find_conflicts(room, day_of_week, start_time, end_time, term,
                               exclude_block_id=exclude_block_id)
        if clash:
            blockers["room"] = clash
    clash = instructor_conflicts(instructor, day_of_week, start_time, end_time, term,
                                 exclude_block_id=exclude_block_id)
    if clash:
        blockers["instructor"] = clash
    clash = section_conflicts(section, day_of_week, start_time, end_time, term,
                              exclude_block_id=exclude_block_id)
    if clash:
        blockers["section"] = clash
    return blockers


def blocker_summary(blockers):
    """'room: IT401 Lecture (Monday 09:00–10:30, SCI-201)' — for the UI."""
    label = {"room": "Room busy", "instructor": "Professor busy",
             "section": "Section busy"}
    return "; ".join(f"{label[dim]} — {describe_conflicts(blocks)}"
                     for dim, blocks in blockers.items())


# ---------------------------------------------------------------------------
# Candidate slots and rooms
# ---------------------------------------------------------------------------

def candidate_slots(duration_minutes, *, days=SCHEDULABLE_DAYS,
                    day_start=DAY_START, day_end=DAY_END, step=SLOT_MINUTES):
    """Every (day, start, end) of ``duration_minutes`` in the window, in order.

    Earliest day first, then earliest start — that ordering *is* the
    first-come-first-served preference: the offering at the head of the queue
    takes Monday morning.
    """
    window_start, window_end = minutes_of(day_start), minutes_of(day_end)
    for day in days:
        start = window_start
        while start + duration_minutes <= window_end:
            yield day, time_of(start), time_of(start + duration_minutes)
            start += step


# Rooms a class may be held in when the offering does not name a type. A
# generator room or a stock room is a usable room for key purposes but is not
# somewhere a section is taught, so the scheduler never reaches for one on its
# own — an admin who really wants it sets it as the preferred room.
TEACHING_ROOM_TYPES = (Room.RoomType.CLASSROOM, Room.RoomType.LABORATORY)


def offering_department_id(offering):
    """The department that owns an offering: its section's, else its subject's."""
    dept = offering.section.department_id if offering.section_id else None
    return dept or (offering.subject.department_id if offering.subject_id else None)


def room_allowed_for(room, department_id):
    """The department rule: a room designated to another department is closed.

    A room with no department is shared and open to everyone; an offering with
    no department can use any room.
    """
    if not department_id or room.department_id is None:
        return True
    return room.department_id == department_id


def candidate_rooms(offering, room_type=None, preferred=None):
    """Rooms the scheduler may use for an offering, preferred one first.

    **Department rule:** a class owned by a department is only placed in rooms
    designated to that department. Rooms with no department are shared and stay
    available, but are tried after the department's own rooms.

    Filtered to usable rooms, to teaching rooms (or the offering's own
    ``room_type`` when it sets one), and to rooms with enough seats when the
    section records a head count. ``room_type`` overrides the offering's own
    (a lecture + lab offering asks once for classrooms, once for laboratories),
    and a preferred room of the wrong type is then ignored. Ordered smallest-fitting-room first so a
    20-seat class does not take the 80-seat hall and leave nothing for the
    offering behind it in the queue.
    """
    qs = (Room.objects.select_related("building")
          .filter(is_active=True, building__is_active=True)
          .exclude(status__in=[Room.Status.MAINTENANCE, Room.Status.INACTIVE]))
    wanted_type = room_type if room_type is not None else offering.room_type
    if wanted_type:
        qs = qs.filter(room_type=wanted_type)
    else:
        qs = qs.filter(room_type__in=TEACHING_ROOM_TYPES)
    seats = offering.section.headcount if offering.section_id else None
    if seats:
        # A room with no recorded capacity is still a candidate — unknown is not
        # the same as too small — but it goes to the back of the order, because
        # a known fit beats a guess.
        qs = qs.filter(Q(capacity__gte=seats) | Q(capacity__isnull=True))
    department_id = offering_department_id(offering)
    if department_id:
        qs = qs.filter(Q(department_id=department_id) | Q(department__isnull=True))
    rooms = list(qs.order_by(F("capacity").asc(nulls_last=True),
                             "building__code", "room_code"))
    if department_id:
        # Stable sort: the department's own rooms first, shared rooms after,
        # smallest-fitting-first preserved inside each group.
        rooms.sort(key=lambda r: r.department_id != department_id)
    if preferred is None:
        preferred = offering.preferred_room
    if (preferred is not None and room_allowed_for(preferred, department_id)
            and (not wanted_type or preferred.room_type == wanted_type)):
        rooms = [preferred] + [r for r in rooms if r.pk != preferred.pk]
    return rooms


# ---------------------------------------------------------------------------
# The FCFS run
# ---------------------------------------------------------------------------

@dataclass
class Placement:
    offering: SubjectOffering
    day_of_week: int
    start_time: dtime
    end_time: dtime
    room: Room
    block_id: int | None = None

    @property
    def day_label(self):
        return ScheduleBlock.DayOfWeek(self.day_of_week).label

    @property
    def time_display(self):
        return _fmt_time_range(self.start_time, self.end_time)


@dataclass
class Unplaced:
    offering: SubjectOffering
    meetings_missing: int
    reason: str


@dataclass
class ScheduleRun:
    term: Term
    dry_run: bool
    placements: list = field(default_factory=list)
    unplaced: list = field(default_factory=list)
    skipped: list = field(default_factory=list)

    @property
    def placed_count(self):
        return len(self.placements)

    @property
    def unplaced_count(self):
        return len(self.unplaced)

    @property
    def ok(self):
        return not self.unplaced

    @property
    def summary(self):
        verb = "would be placed" if self.dry_run else "placed"
        parts = [f"{self.placed_count} meeting(s) {verb}"]
        if self.unplaced:
            parts.append(f"{self.unplaced_count} offering(s) could not be fitted")
        if self.skipped:
            parts.append(f"{len(self.skipped)} already scheduled")
        return ", ".join(parts) + "."


class _TermIndex:
    """Every busy interval in a term, in memory, so a run does not query per slot.

    A full run tries up to ~160 slots per meeting across three clash dimensions.
    Doing that against the database is thousands of queries; loading the term's
    blocks once and keeping the index updated as blocks are placed is two.

    Intervals are ``(start_minute, end_minute)`` keyed by
    ``(dimension, owner_id, day_of_week)``. Section keys are expanded through
    ``clashing_section_ids`` on insert, so a whole-block booking lands under its
    groups' keys too and the group/block rule needs no special case on lookup.
    """

    def __init__(self, term):
        self.term = term
        self.busy = {}
        self._section_keys = {}
        blocks = ScheduleBlock.objects.filter(term=term, is_active=True).values(
            "room_id", "instructor_id", "section_id", "day_of_week",
            "start_time", "end_time")
        for b in blocks:
            self.add(room_id=b["room_id"], instructor_id=b["instructor_id"],
                     section_id=b["section_id"], day_of_week=b["day_of_week"],
                     start_time=b["start_time"], end_time=b["end_time"])

    def _section_scope(self, section_id):
        """Section ids a booking for ``section_id`` makes busy, cached."""
        if section_id not in self._section_keys:
            section = Section.objects.filter(pk=section_id).first()
            self._section_keys[section_id] = (
                section.clashing_section_ids() if section else {section_id})
        return self._section_keys[section_id]

    def add(self, *, room_id, instructor_id, section_id, day_of_week,
            start_time, end_time):
        span = (minutes_of(start_time), minutes_of(end_time))
        if room_id:
            self.busy.setdefault(("room", room_id, day_of_week), []).append(span)
        if instructor_id:
            self.busy.setdefault(("instructor", instructor_id, day_of_week), []).append(span)
        if section_id:
            for sid in self._section_scope(section_id):
                self.busy.setdefault(("section", sid, day_of_week), []).append(span)

    def _free(self, kind, owner_id, day, start, end):
        if not owner_id:
            return True
        for s, e in self.busy.get((kind, owner_id, day), ()):
            if start < e and end > s:
                return False
        return True

    def room_free(self, room_id, day, start, end):
        return self._free("room", room_id, day, start, end)

    def instructor_free(self, instructor_id, day, start, end):
        return self._free("instructor", instructor_id, day, start, end)

    def section_free(self, section_id, day, start, end):
        return self._free("section", section_id, day, start, end)


def offering_queue(term, *, only_unscheduled=True):
    """The offerings of a term in first-come-first-served order."""
    qs = (SubjectOffering.objects
          .filter(term=term, is_active=True)
          .select_related("subject__department", "section__department", "instructor",
                           "preferred_room__building", "preferred_lab_room__building", "term")
          .annotate(placed=Count("blocks", filter=Q(blocks__is_active=True)))
          .order_by(*SubjectOffering.QUEUE_ORDER))
    offerings = list(qs)
    # One count query for the whole queue instead of one per offering, read back
    # through SubjectOffering.scheduled_count.
    for o in offerings:
        o._scheduled_count = o.placed
    if only_unscheduled:
        offerings = [o for o in offerings if o.remaining_meetings > 0]
    return offerings


def auto_schedule(*, term, offerings=None, dry_run=False, user=None,
                  days=SCHEDULABLE_DAYS, day_start=DAY_START, day_end=DAY_END,
                  one_meeting_per_day=True, only_unscheduled=True):
    """Place the term's offerings on the timetable, first come first served.

    Walks the queue in order and, for each offering, fills its missing meetings
    with the earliest slot and room that clashes with nothing. Already-placed
    blocks are never moved, so an earlier offering keeps what it took: this is
    what makes the run FCFS rather than a global optimiser.

    ``one_meeting_per_day`` keeps the meetings of one offering on separate days,
    which is what a weekly timetable normally wants.

    Returns a :class:`ScheduleRun`. ``dry_run=True`` does the full run inside a
    transaction and rolls it back, so the preview is exactly what the real run
    will do.
    """
    if term is None:
        raise ValueError("A term is required.")

    run = ScheduleRun(term=term, dry_run=dry_run)
    # The queue always carries every active offering: an already-placed one is
    # reported as skipped rather than dropped silently, so the run summary adds
    # up to what the admin has on screen.
    queue = (offerings if offerings is not None
             else offering_queue(term, only_unscheduled=False))

    with transaction.atomic():
        index = _TermIndex(term)
        room_cache = {}
        for offering in queue:
            jobs = _meeting_jobs(offering, only_unscheduled)
            if not jobs:
                run.skipped.append(offering)
                continue

            used_days = set(
                offering.scheduled_blocks().values_list("day_of_week", flat=True)
            ) if one_meeting_per_day else set()
            placed_here = 0

            missing_jobs = 0
            reasons = []
            for kind, duration, job_room_type in jobs:
                preferred = _preferred_for(offering, kind)
                cache_key = (job_room_type, preferred.pk if preferred else None,
                             offering.section.headcount if offering.section_id else None,
                             offering_department_id(offering))
                if cache_key not in room_cache:
                    room_cache[cache_key] = candidate_rooms(offering, job_room_type, preferred)
                rooms = room_cache[cache_key]
                if not rooms:
                    missing_jobs += 1
                    reasons.append(_no_room_reason(offering, job_room_type))
                    continue
                slot = _first_free_slot(
                    offering, index=index, rooms=rooms, days=days,
                    day_start=day_start, day_end=day_end, duration=duration,
                    skip_days=used_days)
                if slot is None:
                    missing_jobs += 1
                    reasons.append(
                        f"No free {duration}-minute slot left in the week — the professor, "
                        "the section or every candidate room is busy whenever the others "
                        "are free. Free a slot, widen the room type, or move an earlier "
                        "offering out of the way.")
                    continue
                day, start, end, room = slot
                label = f" ({ScheduleBlock.MeetingKind(kind).label})" if kind else ""
                block = ScheduleBlock.objects.create(
                    term=term,
                    room=room,
                    subject=offering.subject,
                    section=offering.section,
                    offering=offering,
                    title=f"{offering.subject.code} · {offering.section.code}{label}",
                    instructor=offering.instructor,
                    meeting_kind=kind,
                    day_of_week=day,
                    start_time=start,
                    end_time=end,
                    is_active=True,
                    created_by=user,
                )
                index.add(room_id=room.pk, instructor_id=offering.instructor_id,
                          section_id=offering.section_id, day_of_week=day,
                          start_time=start, end_time=end)
                run.placements.append(Placement(
                    offering=offering, day_of_week=day, start_time=start,
                    end_time=end, room=room, block_id=block.pk))
                used_days.add(day)

            if missing_jobs:
                run.unplaced.append(Unplaced(
                    offering=offering,
                    meetings_missing=missing_jobs,
                    reason=reasons[0],
                ))

        if dry_run:
            transaction.set_rollback(True)

    if dry_run:
        # The rolled-back ids are meaningless outside the transaction.
        for p in run.placements:
            p.block_id = None
    return run


def _preferred_for(offering, kind):
    """The room named for this meeting: the lab room for a lab, else the main one."""
    if kind == ScheduleBlock.MeetingKind.LAB:
        return offering.preferred_lab_room
    return offering.preferred_room


def _meeting_jobs(offering, only_unscheduled):
    """The ``(kind, minutes, room_type)`` meetings still to place for an offering.

    A lecture + lab offering is tracked per kind, so one that already has its
    lecture is only missing the lab (and the lab goes in a laboratory).
    """
    plan = offering.meeting_plan()
    if not only_unscheduled:
        return plan
    if offering.is_lecture_lab:
        have = set(offering.scheduled_blocks().values_list("meeting_kind", flat=True))
        return [job for job in plan if job[0] not in have]
    return plan[:offering.remaining_meetings]


def _no_room_reason(offering, room_type=None):
    room_type = room_type if room_type is not None else offering.room_type
    bits = [f"type {room_type}" if room_type
            else "a classroom or laboratory"]
    seats = offering.section.headcount if offering.section_id else None
    if seats:
        bits.append(f"at least {seats} seats")
    dept_id = offering_department_id(offering)
    if dept_id:
        dept = (offering.section.department if offering.section_id
                and offering.section.department_id else offering.subject.department)
        bits.append(f"designated to {dept.code} (or shared)")
    return (f"No usable room matches this offering ({' and '.join(bits)}). Check the "
            "room type, the section's student count, the rooms' departments, and "
            "that the rooms and their building are active.")


def _first_free_slot(offering, *, index, rooms, days, day_start, day_end,
                     duration, skip_days):
    """The earliest (day, start, end, room) with no room/professor/section clash.

    Slots come out of ``candidate_slots`` earliest-first, and the first one that
    is free wins — that is the whole of "first come, first served". The
    professor and the section do not depend on the room, so those two dimensions
    are checked once per slot before any room is tried.
    """
    open_days = [d for d in days if d not in skip_days]
    for day, start, end in candidate_slots(duration, days=open_days,
                                           day_start=day_start, day_end=day_end):
        s, e = minutes_of(start), minutes_of(end)
        if not index.instructor_free(offering.instructor_id, day, s, e):
            continue
        if not index.section_free(offering.section_id, day, s, e):
            continue
        for room in rooms:
            if index.room_free(room.pk, day, s, e):
                return day, start, end, room
    return None


def unschedule_offering(offering, *, user=None):
    """Drop an offering's placed blocks off the timetable (deactivate them)."""
    blocks = list(offering.scheduled_blocks())
    for block in blocks:
        block.is_active = False
        block.save(update_fields=["is_active", "updated_at"])
    return blocks


def clear_term_schedule(term, *, offerings_only=True):
    """Deactivate the term's auto-placed blocks so a run can start clean.

    ``offerings_only`` leaves hand-made bookings (blocks with no offering)
    alone, which is almost always what the admin means by "re-run".
    """
    qs = ScheduleBlock.objects.filter(term=term, is_active=True)
    if offerings_only:
        qs = qs.exclude(offering__isnull=True)
    return qs.update(is_active=False)


# ---------------------------------------------------------------------------
# Shared group classes
# ---------------------------------------------------------------------------
#
# Some subjects are taken by every group of a block at once: BSIT-4DG1 and
# BSIT-4DG2 sit in the same room at the same time with the same professor. That
# is not a new concept, it is an offering filed against the whole block
# (``BSIT-4D``): the whole block already clashes with each of its groups, and a
# group's timetable already includes the whole-block classes. What the helpers
# below add is the workflow: filing a shared class from a group without
# creating the whole-block row by hand, and never letting a group take the same
# subject twice (once on its own and once shared).

def group_labels(sections):
    """``{(program, year, block): ["G1", "G2"]}`` for the live groups of every
    family in ``sections``, in one query."""
    families = {(s.program, s.year_level, s.block) for s in sections if s is not None}
    if not families:
        return {}
    q = Q()
    for program, year, block in families:
        q |= Q(program=program, year_level=year, block=block)
    out = {}
    for program, year, block, group in (Section.objects.filter(q, is_active=True)
                                        .exclude(group="")
                                        .order_by("group")
                                        .values_list("program", "year_level", "block", "group")):
        out.setdefault((program, year, block), []).append(group)
    return out


def shared_label(section, labels=None):
    """``"G1 + G2"`` when ``section`` is a whole block whose groups attend its
    classes together, else ``""``. Pass ``labels`` from :func:`group_labels`
    to avoid a query per row."""
    if section is None or not section.is_whole_block:
        return ""
    if labels is None:
        groups = list(section.groups().filter(is_active=True)
                      .order_by("group").values_list("group", flat=True))
    else:
        groups = labels.get((section.program, section.year_level, section.block), [])
    return " + ".join(groups)


def sharing_info(section):
    """What the offering form says about sharing for the picked section.

    * ``available``: a group with at least one live sibling, so "all groups
      attend together" can be ticked.
    * ``is_shared``: the section is a whole block with groups, so every
      offering filed on it is already a shared class.
    """
    info = {"available": False, "is_shared": False, "block_code": "", "groups": ""}
    if section is None:
        return info
    info["block_code"] = section.block_code
    if section.is_whole_block:
        info["groups"] = shared_label(section)
        info["is_shared"] = bool(info["groups"])
        return info
    siblings = list(section.sibling_groups().order_by("group")
                    .values_list("group", flat=True))
    info["available"] = bool(siblings)
    info["groups"] = " + ".join(sorted([section.group, *siblings]))
    return info


def family_duplicates(*, term, subject, section, exclude_pk=None):
    """Live offerings that would make the same students take ``subject`` twice.

    For a whole block that is its groups' own offerings of the subject (they
    are merged into the shared class). For a group it is the whole block's
    offering of it (the group already takes it, shared).
    """
    if term is None or subject is None or section is None:
        return SubjectOffering.objects.none()
    qs = SubjectOffering.objects.filter(term=term, subject=subject, is_active=True)
    if section.is_whole_block:
        qs = qs.filter(section__in=section.groups())
    else:
        qs = qs.filter(section__in=section.family().filter(group=""))
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.select_related("subject", "section", "term")


def restore_blocker(offering):
    """Why an archived offering cannot be restored, or None.

    Restoring a group's own offering after it was merged into a shared class
    (or the other way round) would double the subject for those students.
    """
    clash = list(family_duplicates(term=offering.term, subject=offering.subject,
                                   section=offering.section, exclude_pk=offering.pk))
    if not clash:
        return None
    if offering.section.is_whole_block:
        who = ", ".join(o.section.code for o in clash)
        return f"{who} already take {offering.subject.code} on their own"
    return (f"{offering.subject.code} is a shared class of "
            f"{offering.section.block_code} now")


@transaction.atomic
def ensure_whole_block(section):
    """The whole-block row of ``section``'s family, filed if it is missing.

    Returns ``(block, created)``. A new row takes the group's department, and
    its head count is left blank so :attr:`Section.headcount` adds the groups'
    counts up (and keeps doing so as they change). An archived whole block is
    restored, because a shared class needs it live.
    """
    if section.is_whole_block:
        return section, False
    block = (Section.objects.select_for_update()
             .filter(program=section.program, year_level=section.year_level,
                     block=section.block, group="").first())
    if block is None:
        block = Section.objects.create(
            program=section.program, year_level=section.year_level,
            block=section.block, group="", department=section.department)
        return block, True
    if not block.is_active:
        block.is_active = True
        block.save(update_fields=["is_active", "updated_at"])
    return block, False


@dataclass
class FiledOffering:
    offering: SubjectOffering
    created_block: Section | None = None
    merged: list = field(default_factory=list)
    unscheduled: int = 0

    @property
    def message_suffix(self):
        bits = []
        if self.created_block is not None:
            bits.append(f"Whole block {self.created_block.code} was added for it.")
        if self.merged:
            bits.append("Merged in and archived: "
                        + ", ".join(f"{o.section.code}'s own offering" for o in self.merged)
                        + ".")
        if self.unscheduled:
            bits.append(f"{self.unscheduled} placed meeting(s) were taken off the "
                        "timetable; run the auto-scheduler to place it again.")
        return (" " + " ".join(bits)) if bits else ""


@transaction.atomic
def file_offering(*, offering, share_with_groups=False, user=None):
    """Save an offering, as a shared class of every group when asked.

    * ``share_with_groups`` on a group offering re-files it against the whole
      block (filing that row if it is missing), so the auto-scheduler places
      one set of meetings that every group attends.
    * Any group's own live offering of the same subject that term is merged:
      archived and taken off the timetable, so nobody takes it twice.
    * An edit that moves the offering to another section takes its placed
      meetings off the timetable. They were checked against the old section's
      week, not the new one's.
    """
    result = FiledOffering(offering=offering)
    if share_with_groups and offering.section_id and not offering.section.is_whole_block:
        block, created = ensure_whole_block(offering.section)
        offering.section = block
        if created:
            result.created_block = block

    if offering.pk:
        old_section_id = (SubjectOffering.objects.filter(pk=offering.pk)
                          .values_list("section_id", flat=True).first())
        if old_section_id is not None and old_section_id != offering.section_id:
            result.unscheduled = len(unschedule_offering(offering, user=user))
    offering.save()

    if offering.section.is_whole_block:
        for dup in family_duplicates(term=offering.term, subject=offering.subject,
                                     section=offering.section,
                                     exclude_pk=offering.pk).select_for_update():
            dup.is_active = False
            dup.save(update_fields=["is_active", "updated_at"])
            unschedule_offering(dup, user=user)
            result.merged.append(dup)
    return result


# ---------------------------------------------------------------------------
# Swapping
# ---------------------------------------------------------------------------

class SwapError(ValueError):
    """A swap was refused because one of the two blocks would clash after it."""


@transaction.atomic
def swap_blocks(block_a, block_b, *, include_rooms=False, user=None):
    """Exchange the day/time (and optionally the room) of two placed blocks.

    Both blocks are re-checked against all three dimensions *after* the
    exchange, with each other excluded from the check — otherwise the two would
    always appear to clash with each other's old slot. Any clash raises
    :class:`SwapError` and the transaction rolls back, so the timetable is never
    left half-swapped.
    """
    if block_a.pk == block_b.pk:
        raise SwapError("Pick two different schedule blocks.")
    if block_a.term_id != block_b.term_id:
        raise SwapError("Both blocks must be in the same term.")

    a = ScheduleBlock.objects.select_for_update().select_related(
        "room__building", "section", "instructor", "term").get(pk=block_a.pk)
    b = ScheduleBlock.objects.select_for_update().select_related(
        "room__building", "section", "instructor", "term").get(pk=block_b.pk)

    a.day_of_week, b.day_of_week = b.day_of_week, a.day_of_week
    a.start_time, b.start_time = b.start_time, a.start_time
    a.end_time, b.end_time = b.end_time, a.end_time
    if include_rooms:
        a.room, b.room = b.room, a.room

    for moved, other in ((a, b), (b, a)):
        blockers = slot_blockers(
            term=moved.term, section=moved.section, instructor=moved.instructor,
            room=moved.room, day_of_week=moved.day_of_week,
            start_time=moved.start_time, end_time=moved.end_time,
            exclude_block_id=moved.pk,
        )
        # The partner is mid-swap: its row still holds the old slot, so ignore
        # it as a blocker. Both end states are checked, so a genuine clash
        # between the two still surfaces.
        blockers = {dim: [c for c in clash if c.pk != other.pk]
                    for dim, clash in blockers.items()}
        blockers = {dim: clash for dim, clash in blockers.items() if clash}
        if blockers:
            raise SwapError(f"'{moved.title}' cannot move there. "
                            + blocker_summary(blockers))

    a.save(update_fields=["day_of_week", "start_time", "end_time", "room", "updated_at"])
    b.save(update_fields=["day_of_week", "start_time", "end_time", "room", "updated_at"])
    return a, b


@transaction.atomic
def move_block(block, *, day_of_week=None, start_time=None, end_time=None, room=None):
    """Move one block to a new day/time/room, all three dimensions validated."""
    locked = ScheduleBlock.objects.select_for_update().select_related(
        "room__building", "section", "instructor", "term").get(pk=block.pk)
    if day_of_week is not None:
        locked.day_of_week = int(day_of_week)
    if start_time is not None:
        locked.start_time = start_time
    if end_time is not None:
        locked.end_time = end_time
    if room is not None:
        locked.room = room
    from datetime import datetime
    for attr in ("start_time", "end_time"):
        value = getattr(locked, attr)
        if isinstance(value, str):
            try:
                setattr(locked, attr, datetime.strptime(value[:5], "%H:%M").time())
            except ValueError:
                raise SwapError("Use HH:MM for the start and end time.")
    try:
        check_grid(locked.day_of_week, locked.start_time, locked.end_time)
    except PlaceError as e:
        raise SwapError(str(e)) from e
    if room is not None and (not room.is_active or room.status in (
            Room.Status.MAINTENANCE, Room.Status.INACTIVE)):
        raise SwapError("Selected room is not usable for scheduling.")
    blockers = slot_blockers(
        term=locked.term, section=locked.section, instructor=locked.instructor,
        room=locked.room, day_of_week=locked.day_of_week,
        start_time=locked.start_time, end_time=locked.end_time,
        exclude_block_id=locked.pk,
    )
    if blockers:
        raise SwapError(f"'{locked.title}' cannot move there. " + blocker_summary(blockers))
    locked.save(update_fields=["day_of_week", "start_time", "end_time", "room", "updated_at"])
    return locked


# ---------------------------------------------------------------------------
# Manual placement (the hand-made twin of the auto-scheduler)
# ---------------------------------------------------------------------------
#
# The section and room timetables let an admin place a class by hand. It goes
# through the same objects the auto-scheduler writes - a SubjectOffering and a
# ScheduleBlock linked to it - so the Subject Offerings page, both timetables
# and the exports always agree. A subject can only be placed when it is in the
# section's curriculum for the term (``curriculum.subject_allowed``).

class PlaceError(ValueError):
    """A manual placement was refused; the message says why."""


def check_grid(day_of_week, start_time, end_time):
    """The timetable grid is Mon-Sat, 07:00-21:00 in half-hour steps."""
    if day_of_week is None or int(day_of_week) not in SCHEDULABLE_DAYS:
        raise PlaceError("Pick a day from Monday to Saturday.")
    if end_time <= start_time:
        raise PlaceError("End time must be after start time.")
    if start_time < DAY_START or end_time > DAY_END:
        raise PlaceError("Classes run between 7:00 AM and 9:00 PM.")
    if minutes_of(start_time) % SLOT_MINUTES or minutes_of(end_time) % SLOT_MINUTES:
        raise PlaceError("Use half-hour times (for example 9:00 or 9:30).")


def placeable_subjects(*, section, term):
    """Subjects an admin may place for ``section`` in ``term``, with progress.

    Narrowed to the curriculum slot (every live subject only when the program
    has no curriculum yet). Unlike the offering form this keeps subjects that
    are already offered: placing a second meeting is the normal case. It drops
    the ones the family already takes as a shared class (or, for a whole
    block, that a group took on its own), which would double the subject.
    Returns ``(rows, restricted)``.
    """
    from .curriculum import slot_entries
    if section is None or term is None:
        return [], False
    in_slot = set(slot_entries(section.program, section.year_level, term)
                  .values_list("subject_id", flat=True))
    subjects = Subject.objects.filter(is_active=True)
    if in_slot:
        subjects = subjects.filter(pk__in=in_slot)
    offerings = {
        o.subject_id: o for o in SubjectOffering.objects.filter(
            term=term, section=section, is_active=True).select_related("instructor")}
    family = (section.groups() if section.is_whole_block
              else section.family().filter(group=""))
    family_taken = set(SubjectOffering.objects.filter(term=term, is_active=True,
                                                      section__in=family)
                       .values_list("subject_id", flat=True))
    rows = []
    for s in subjects.order_by("code"):
        if s.pk in family_taken and s.pk not in offerings:
            continue
        o = offerings.get(s.pk)
        rows.append({
            "id": s.pk, "code": s.code, "label": str(s),
            "offering_id": o.pk if o else None,
            "scheduled": o.scheduled_count if o else 0,
            "meetings": o.meetings_per_week if o else 0,
            "minutes": o.minutes_per_meeting if o else 90,
            "instructor_id": o.instructor_id if o and o.instructor_id else "",
            "instructor": o.instructor.full_name if o and o.instructor_id else "",
            "lecture_lab": bool(o and o.is_lecture_lab),
        })
    return rows, bool(in_slot)


@transaction.atomic
def place_class(*, term, section, subject, room, day_of_week, start_time, end_time,
                instructor=None, user=None):
    """Put one meeting of ``subject`` on ``section``'s week, by hand.

    Finds (or files) the section's offering for the term, links a new block to
    it and validates all three clash dimensions plus the room and term rules.
    Returns ``(block, offering, created_offering)``.
    """
    from .curriculum import subject_allowed
    from .services import ScheduleConflictError, validate_block

    if term is None or section is None or subject is None or room is None:
        raise PlaceError("Pick a term, section, subject and room.")
    if not section.is_active:
        raise PlaceError(f"{section.code} is archived.")
    if not subject.is_active:
        raise PlaceError(f"{subject.code} is archived.")
    reason = subject_allowed(section=section, term=term, subject=subject)
    if reason:
        raise PlaceError(reason)
    check_grid(day_of_week, start_time, end_time)

    offering = (SubjectOffering.objects.select_for_update()
                .filter(term=term, subject=subject, section=section).first())
    created = False
    if offering is None:
        if family_duplicates(term=term, subject=subject, section=section).exists():
            raise PlaceError(f"{subject.code} is already taken by {section.code}'s "
                             "block or groups. Edit it under Subject Offerings.")
        offering = SubjectOffering(
            term=term, subject=subject, section=section, instructor=instructor,
            meetings_per_week=1,
            minutes_per_meeting=minutes_of(end_time) - minutes_of(start_time),
            created_by=user)
        offering.save()
        created = True
    elif not offering.is_active:
        blocker = restore_blocker(offering)
        if blocker:
            raise PlaceError(blocker)
        offering.is_active = True
        offering.archived_at = None
        offering.save()

    kind = ""
    if offering.is_lecture_lab:
        kind = (ScheduleBlock.MeetingKind.LAB if room.room_type == Room.RoomType.LABORATORY
                else ScheduleBlock.MeetingKind.LECTURE)
        if offering.scheduled_blocks().filter(meeting_kind=kind).exists():
            raise PlaceError(
                f"{subject.code} already has its "
                f"{ScheduleBlock.MeetingKind(kind).label.lower()} placed. "
                "Move that one, or remove it first.")
    elif offering.scheduled_count >= (offering.meetings_per_week or 0) and not created:
        # One more meeting than planned: the plan follows what was placed.
        offering.meetings_per_week = offering.scheduled_count + 1
        offering.save(update_fields=["meetings_per_week", "updated_at"])

    teacher = offering.instructor
    if teacher is None and instructor is not None:
        offering.instructor = teacher = instructor
        offering.save(update_fields=["instructor", "updated_at"])

    label = f" ({ScheduleBlock.MeetingKind(kind).label})" if kind else ""
    block = ScheduleBlock(
        term=term, room=room, subject=subject, section=section, offering=offering,
        title=f"{subject.code} · {section.code}{label}", instructor=teacher,
        meeting_kind=kind, day_of_week=int(day_of_week), start_time=start_time,
        end_time=end_time, is_active=True, created_by=user)
    try:
        validate_block(block)
    except ScheduleConflictError as e:
        raise PlaceError("Room busy - " + describe_conflicts(e.conflicts)) from e
    except ValueError as e:
        raise PlaceError(str(e)) from e
    blockers = slot_blockers(
        term=term, section=section, instructor=teacher, room=None,
        day_of_week=block.day_of_week, start_time=start_time, end_time=end_time)
    if blockers:
        raise PlaceError(blocker_summary(blockers))
    block.save()
    return block, offering, created


# ---------------------------------------------------------------------------
# Section timetable + export
# ---------------------------------------------------------------------------

def section_scope_ids(section, *, include_groups=True):
    """The section ids a timetable/export covers.

    Asking for the whole block ``BSIT-4D`` with ``include_groups`` returns the
    block *and* its groups, because a 4D student attends both the block-wide
    subjects and their own group's. Asking for ``BSIT-4DG1`` returns G1 plus the
    whole block, never G2 — that is G1's actual week.
    """
    if section is None:
        return []
    if section.is_whole_block:
        # Without the groups, a whole block is only its own classes — the
        # block-wide subjects, which is the export for those alone.
        return (list(section.family().values_list("pk", flat=True))
                if include_groups else [section.pk])
    # A group's week is the group plus the block-wide classes, never a sibling.
    return sorted(section.clashing_section_ids())


def section_blocks(section, term, *, include_groups=True):
    """The placed blocks that make up a section's week, in reading order."""
    ids = section_scope_ids(section, include_groups=include_groups)
    if not ids or term is None:
        return []
    return list(
        ScheduleBlock.objects
        .exclude(offering__is_active=False)
        .filter(term=term, section_id__in=ids, is_active=True, day_of_week__lte=6)
        .select_related("room__building", "subject", "instructor", "section",
                        "offering", "term")
        .order_by("day_of_week", "start_time", "room__room_code")
    )


def get_section_timetable(section, term, *, include_groups=True):
    """Grid data for the section timetable page.

    Mirrors ``services.get_room_timetable`` — same 07:00–21:00 half-hour window,
    same ``row_start``/``span``/``col`` contract — so the two pages can share one
    grid template. Blocks are coloured per subject, and overlapping group blocks
    are laid out side by side within their day column.
    """
    from .services import PALETTES

    if section is None or term is None:
        return {"blocks": [], "total_blocks": 0, "total_hours": 0.0, "days_used": 0}

    START_MINUTE = minutes_of(DAY_START)
    END_MINUTE = minutes_of(DAY_END)

    raw = section_blocks(section, term, include_groups=include_groups)
    processed, total_minutes = [], 0
    labels = group_labels([b.section for b in raw if b.section_id])

    # Lane assignment: G1 and G2 can legitimately meet at the same time, so a
    # day column may need two side-by-side lanes. Per day, give each block the
    # lowest lane whose blocks it does not overlap.
    lanes_by_day = {}
    for block in raw:
        start_min = minutes_of(block.start_time)
        end_min = minutes_of(block.end_time)
        if end_min <= start_min:
            continue
        total_minutes += end_min - start_min

        lanes = lanes_by_day.setdefault(block.day_of_week, [])
        lane_index = None
        for i, lane in enumerate(lanes):
            if all(end_min <= s or start_min >= e for s, e in lane):
                lane_index = i
                lane.append((start_min, end_min))
                break
        if lane_index is None:
            lanes.append([(start_min, end_min)])
            lane_index = len(lanes) - 1

        clamped_start = max(start_min, START_MINUTE)
        clamped_end = min(end_min, END_MINUTE)
        row_start = (clamped_start - START_MINUTE) // SLOT_MINUTES + 1
        row_end = (clamped_end - START_MINUTE + SLOT_MINUTES - 1) // SLOT_MINUTES + 1

        key = block.subject.code if block.subject_id else block.title
        palette = PALETTES[sum(ord(c) for c in key) % len(PALETTES)]

        processed.append({
            "block": block,
            "id": block.pk,
            "title": block.title,
            "subject_code": block.subject.code if block.subject_id else "",
            "subject_title": block.subject.title if block.subject_id else "",
            "section_code": block.section.code if block.section_id else "",
            "is_group_block": bool(block.section_id and block.section.group),
            # "G1 + G2" when every group of the block attends this one together.
            "shared_groups": shared_label(block.section, labels) if block.section_id else "",
            "room_label": str(block.room) if block.room_id else "",
            "instructor_name": (block.instructor.full_name if block.instructor_id
                                else "Unassigned"),
            "day_of_week": block.day_of_week,
            "day_label": block.get_day_of_week_display(),
            "start_time_str": f"{block.start_time:%H:%M}",
            "end_time_str": f"{block.end_time:%H:%M}",
            "time_display": _fmt_time_range(block.start_time, block.end_time),
            "row_start": row_start,
            "row_end": row_end,
            "span": max(1, row_end - row_start),
            "col": block.day_of_week + 2,
            "lane": lane_index,
            "palette": palette,
            "json": json.dumps({
                "id": block.pk, "title": block.title,
                "subject_id": block.subject_id or "",
                "subject_code": block.subject.code if block.subject_id else "",
                "section_id": block.section_id or "",
                "section_code": block.section.code if block.section_id else "",
                "offering_id": block.offering_id or "",
                "instructor_id": block.instructor_id or "",
                "instructor": (block.instructor.full_name if block.instructor_id else ""),
                "room_id": block.room_id, "term_id": block.term_id,
                "day_of_week": block.day_of_week,
                "start_time": f"{block.start_time:%H:%M}",
                "end_time": f"{block.end_time:%H:%M}",
                "is_active": block.is_active,
            }),
        })

    # How many lanes each day needs, so the template can split the column.
    lane_counts = {day: len(lanes) for day, lanes in lanes_by_day.items()}
    for item in processed:
        item["lane_count"] = lane_counts.get(item["day_of_week"], 1)

    return {
        "blocks": processed,
        "total_blocks": len(processed),
        "total_hours": round(total_minutes / 60, 1),
        "days_used": len(lanes_by_day),
    }
