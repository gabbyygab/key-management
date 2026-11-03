from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from accounts.retention import ArchiveClockMixin


class Term(ArchiveClockMixin):
    """An academic term, filed as ``2025-2026 1st Semester``.

    ``academic_year`` + ``semester`` are the structured record; ``name`` is the
    display label built from them (and stays the unique key every older row and
    query already uses). Terms that predate the two fields keep their free-text
    name and an empty academic year.
    """

    class Semester(models.TextChoices):
        FIRST = "1ST", "1st Semester"
        SECOND = "2ND", "2nd Semester"
        SUMMER = "SUMMER", "Summer"

    name = models.CharField(max_length=100, unique=True, blank=True)
    academic_year = models.CharField(max_length=9, blank=True,
                                     help_text="Two consecutive years, e.g. 2025-2026.")
    semester = models.CharField(max_length=6, choices=Semester.choices, blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=True)
    # See facilities.Building.archived_at.
    archived_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_date", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["academic_year", "semester"], name="unique_term_year_semester",
                condition=~Q(academic_year="")),
        ]

    @staticmethod
    def build_name(academic_year, semester):
        label = dict(Term.Semester.choices).get(semester, "")
        return f"{academic_year} {label}".strip()

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date must be after the start date."})
        self.academic_year = (self.academic_year or "").strip()
        if self.academic_year:
            parts = self.academic_year.split("-")
            if not (len(parts) == 2 and all(p.isdigit() and len(p) == 4 for p in parts)
                    and int(parts[1]) == int(parts[0]) + 1):
                raise ValidationError({
                    "academic_year": "Use two consecutive years, like 2025-2026."})
            if not self.semester:
                raise ValidationError({"semester": "Pick the semester."})

    def save(self, *args, **kwargs):
        if self.academic_year and self.semester:
            self.name = self.build_name(self.academic_year, self.semester)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Subject(ArchiveClockMixin):
    code = models.CharField(max_length=50, unique=True)
    title = models.CharField(max_length=200)
    department = models.ForeignKey("accounts.Department", null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="subjects")
    is_active = models.BooleanField(default=True)
    # See facilities.Building.archived_at.
    archived_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.title}"


