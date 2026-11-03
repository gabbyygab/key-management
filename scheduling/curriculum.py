"""Program curricula: which subjects a section takes in which semester.

A curriculum slot is (program, year level, semester), e.g. ``BSIT · 4th year ·
1st Semester``, holding the subjects that year of the program takes that
semester. A section knows its program and year level and a term knows its
semester, so together they pick exactly one slot. That is what the offering
form uses: ``offerable_subjects`` narrows the subject picker to the slot and
drops what is already offered to the section that term.

Rules live here; views only translate HTTP.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Q

from .models import (CurriculumSubject, Section, Subject, SubjectOffering, Term,
                     year_level_label)

# The semesters in display order (1st, 2nd, Summer). These are the Term
# choices, so the curriculum always has the same columns the term page offers.
SEMESTERS = list(Term.Semester.choices)
DEFAULT_YEAR_LEVELS = (1, 2, 3, 4)
MAX_YEAR_LEVEL = 5


def slot_label(program, year_level, semester):
    sem = dict(SEMESTERS).get(semester, "")
    parts = [program, year_level_label(int(year_level))]
    if sem:
        parts.append(sem)
    return " · ".join(parts)


def normalise_program(program):
    return (program or "").strip().upper()


def known_programs():
    """Every program code in use: on a live section or in a curriculum."""
    codes = set(Section.objects.filter(is_active=True).values_list("program", flat=True))
    codes |= set(CurriculumSubject.objects.filter(is_active=True)
                 .values_list("program", flat=True))
    return sorted(c for c in codes if c)


def slot_entries(program, year_level, semester=None):
    """Live curriculum rows of one slot. ``semester=None`` means every semester
    of that year (a term filed before terms had a semester)."""
    qs = CurriculumSubject.objects.filter(
        program=normalise_program(program), year_level=year_level, is_active=True,
        subject__is_active=True)
    if semester:
        qs = qs.filter(semester=semester)
    return qs


@transaction.atomic
def add_subjects(*, program, year_level, semester, subjects, user=None):
    """Put ``subjects`` into one curriculum slot.

    Returns ``(added, already)``: the subjects newly in the slot (a row created
    or an archived one restored) and the ones that were there already.
    """
    program = normalise_program(program)
    added, already = [], []
    existing = {
        row.subject_id: row for row in CurriculumSubject.objects.select_for_update().filter(
            program=program, year_level=year_level, semester=semester,
            subject__in=subjects)
    }
    for subject in subjects:
        row = existing.get(subject.pk)
        if row is None:
            CurriculumSubject.objects.create(
                program=program, year_level=year_level, semester=semester,
                subject=subject, created_by=user)
            added.append(subject)
        elif not row.is_active:
            row.is_active = True
            row.save(update_fields=["is_active", "updated_at"])
            added.append(subject)
        else:
            already.append(subject)
    return added, already


@transaction.atomic
def remove_subject(entry):
    """Take a subject out of its curriculum slot (archives the row).

    Offerings already filed for it stay: the curriculum only steers what the
    offering form suggests next.
    """
    entry = CurriculumSubject.objects.select_for_update().get(pk=entry.pk)
    if entry.is_active:
        entry.is_active = False
        entry.save(update_fields=["is_active", "updated_at"])
    return entry


@dataclass
class OfferableSubjects:
    """What the offering form may pick for one section in one term."""

    subjects: list = field(default_factory=list)
    # True when a curriculum exists for the slot and the list is narrowed to it.
    restricted: bool = False
    slot: str = ""
    program: str = ""
    year_level: int | None = None
    semester: str = ""
    already_offered: int = 0

    @property
    def message(self):
        if not self.slot:
            return "Pick a section to see the subjects of its curriculum."
        if not self.restricted:
            return (f"No curriculum set for {self.slot}, so every subject is listed. "
                    "Add one on the Curriculum page to narrow this list.")
        if not self.subjects:
            return (f"Every {self.slot} subject is already offered to this section "
                    "this term.")
        msg = f"{self.slot} curriculum: {len(self.subjects)} subject(s) left to offer"
        if self.already_offered:
            msg += f", {self.already_offered} already offered"
        return msg + "."


def offerable_subjects(*, section, term, keep_subject_id=None):
    """The subjects that may still be offered to ``section`` in ``term``.

    * Narrowed to the section's curriculum slot (its program + year level and
      the term's semester) when that slot has any subjects; otherwise every
      live subject, so a program without a curriculum is not locked out.
    * Minus every subject already offered to the section that term, archived
      offerings included, since (term, subject, section) is unique.
    * ``keep_subject_id`` stays in the list regardless: the subject an existing
      offering already has, so editing it never empties its own field.
    """
    base = Subject.objects.filter(is_active=True).select_related("department").order_by("code")
    if section is None:
        return OfferableSubjects(subjects=list(base))

    semester = getattr(term, "semester", "") or ""
    result = OfferableSubjects(
        program=section.program, year_level=section.year_level, semester=semester,
        slot=slot_label(section.program, section.year_level, semester))

    in_slot = set(slot_entries(section.program, section.year_level, semester or None)
                  .values_list("subject_id", flat=True))
    taken = set()
    if term is not None:
        # A group also already "has" every subject its whole block takes as a
        # shared class, so those are not offered to it a second time. (A whole
        # block still lists its groups' own subjects: filing one there merges
        # the groups into a shared class, see planner.file_offering.)
        who = [section.pk]
        if not section.is_whole_block:
            who += list(section.family().filter(group="", is_active=True)
                        .values_list("pk", flat=True))
        taken = set(SubjectOffering.objects.filter(term=term, section__in=who)
                    .exclude(~Q(section=section) & Q(is_active=False))
                    .values_list("subject_id", flat=True))
    taken.discard(keep_subject_id)

    qs = base
    if in_slot:
        result.restricted = True
        qs = qs.filter(Q(pk__in=in_slot) | Q(pk=keep_subject_id or 0))
        result.already_offered = len(in_slot & taken)
    result.subjects = [s for s in qs if s.pk not in taken]
    return result


def subject_allowed(*, section, term, subject):
    """None if ``subject`` belongs to the curriculum of ``section`` in ``term``
    (or that slot has no curriculum), else the reason it does not."""
    if section is None or subject is None:
        return None
    semester = getattr(term, "semester", "") or ""
    in_slot = slot_entries(section.program, section.year_level, semester or None)
    if in_slot.exists() and not in_slot.filter(subject=subject).exists():
        slot = slot_label(section.program, section.year_level, semester)
        return f"{subject.code} is not in the {slot} curriculum."
    return None


def program_board(program, *, highlight_semester=""):
    """The curriculum page for one program: a card per year level, a column
    per semester. Summer only shows for a year that uses it (or while the
    highlighted term is a summer term)."""
    program = normalise_program(program)
    rows = list(CurriculumSubject.objects.filter(program=program, is_active=True)
                .select_related("subject__department")
                .order_by("year_level", "semester", "subject__code"))
    years = set(DEFAULT_YEAR_LEVELS)
    years |= {r.year_level for r in rows}
    years |= set(Section.objects.filter(program=program, is_active=True)
                 .values_list("year_level", flat=True))
    by_slot = {}
    for r in rows:
        by_slot.setdefault((r.year_level, r.semester), []).append(r)

    board = []
    for year in sorted(years):
        semesters = []
        for code, label in SEMESTERS:
            entries = by_slot.get((year, code), [])
            if (code == Term.Semester.SUMMER and not entries
                    and highlight_semester != Term.Semester.SUMMER):
                continue
            semesters.append({"code": code, "label": label, "entries": entries,
                              "is_current": code == highlight_semester})
        board.append({"year": year, "label": year_level_label(year),
                      "semesters": semesters,
                      "count": sum(len(s["entries"]) for s in semesters)})
    return board


def subject_slots():
    """``{subject_id: ["BSIT|4|1ST", ...]}``: every live slot each subject is in.

    The add modal uses it to grey out subjects already in the slot being
    edited, and to re-check that as the admin changes the slot fields.
    """
    out = {}
    for sid, program, year, sem in (CurriculumSubject.objects.filter(is_active=True)
                                    .values_list("subject_id", "program", "year_level",
                                                 "semester")):
        out.setdefault(sid, []).append(f"{program}|{year}|{sem}")
    return out
