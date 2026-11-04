"""Program curricula: which subjects a section takes in a term.

Every term owns its curriculum. A slot is (term, program, year level), e.g.
``2026-2027 1st Semester / BSIT / 4th year``, holding the subjects that year of
the program takes that term. A new term starts as a copy of the previous
same-semester term's curriculum (``copy_curriculum``); after that each term is
edited on its own, so adding or dropping a subject in one term never touches
another. The offering form uses it: ``offerable_subjects`` narrows the subject
picker to the section's slot in the term and drops what is already offered.

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


def slot_label(program, year_level, term=None):
    parts = [program, year_level_label(int(year_level))]
    if term is not None:
        parts.append(str(term))
    return " · ".join(parts)


def normalise_program(program):
    return (program or "").strip().upper()


def known_programs():
    """Every program code in use: on a live section or in a curriculum."""
    codes = set(Section.objects.filter(is_active=True).values_list("program", flat=True))
    codes |= set(CurriculumSubject.objects.filter(is_active=True)
                 .values_list("program", flat=True))
    return sorted(c for c in codes if c)


def slot_entries(program, year_level, term):
    """Live curriculum rows of one slot of one term (none without a term)."""
    if term is None:
        return CurriculumSubject.objects.none()
    return CurriculumSubject.objects.filter(
        term=term, program=normalise_program(program), year_level=year_level,
        is_active=True, subject__is_active=True)


@transaction.atomic
def add_subjects(*, program, year_level, term, subjects, user=None):
    """Put ``subjects`` into one curriculum slot of ``term``.

    Returns ``(added, already)``: the subjects newly in the slot (a row created
    or an archived one restored) and the ones that were there already.
    """
    program = normalise_program(program)
    added, already = [], []
    existing = {
        row.subject_id: row for row in CurriculumSubject.objects.select_for_update().filter(
            term=term, program=program, year_level=year_level, subject__in=subjects)
    }
    for subject in subjects:
        row = existing.get(subject.pk)
        if row is None:
            CurriculumSubject.objects.create(
                term=term, program=program, year_level=year_level,
                semester=term.semester, subject=subject, created_by=user)
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


def previous_term(term):
    """The term whose curriculum a new ``term`` starts from: the most recent
    other term of the same semester that has a curriculum."""
    if not term.semester:
        return None
    for other in (Term.objects.filter(semester=term.semester).exclude(pk=term.pk)
                  .order_by("-start_date", "-pk")):
        if other.curriculum.filter(is_active=True).exists():
            return other
    return None


@transaction.atomic
def copy_curriculum(source, target):
    """Copy ``source``'s live curriculum into ``target``, skipping subjects the
    target already has. Returns how many rows were added."""
    have = set(target.curriculum.values_list("program", "year_level", "subject_id"))
    rows = [
        CurriculumSubject(term=target, program=e.program, year_level=e.year_level,
                          semester=target.semester, subject_id=e.subject_id,
                          created_by_id=e.created_by_id)
        for e in source.curriculum.filter(is_active=True)
        if (e.program, e.year_level, e.subject_id) not in have]
    CurriculumSubject.objects.bulk_create(rows)
    return len(rows)


def seed_from_previous(term):
    """Give a freshly created ``term`` the previous term's curriculum."""
    source = previous_term(term)
    return copy_curriculum(source, term) if source else 0


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

    * Narrowed to the section's curriculum slot in that term (its program +
      year level) when that slot has any subjects; otherwise every live
      subject, so a program without a curriculum is not locked out.
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
        slot=slot_label(section.program, section.year_level, term))

    in_slot = set(slot_entries(section.program, section.year_level, term)
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


def section_completion(term, sections=None):
    """``[(section, complete)]`` for the offering form's Section picker.

    ``complete`` is True when the section's curriculum slot for ``term`` exists
    and every subject in it is already offered, so there is nothing left to
    schedule. Sections that still have subjects come first (code order), the
    completed ones after them.
    """
    if sections is None:
        sections = Section.objects.filter(is_active=True)
    sections = sorted(sections, key=lambda s: s.code)
    rows = []
    for section in sections:
        done = False
        if term is not None:
            offer = offerable_subjects(section=section, term=term)
            done = offer.restricted and not offer.subjects
        rows.append((section, done))
    rows.sort(key=lambda r: r[1])  # stable: pending first, code order kept
    return rows


def subject_allowed(*, section, term, subject):
    """None if ``subject`` belongs to the curriculum of ``section`` in ``term``
    (or that slot has no curriculum), else the reason it does not."""
    if section is None or subject is None:
        return None
    semester = getattr(term, "semester", "") or ""
    in_slot = slot_entries(section.program, section.year_level, term)
    if in_slot.exists() and not in_slot.filter(subject=subject).exists():
        slot = slot_label(section.program, section.year_level, term)
        return f"{subject.code} is not in the {slot} curriculum."
    return None


def program_board(program, term):
    """The curriculum page for one program in one term: a card per year level
    holding the term's semester column."""
    program = normalise_program(program)
    if term is None:
        return []
    rows = list(CurriculumSubject.objects.filter(term=term, program=program, is_active=True)
                .select_related("subject__department")
                .order_by("year_level", "subject__code"))
    years = set(DEFAULT_YEAR_LEVELS)
    years |= {r.year_level for r in rows}
    years |= set(Section.objects.filter(program=program, is_active=True)
                 .values_list("year_level", flat=True))
    by_year = {}
    for r in rows:
        by_year.setdefault(r.year_level, []).append(r)

    label = dict(SEMESTERS).get(term.semester, str(term))
    board = []
    for year in sorted(years):
        entries = by_year.get(year, [])
        board.append({"year": year, "label": year_level_label(year),
                      "semesters": [{"code": term.semester, "label": label,
                                     "entries": entries, "is_current": False}],
                      "count": len(entries)})
    return board


def subject_slots():
    """``{subject_id: ["BSIT|4|<term pk>", ...]}``: every live slot each subject is in.

    The add modal uses it to grey out subjects already in the slot being
    edited, and to re-check that as the admin changes the slot fields.
    """
    out = {}
    for sid, program, year, term_id in (CurriculumSubject.objects.filter(is_active=True)
                                        .values_list("subject_id", "program", "year_level",
                                                     "term_id")):
        out.setdefault(sid, []).append(f"{program}|{year}|{term_id}")
    return out