class Section(ArchiveClockMixin):
    """A class section — ``BSIT-4D``, or one of its groups ``BSIT-4DG1`` / ``BSIT-4DG2``.

    A section is identified by four parts: program (``BSIT``), year level
    (``4``), block (``D``) and an optional group (``G1``). The whole-block row
    has ``group=""``; its groups are the rows that share program/year/block and
    carry a group label. There is deliberately **no parent foreign key** — the
    family is the (program, year_level, block) triple, which keeps renaming a
    block from orphaning its groups.

    What that buys the scheduler: ``BSIT-4D`` (the whole block) cannot be in two
    places at once, so it clashes with *both* of its groups, while ``BSIT-4DG1``
    and ``BSIT-4DG2`` may meet at the same time in different rooms. See
    ``clashing_section_ids`` below, which is what ``planner.py`` enforces.
    """

    program = models.CharField(max_length=20, help_text="Program code, e.g. BSIT.")
    year_level = models.PositiveSmallIntegerField(help_text="Year level, e.g. 4.")
    block = models.CharField(max_length=10, help_text="Block letter, e.g. D.")
    group = models.CharField(max_length=10, blank=True,
                             help_text="Group label such as G1 or G2. Leave blank for the whole block.")
    department = models.ForeignKey("accounts.Department", null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="sections")
    student_count = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Used to pick a room with enough seats.")
    is_active = models.BooleanField(default=True)
    # See facilities.Building.archived_at.
    archived_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["program", "year_level", "block", "group"]
        constraints = [
            models.UniqueConstraint(fields=["program", "year_level", "block", "group"],
                                    name="unique_section_code"),
        ]
        indexes = [
            models.Index(fields=["program", "year_level", "block"],
                         name="section_family_idx"),
        ]

    def clean(self):
        super().clean()
        self.program = (self.program or "").strip().upper()
        self.block = (self.block or "").strip().upper()
        self.group = (self.group or "").strip().upper()
        if self.group and not self.group.startswith("G"):
            raise ValidationError({"group": "A group label looks like G1 or G2."})

    def save(self, *args, **kwargs):
        self.program = (self.program or "").strip().upper()
        self.block = (self.block or "").strip().upper()
        self.group = (self.group or "").strip().upper()
        return super().save(*args, **kwargs)

    @property
    def block_code(self):
        """``BSIT-4D`` — the whole block this section belongs to."""
        return f"{self.program}-{self.year_level}{self.block}"

    @property
    def code(self):
        """``BSIT-4DG1``, or ``BSIT-4D`` for a whole block."""
        return f"{self.block_code}{self.group}"

    @property
    def is_whole_block(self):
        return not self.group

    def family(self):
        """Every section sharing this program/year/block, groups included."""
        return Section.objects.filter(program=self.program, year_level=self.year_level,
                                      block=self.block)

    def groups(self):
        """The split groups of this block (empty for a group row)."""
        return self.family().exclude(group="")

    def whole_block(self):
        """The whole-block row of this family (``BSIT-4D``), or None if it was
        never filed. A whole block is its own whole block."""
        if self.is_whole_block:
            return self
        return self.family().filter(group="").first()

    def sibling_groups(self):
        """The other live groups of this block (never this row itself)."""
        return self.groups().filter(is_active=True).exclude(pk=self.pk)

    @property
    def headcount(self):
        """Seats the class needs: the recorded count, else (for a whole block)
        the groups' counts added up, so a shared class gets a big enough room."""
        if self.student_count or not self.is_whole_block:
            return self.student_count
        counts = [c for c in self.groups().filter(is_active=True)
                  .values_list("student_count", flat=True) if c]
        return sum(counts) or None

    def clashing_section_ids(self):
        """Section ids that cannot meet at the same time as this one.

        A whole block clashes with itself and every one of its groups (the same
        students). A group clashes with itself and with the whole block, but not
        with sibling groups — splitting the block is the point.
        """
        family = self.family()
        if self.is_whole_block:
            ids = set(family.values_list("pk", flat=True))
        else:
            ids = set(family.filter(Q(group="") | Q(group=self.group))
                      .values_list("pk", flat=True))
        ids.add(self.pk)
        return {pk for pk in ids if pk}

    def __str__(self):
        return self.code


YEAR_LEVEL_LABELS = {1: "1st year", 2: "2nd year", 3: "3rd year", 4: "4th year",
                     5: "5th year"}


def year_level_label(year):
    return YEAR_LEVEL_LABELS.get(year, f"Year {year}")


class CurriculumSubject(ArchiveClockMixin):
    """One subject in a program's curriculum: BSIT, 4th year, 1st Semester, IT401.

    The curriculum is what a section is *supposed* to take. A section carries
    its program and year level, and a term carries its semester, so the
    offering form can narrow the subject picker to exactly this program/year/
    semester slot (``scheduling.curriculum.offerable_subjects``).

    ``program`` is the same free-text code a Section uses (``BSIT``), not a
    foreign key: there is no Program table, and matching on the code keeps a
    curriculum valid for every block of the program. ``semester`` reuses the
    Term choices, which is how a term "knows" its curriculum column.

    Removing a subject from the curriculum archives the row (views never
    hard-delete); adding it back restores the same row.
    """

    program = models.CharField(max_length=20, help_text="Program code, e.g. BSIT.")
    year_level = models.PositiveSmallIntegerField(help_text="Year level, e.g. 4.")
    semester = models.CharField(max_length=6, choices=Term.Semester.choices)
    subject = models.ForeignKey("Subject", on_delete=models.PROTECT,
                                related_name="curriculum_entries")
    is_active = models.BooleanField(default=True)
    # See facilities.Building.archived_at.
    archived_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["program", "year_level", "semester", "subject__code"]
        constraints = [
            models.UniqueConstraint(fields=["program", "year_level", "semester", "subject"],
                                    name="unique_curriculum_subject"),
        ]
        indexes = [
            models.Index(fields=["program", "year_level", "semester"],
                         name="curriculum_slot_idx"),
        ]

    def save(self, *args, **kwargs):
        self.program = (self.program or "").strip().upper()
        return super().save(*args, **kwargs)

    @property
    def slot_label(self):
        return (f"{self.program} · {year_level_label(self.year_level)} · "
                f"{self.get_semester_display()}")

    def __str__(self):
        return f"{self.subject.code} in {self.slot_label}"


# The first-come-first-served queue order. ``planner.auto_schedule`` walks the
# offerings of a term in exactly this order, so changing it changes who wins a
# contested slot: an explicit priority first, then the request stamp, then the
# row's own age as a last tie-break.
OFFERING_QUEUE_ORDER = ("-priority", "requested_at", "pk")

# Priority is a 1-5 scale where 5 is the highest, so the queue sorts it
# descending. 3 is the ordinary request.
PRIORITY_CHOICES = [(5, "5 - Highest"), (4, "4 - High"), (3, "3 - Normal"),
                    (2, "2 - Low"), (1, "1 - Lowest")]
DEFAULT_PRIORITY = 3


class SubjectOffering(ArchiveClockMixin):
    """One subject taught to one section by one professor in one term.

    This is the scheduler's input queue. An offering says *what* has to be
    scheduled (subject + section + professor, so many meetings of so many
    minutes); ``planner.auto_schedule`` turns it into ScheduleBlocks. The queue
    is first come, first served: ``priority`` then ``requested_at`` then ``pk``
    (see ``QUEUE_ORDER``), so an offering filed earlier gets its pick of the
    week before a later one.
    """

    class MeetingFormat(models.TextChoices):
        STANDARD = "STANDARD", "Standard (same kind of meeting)"
        LECTURE_LAB = "LECTURE_LAB", "Lecture + lab (1 + 1 a week)"

    term = models.ForeignKey("Term", on_delete=models.PROTECT, related_name="offerings")
    subject = models.ForeignKey("Subject", on_delete=models.PROTECT, related_name="offerings")
    section = models.ForeignKey("Section", on_delete=models.PROTECT, related_name="offerings")
    instructor = models.ForeignKey("accounts.Borrower", null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="offerings")
    meetings_per_week = models.PositiveSmallIntegerField(
        default=2, help_text="How many separate meetings this subject needs each week.")
    minutes_per_meeting = models.PositiveSmallIntegerField(
        default=90, help_text="Length of one meeting, in minutes (the lecture, for a lecture + lab subject).")
    meeting_format = models.CharField(
        max_length=11, choices=MeetingFormat.choices, default=MeetingFormat.STANDARD,
        help_text="Lecture + lab = exactly 2 meetings a week: one lecture in a classroom, one lab in a laboratory.")
    lab_minutes_per_meeting = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Length of the lab meeting. Leave blank to use the same length as the lecture.")
    preferred_room = models.ForeignKey("facilities.Room", null=True, blank=True,
                                       on_delete=models.SET_NULL,
                                       related_name="preferred_offerings",
                                       help_text="Tried first; the scheduler falls back to any free room.")
    preferred_lab_room = models.ForeignKey(
        "facilities.Room", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="preferred_lab_offerings",
        help_text="Lecture + lab only: tried first for the lab meeting (a laboratory). "
                  "The preferred room above is then the lecture room.")
    room_type = models.CharField(max_length=15, blank=True,
                                 help_text="Restrict the fallback to rooms of this type.")
    priority = models.PositiveSmallIntegerField(
        choices=PRIORITY_CHOICES, default=DEFAULT_PRIORITY,
        help_text="1 to 5, where 5 is the highest and goes first. Ties fall back to the request time.")
    requested_at = models.DateTimeField(
        default=timezone.now,
        help_text="The first-come-first-served stamp. Defaults to when the offering was filed.")
    notes = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    # See facilities.Building.archived_at.
    archived_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="offerings_created")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Exposed on the model so callers do not have to import the constant.
    QUEUE_ORDER = OFFERING_QUEUE_ORDER

    class Meta:
        ordering = list(OFFERING_QUEUE_ORDER)
        constraints = [
            models.UniqueConstraint(fields=["term", "subject", "section"],
                                    name="unique_offering_per_term_section"),
        ]
        indexes = [
            models.Index(fields=["term", "priority", "requested_at"],
                         name="offering_queue_idx"),
        ]

    def clean(self):
        super().clean()
        if self.lab_minutes_per_meeting and self.lab_minutes_per_meeting % 30:
            raise ValidationError({
                "lab_minutes_per_meeting": "Use a multiple of 30 minutes — the timetable grid is half-hourly.",
            })
        if self.minutes_per_meeting and self.minutes_per_meeting % 30:
            raise ValidationError({
                "minutes_per_meeting": "Use a multiple of 30 minutes — the timetable grid is half-hourly.",
            })

    @property
    def is_lecture_lab(self):
        return self.meeting_format == self.MeetingFormat.LECTURE_LAB

    @property
    def lab_minutes(self):
        return self.lab_minutes_per_meeting or self.minutes_per_meeting

    def meeting_plan(self):
        """The meetings one week needs, as ``(kind, minutes, room_type)`` tuples.

        A standard offering is ``meetings_per_week`` identical meetings (kind
        ``""``, room type from ``room_type``). A lecture + lab offering is
        always one lecture in a classroom and one lab in a laboratory.
        """
        if self.is_lecture_lab:
            return [(ScheduleBlock.MeetingKind.LECTURE, self.minutes_per_meeting,
                     "CLASSROOM"),
                    (ScheduleBlock.MeetingKind.LAB, self.lab_minutes, "LABORATORY")]
        return [("", self.minutes_per_meeting, self.room_type)] * (self.meetings_per_week or 0)

    def save(self, *args, **kwargs):
        if self.is_lecture_lab:
            self.meetings_per_week = 2
        return super().save(*args, **kwargs)

    @property
    def weekly_minutes(self):
        if self.is_lecture_lab:
            return (self.minutes_per_meeting or 0) + (self.lab_minutes or 0)
        return (self.meetings_per_week or 0) * (self.minutes_per_meeting or 0)

    def scheduled_blocks(self):
        return self.blocks.filter(is_active=True)

    @property
    def scheduled_count(self):
        # Set by the list view's annotation when there is one, so the page does
        # not fire a query per row.
        if hasattr(self, "_scheduled_count"):
            return self._scheduled_count
        return self.scheduled_blocks().count()

    @property
    def is_fully_scheduled(self):
        return self.scheduled_count >= (self.meetings_per_week or 0)

    @property
    def remaining_meetings(self):
        return max(0, (self.meetings_per_week or 0) - self.scheduled_count)

    def __str__(self):
        return f"{self.subject.code} · {self.section.code}"


class ScheduleBlock(models.Model):
    class DayOfWeek(models.IntegerChoices):
        MONDAY = 0, "Monday"
        TUESDAY = 1, "Tuesday"
        WEDNESDAY = 2, "Wednesday"
        THURSDAY = 3, "Thursday"
        FRIDAY = 4, "Friday"
        SATURDAY = 5, "Saturday"
        SUNDAY = 6, "Sunday"

    class MeetingKind(models.TextChoices):
        LECTURE = "LECTURE", "Lecture"
        LAB = "LAB", "Laboratory"

    term = models.ForeignKey("Term", on_delete=models.PROTECT, related_name="schedule_blocks")
    room = models.ForeignKey("facilities.Room", on_delete=models.PROTECT, related_name="schedule_blocks")
    subject = models.ForeignKey("Subject", null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="schedule_blocks")
    title = models.CharField(max_length=200)
    instructor = models.ForeignKey("accounts.Borrower", null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="schedule_blocks")
    # Who sits in the class. Nullable so every block that predates sections
    # (and every non-class booking) stays valid; the section timetable and its
    # export only show blocks that have one.
    section = models.ForeignKey("Section", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="schedule_blocks")
    # The offering this block was placed for. Set by planner.auto_schedule and
    # by the manual form when an offering is picked; SET_NULL so deleting the
    # offering leaves the booking standing rather than wiping the timetable.
    offering = models.ForeignKey("SubjectOffering", null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="blocks")
    # Which of a lecture + lab offering's two meetings this block is. Blank for
    # every other block.
    meeting_kind = models.CharField(max_length=10, choices=MeetingKind.choices, blank=True)
    day_of_week = models.PositiveSmallIntegerField(choices=DayOfWeek.choices, default=DayOfWeek.MONDAY)
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="schedule_blocks_created")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["day_of_week", "start_time", "end_time"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_time__gt=F("start_time")),
                name="scheduleblock_end_after_start",
            ),
        ]
        indexes = [
            models.Index(fields=["room", "day_of_week", "start_time"],
                         name="schedule_room_day_start_idx"),
            # The section timetable and the section clash check both start here.
            models.Index(fields=["section", "term", "day_of_week", "start_time"],
                         name="schedule_section_day_idx"),
            models.Index(fields=["instructor", "term", "day_of_week", "start_time"],
                         name="schedule_instr_day_idx"),
        ]

    def __str__(self):
        return f"{self.title} ({self.term})"


class ScheduleException(models.Model):
    class ExceptionType(models.TextChoices):
        CANCELLED = "CANCELLED", "Cancelled"
        MOVED = "MOVED", "Moved"
        ROOM_CHANGE = "ROOM_CHANGE", "Room change"

    schedule_block = models.ForeignKey("ScheduleBlock", on_delete=models.PROTECT,
                                      related_name="exceptions")
    date = models.DateField()
    exception_type = models.CharField(max_length=20, choices=ExceptionType.choices)
    replacement_room = models.ForeignKey("facilities.Room", null=True, blank=True,
                                        on_delete=models.PROTECT, related_name="replacement_schedule_exceptions")
    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "schedule_block__start_time"]

    def __str__(self):
        return f"{self.schedule_block} {self.date} ({self.exception_type})"


class ScheduleSettings(models.Model):
    """Admin-controlled rules for how the POS treats the timetable (one row).

    No row exists until an admin saves the settings page; until then
    ``scheduling.services.get_schedule_settings`` answers from the
    ``SCHEDULE_ENFORCEMENT`` env default, so nothing changes on deploy.
    """

    class Enforcement(models.TextChoices):
        OFF = "off", "Ignore the timetable"
        WARN = "warn", "Allow, but ask the guard for a reason"
        BLOCK = "block", "Do not allow"

    class NoSchedule(models.TextChoices):
        AUTO = "auto", "Lend freely (nothing to check against)"
        REASON = "reason", "Ask the guard for a reason"

    enforcement = models.CharField(
        max_length=5, choices=Enforcement.choices, default=Enforcement.WARN,
        help_text="What happens when someone borrows a room that is scheduled to "
                  "another instructor at that time.")
    no_schedule_policy = models.CharField(
        max_length=6, choices=NoSchedule.choices, default=NoSchedule.AUTO,
        help_text="What happens when the semester has no timetable generated yet.")
    current_term = models.ForeignKey(
        Term, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        help_text="The semester the guards work from. Leave blank to follow the "
                  "term whose dates cover today.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "schedule settings"

    def __str__(self):
        return "Schedule settings"

    def save(self, *args, **kwargs):
        self.pk = 1  # singleton
        super().save(*args, **kwargs)
