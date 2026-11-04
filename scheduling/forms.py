from django import forms
from django.db.models import Q
from django.core.exceptions import ValidationError

from accounts.models import Borrower, Department
from facilities.models import Building, Room
from .curriculum import MAX_YEAR_LEVEL, normalise_program, offerable_subjects, subject_allowed
from .models import (DEFAULT_PRIORITY, PRIORITY_CHOICES, ScheduleBlock, ScheduleException, ScheduleSettings, Section, Subject,
                     SubjectOffering, Term, year_level_label)
from .services import ScheduleConflictError, find_conflicts, validate_block

TAILWIND = "w-full rounded-lg border border-stone-300 bg-white px-3 py-2 text-[14px] focus:border-[#1E6B4A] focus:ring-[#1E6B4A]"

DAY_CHOICES = [(0, "Monday"), (1, "Tuesday"), (2, "Wednesday"), (3, "Thursday"),
               (4, "Friday"), (5, "Saturday"), (6, "Sunday")]


def _style(form):
    """Apply the shared control classes and error styling to every field.

    The same loop the other scheduling forms use, pulled out so the four new
    forms do not each repeat it.
    """
    for name, f in form.fields.items():
        if isinstance(f.widget, forms.CheckboxInput):
            f.widget.attrs.setdefault("class", "h-4 w-4 accent-[#1E6B4A]")
        else:
            f.widget.attrs.setdefault("class", TAILWIND)
        f.widget.attrs.setdefault("data-form-control", "")
        if form.is_bound and form.errors.get(name):
            f.widget.attrs["class"] += (
                " border-red-500 text-red-700 focus:border-red-500 focus:ring-red-500")
            f.widget.attrs["aria-invalid"] = "true"


def borrower_label(borrower):
    return f"{borrower.last_name}, {borrower.first_name}"


def room_label(room):
    return f"{room.building.code}-{room.room_code} ({room.get_room_type_display()})"


class DepartmentForm(forms.ModelForm):
    """Master data: the departments that own subjects, sections and rooms."""

    class Meta:
        model = Department
        fields = ["code", "name", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["code"].widget.attrs["placeholder"] = "CCS"
        self.fields["name"].widget.attrs["placeholder"] = "College of Computer Studies"
        _style(self)

    def clean_code(self):
        code = (self.cleaned_data.get("code") or "").strip().upper()
        clash = Department.objects.filter(code__iexact=code)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise ValidationError("A department with this code already exists.")
        return code


class SubjectForm(forms.ModelForm):
    """Master data: the catalogue of subjects a professor can be given."""

    class Meta:
        model = Subject
        fields = ["code", "title", "department", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["department"].queryset = Department.objects.filter(is_active=True)
        self.fields["department"].empty_label = "No department"
        self.fields["code"].widget.attrs["placeholder"] = "IT401"
        self.fields["title"].widget.attrs["placeholder"] = "Capstone Project 1"
        _style(self)

    def clean_code(self):
        code = (self.cleaned_data.get("code") or "").strip().upper()
        clash = Subject.objects.filter(code__iexact=code)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise ValidationError("A subject with this code already exists.")
        return code


class TermForm(forms.ModelForm):
    """An academic term filed as an academic year plus a semester.

    The display name (``2025-2026 1st Semester``) is built from the two, never
    typed, so every term reads the same everywhere it is picked.
    """

    class Meta:
        model = Term
        fields = ["academic_year", "semester", "start_date", "end_date", "is_active"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "end_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["academic_year"].required = True
        self.fields["semester"].required = True
        self.fields["semester"].choices = [("", "Choose…")] + list(Term.Semester.choices)
        self.fields["academic_year"].widget.attrs["placeholder"] = "2025-2026"
        _style(self)

    def clean(self):
        cleaned = super().clean()
        year, sem = cleaned.get("academic_year"), cleaned.get("semester")
        if year and sem:
            clash = Term.objects.filter(academic_year=year, semester=sem)
            if self.instance.pk:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise ValidationError(f"{Term.build_name(year, sem)} already exists.")
        return cleaned


class SectionForm(forms.ModelForm):
    """A section, or one group of a section.

    Leaving **Group** blank files the whole block (``BSIT-4D``); filling it in
    with ``G1`` files a group of that block. A group does not need its parent
    row to exist first — the family is the program/year/block triple, so the
    rows find each other either way.
    """

    class Meta:
        model = Section
        fields = ["program", "year_level", "block", "group", "department",
                  "student_count", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["department"].queryset = Department.objects.filter(is_active=True)
        self.fields["department"].empty_label = "No department"
        self.fields["group"].required = False
        self.fields["program"].widget.attrs["placeholder"] = "BSIT"
        self.fields["block"].widget.attrs["placeholder"] = "D"
        self.fields["group"].widget.attrs["placeholder"] = "G1 (leave blank for the whole block)"
        self.fields["year_level"].widget.attrs["placeholder"] = "4"
        self.fields["student_count"].widget.attrs["placeholder"] = "Optional"
        _style(self)

    def clean(self):
        cleaned = super().clean()
        program = (cleaned.get("program") or "").strip().upper()
        block = (cleaned.get("block") or "").strip().upper()
        group = (cleaned.get("group") or "").strip().upper()
        cleaned["program"], cleaned["block"], cleaned["group"] = program, block, group
        if group and not group.startswith("G"):
            self.add_error("group", "A group label looks like G1 or G2.")
        year = cleaned.get("year_level")
        if program and block and year:
            clash = Section.objects.filter(program=program, year_level=year,
                                           block=block, group=group)
            if self.instance.pk:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise ValidationError(
                    f"{program}-{year}{block}{group} already exists.")
        return cleaned


class SubjectOfferingForm(forms.ModelForm):
    """One subject + section + professor queued for scheduling.

    ``requested_at`` is editable on purpose: it is the first-come-first-served
    stamp, so an admin entering a backlog can keep the real request order
    instead of the order they happened to type it in.
    """

    shared = forms.BooleanField(
        required=False, label="All groups attend together (one shared schedule)",
        help_text="Files the class against the whole block, so every group meets in the "
                  "same room at the same time. A group's own offering of this subject is "
                  "merged in.")

    class Meta:
        model = SubjectOffering
        fields = ["term", "section", "subject", "instructor", "meetings_per_week",
                  "minutes_per_meeting", "meeting_format", "lab_minutes_per_meeting",
                  "preferred_room", "preferred_lab_room", "room_type", "priority",
                  "requested_at", "notes", "is_active"]
        widgets = {
            "requested_at": forms.DateTimeInput(attrs={"type": "datetime-local"},
                                                format="%Y-%m-%dT%H:%M"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["term"].queryset = Term.objects.filter(is_active=True).order_by("-start_date")
        self.fields["subject"].queryset = Subject.objects.filter(is_active=True).order_by("code")
        self.fields["subject"].widget.attrs["data-searchable"] = "true"
        self.fields["subject"].widget.attrs["data-search-placeholder"] = "Search subject code or title…"
        for _name, _hint in (("section", "Search section…"),
                             ("instructor", "Search professor…"),
                             ("preferred_room", "Search room…"),
                             ("preferred_lab_room", "Search laboratory…")):
            self.fields[_name].widget.attrs["data-searchable"] = "true"
            self.fields[_name].widget.attrs["data-search-placeholder"] = _hint
        self.fields["section"].queryset = Section.objects.filter(is_active=True)
        self.fields["section"].label_from_instance = lambda s: s.code
        # Only faculty can teach an offering. Keep the currently assigned person
        # selectable so editing an older offering doesn't fail validation.
        faculty = Q(borrower_type=Borrower.BorrowerType.FACULTY)
        if self.instance and self.instance.instructor_id:
            faculty |= Q(pk=self.instance.instructor_id)
        self.fields["instructor"].queryset = (
            Borrower.objects.filter(faculty, is_active=True).order_by("last_name", "first_name"))
        self.fields["instructor"].label_from_instance = borrower_label
        self.fields["instructor"].required = False
        self.fields["instructor"].empty_label = "Unassigned"
        self.fields["preferred_room"].queryset = (
            Room.objects.filter(is_active=True).select_related("building")
            .order_by("building__code", "room_code"))
        self.fields["preferred_room"].label_from_instance = room_label
        self.fields["preferred_room"].required = False
        self.fields["preferred_room"].empty_label = "Any free room"
        self.fields["preferred_lab_room"].queryset = (
            Room.objects.filter(is_active=True, room_type=Room.RoomType.LABORATORY)
            .select_related("building").order_by("building__code", "room_code"))
        self.fields["preferred_lab_room"].label_from_instance = room_label
        self.fields["preferred_lab_room"].required = False
        self.fields["preferred_lab_room"].empty_label = "Any free laboratory"
        self.fields["room_type"] = forms.ChoiceField(
            choices=[("", "Any type")] + list(Room.RoomType.choices), required=False,
            label="Room type", help_text="Restricts the fallback when no preferred room is set.")
        self.fields["priority"] = forms.TypedChoiceField(
            choices=PRIORITY_CHOICES, coerce=int, initial=DEFAULT_PRIORITY,
            label="Priority",
            help_text="5 is the highest and is scheduled first; 1 is the lowest.")
        self.fields["meeting_format"].required = False
        self.fields["meeting_format"].initial = SubjectOffering.MeetingFormat.STANDARD
        self.fields["requested_at"].required = False
        self.fields["requested_at"].help_text = (
            "First-come-first-served stamp. Leave as-is unless you are entering a backlog.")
        self._narrow_subjects()
        from .planner import sharing_info
        self.sharing = sharing_info(self._picked("section", Section))
        self.fields["shared"].widget.attrs["data-shared-toggle"] = ""
        _style(self)

    def _picked(self, name, model):
        """The term/section this form is about: posted, else initial, else saved."""
        if self.is_bound:
            raw = self.data.get(self.add_prefix(name))
        else:
            raw = self.initial.get(name) or getattr(self.instance, f"{name}_id", None)
        if isinstance(raw, model):
            return raw
        if raw and str(raw).isdigit():
            return model.objects.filter(pk=int(raw)).first()
        return None

    def _narrow_subjects(self):
        """Offer only the section's curriculum subjects for the term's semester,
        minus those already offered to it (scheduling/curriculum.py).

        Only the rendered options are narrowed. The field's queryset stays every
        live subject, so a pick outside the list fails in clean() with a reason
        rather than Django's bare "Select a valid choice"."""
        section = self._picked("section", Section)
        term = self._picked("term", Term)
        keep_id = self.instance.subject_id
        if not keep_id and self.is_bound:
            picked_sub = self._picked("subject", Subject)
            if picked_sub:
                keep_id = picked_sub.pk
        self.curriculum = offerable_subjects(
            section=section, term=term, keep_subject_id=keep_id)
        field = self.fields["subject"]
        field.empty_label = "Pick a subject" if section else "Pick a section first"
        field.widget.choices = [("", field.empty_label)] + [
            (s.pk, field.label_from_instance(s)) for s in self.curriculum.subjects]

    def _check_sharing(self, term, subject, section, shared):
        """Keep one subject to one schedule per student.

        A group cannot take a subject on its own when its whole block already
        has it as a shared class. Sharing needs a sibling group to share with,
        and the whole block must not already hold the subject (an archived one
        is restored instead, the (term, subject, section) pair is unique).
        """
        from .planner import family_duplicates
        if section.is_whole_block:
            return
        block = section.whole_block()
        if not shared:
            if family_duplicates(term=term, subject=subject, section=section,
                                 exclude_pk=self.instance.pk).exists():
                self.add_error(
                    "subject",
                    f"{subject.code} is already a shared class for all of "
                    f"{section.block_code}'s groups this term, so {section.code} "
                    "takes it there.")
            return
        if not self.sharing["available"]:
            self.add_error("shared", f"{section.code} has no other group to share with.")
            return
        if block is not None:
            clash = SubjectOffering.objects.filter(term=term, subject=subject, section=block)
            if self.instance.pk:
                clash = clash.exclude(pk=self.instance.pk)
            existing = clash.first()
            if existing is not None:
                hint = (" It is archived: restore it instead." if not existing.is_active
                        else "")
                self.add_error("shared",
                               f"{subject.code} is already a shared class of "
                               f"{block.code} in {term}.{hint}")

    def clean_lab_minutes_per_meeting(self):
        value = self.cleaned_data.get("lab_minutes_per_meeting")
        if value and value % 30:
            raise ValidationError(
                "Use a multiple of 30 minutes — the timetable grid is half-hourly.")
        if value and value > 8 * 60:
            raise ValidationError("A single meeting cannot run longer than 8 hours.")
        return value

    def clean_minutes_per_meeting(self):
        value = self.cleaned_data.get("minutes_per_meeting")
        if value and value % 30:
            raise ValidationError(
                "Use a multiple of 30 minutes — the timetable grid is half-hourly.")
        if value and value > 8 * 60:
            raise ValidationError("A single meeting cannot run longer than 8 hours.")
        return value

    def clean_meeting_format(self):
        return (self.cleaned_data.get("meeting_format")
                or SubjectOffering.MeetingFormat.STANDARD)

    def clean(self):
        cleaned = super().clean()
        term, subject, section = (cleaned.get("term"), cleaned.get("subject"),
                                  cleaned.get("section"))
        if term and subject and section:
            clash = SubjectOffering.objects.filter(term=term, subject=subject, section=section)
            if self.instance.pk:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                existing = clash.first()
                hint = " (archived: restore it instead)" if not existing.is_active else ""
                err = f"{subject.code} is already offered to {section.code} in {term}.{hint}"
                self.add_error("subject", err)
                raise ValidationError(err)
            self._check_sharing(term, subject, section, cleaned.get("shared"))
            moved = {"term", "subject", "section"} & set(self.changed_data)
            if not self.instance.pk or moved:
                reason = subject_allowed(section=section, term=term, subject=subject)
                if reason:
                    self.add_error("subject", reason)
        preferred = cleaned.get("preferred_room")
        if preferred and section and subject:
            from .planner import room_allowed_for
            dept_id = section.department_id or subject.department_id
            if not room_allowed_for(preferred, dept_id):
                self.add_error(
                    "preferred_room",
                    f"{preferred} is designated to {preferred.department.code}, so a "
                    "class from another department cannot be placed there.")
        if cleaned.get("meeting_format") == SubjectOffering.MeetingFormat.LECTURE_LAB:
            # One lecture + one lab, always: the count is not the admin's to set.
            cleaned["meetings_per_week"] = 2
            self.instance.meetings_per_week = 2
            if preferred and preferred.room_type != Room.RoomType.CLASSROOM:
                self.add_error("preferred_room",
                               "The lecture room must be a classroom — the lab has its own laboratory field.")
            lab_room = cleaned.get("preferred_lab_room")
            if lab_room and section and subject:
                from .planner import room_allowed_for
                if not room_allowed_for(lab_room, section.department_id or subject.department_id):
                    self.add_error("preferred_lab_room",
                                   f"{lab_room} is designated to another department.")
            return cleaned
        cleaned["lab_minutes_per_meeting"] = None
        cleaned["preferred_lab_room"] = None
        self.instance.preferred_lab_room = None
        meetings = cleaned.get("meetings_per_week") or 0
        minutes = cleaned.get("minutes_per_meeting") or 0
        if meetings and minutes and meetings * minutes > 6 * 60 * 6:
            raise ValidationError(
                "That is more weekly hours than the Monday–Saturday window holds.")
        if meetings > 6:
            self.add_error("meetings_per_week",
                           "At most 6 meetings a week — one per schedulable day.")
        return cleaned


YEAR_LEVEL_CHOICES = [(y, year_level_label(y)) for y in range(1, MAX_YEAR_LEVEL + 1)]


class CurriculumAddForm(forms.Form):
    """Put one or more subjects into a curriculum slot (term/program/year).

    A term owns its curriculum, so the slot is picked per term.
    """

    program = forms.CharField(
        max_length=20, label="Program",
        help_text="The program code sections use, e.g. BSIT.")
    year_level = forms.TypedChoiceField(choices=YEAR_LEVEL_CHOICES, coerce=int,
                                        label="Year level")
    term = forms.ModelChoiceField(
        queryset=Term.objects.filter(is_active=True).order_by("-start_date"),
        empty_label=None, label="Term")
    subjects = forms.ModelMultipleChoiceField(
        queryset=Subject.objects.filter(is_active=True).select_related("department")
        .order_by("code"),
        widget=forms.CheckboxSelectMultiple, label="Subjects",
        error_messages={"required": "Pick at least one subject."})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["program"].widget.attrs.update({
            "placeholder": "BSIT", "list": "curriculum-programs",
            "autocomplete": "off", "data-curriculum-slot": "program",
            "class": TAILWIND + " uppercase"})
        self.fields["year_level"].widget.attrs["data-curriculum-slot"] = "year_level"
        self.fields["term"].widget.attrs["data-curriculum-slot"] = "term"
        _style(self)

    def clean_program(self):
        program = normalise_program(self.cleaned_data.get("program"))
        if not program:
            raise ValidationError("Enter the program code, e.g. BSIT.")
        if not all(c.isalnum() or c == "-" for c in program):
            raise ValidationError("Use letters and digits only, like BSIT.")
        return program

    @property
    def slot(self):
        """``(program, year, term pk)`` as posted or prefilled, for the template."""
        if self.is_bound:
            data = (self.data.get("program", ""), self.data.get("year_level", ""),
                    self.data.get("term", ""))
        else:
            data = (self.initial.get("program", ""), self.initial.get("year_level", ""),
                    self.initial.get("term", ""))
        return normalise_program(data[0]), str(data[1] or ""), str(data[2] or "")

    def selected_ids(self):
        if self.is_bound:
            return {int(v) for v in self.data.getlist("subjects") if str(v).isdigit()}
        return set()


class AutoScheduleForm(forms.Form):
    """Options for one first-come-first-served run."""

    term = forms.ModelChoiceField(
        queryset=Term.objects.filter(is_active=True).order_by("-start_date"),
        empty_label=None, label="Term")
    day_start = forms.TimeField(
        label="Earliest start", initial="07:00",
        widget=forms.TimeInput(attrs={"type": "time", "step": 1800}, format="%H:%M"))
    day_end = forms.TimeField(
        label="Latest end", initial="21:00",
        widget=forms.TimeInput(attrs={"type": "time", "step": 1800}, format="%H:%M"))
    include_saturday = forms.BooleanField(
        required=False, initial=True, label="Use Saturday too")
    one_meeting_per_day = forms.BooleanField(
        required=False, initial=True, label="Keep an offering's meetings on separate days")
    rebuild = forms.BooleanField(
        required=False, initial=False, label="Clear auto-placed blocks first and rebuild",
        help_text="Hand-made bookings (blocks with no offering) are left alone.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("day_start"), cleaned.get("day_end")
        if start and end and end <= start:
            self.add_error("day_end", "The latest end must be after the earliest start.")
        for name in ("day_start", "day_end"):
            value = cleaned.get(name)
            if value and (value.minute % 30 or value.second):
                self.add_error(name, "Use a half-hour boundary, e.g. 07:00 or 07:30.")
        return cleaned

    @property
    def days(self):
        return (0, 1, 2, 3, 4, 5) if self.cleaned_data.get("include_saturday") else (0, 1, 2, 3, 4)


class BlockSwapForm(forms.Form):
    """Swap the day/time of two placed blocks (the manual fix for FCFS order)."""

    block_a = forms.ModelChoiceField(queryset=ScheduleBlock.objects.none(),
                                     label="Move this class")
    block_b = forms.ModelChoiceField(queryset=ScheduleBlock.objects.none(),
                                     label="…into the slot of")
    include_rooms = forms.BooleanField(required=False, initial=False,
                                       label="Swap the rooms as well")

    def __init__(self, *args, term=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = (ScheduleBlock.objects.filter(is_active=True)
              .select_related("room__building", "section", "subject", "term")
              .order_by("day_of_week", "start_time"))
        if term is not None:
            qs = qs.filter(term=term)
        self.fields["block_a"].queryset = qs
        self.fields["block_b"].queryset = qs
        label = lambda b: (  # noqa: E731 - one-liner picker label
            f"{b.title} — {b.get_day_of_week_display()} "
            f"{b.start_time:%H:%M}–{b.end_time:%H:%M} @ {b.room}")
        self.fields["block_a"].label_from_instance = label
        self.fields["block_b"].label_from_instance = label
        _style(self)

    def clean(self):
        cleaned = super().clean()
        a, b = cleaned.get("block_a"), cleaned.get("block_b")
        if a and b and a.pk == b.pk:
            self.add_error("block_b", "Pick two different classes.")
        if a and b and a.term_id != b.term_id:
            self.add_error("block_b", "Both classes must be in the same term.")
        return cleaned


class FreeRoomQueryForm(forms.Form):
    """Room finder filters. Used by the page (GET) and the JSON endpoint."""
    day = forms.TypedChoiceField(choices=DAY_CHOICES, coerce=int)
    start = forms.TimeField(label="Start time",
                            widget=forms.TimeInput(attrs={"type": "time", "step": 300}, format="%H:%M"))
    end = forms.TimeField(label="End time",
                          widget=forms.TimeInput(attrs={"type": "time", "step": 300}, format="%H:%M"))
    building = forms.ModelChoiceField(queryset=Building.objects.filter(is_active=True),
                                      required=False, empty_label="Any building")
    room_type = forms.ChoiceField(choices=[("", "Any type")] + Room.RoomType.choices,
                                  required=False, label="Room type")
    min_capacity = forms.IntegerField(required=False, min_value=1, label="Min. capacity",
                                      widget=forms.NumberInput(attrs={"placeholder": "Any"}))
    term = forms.ModelChoiceField(queryset=Term.objects.filter(is_active=True).order_by("-start_date"),
                                  required=False, empty_label="Active term",
                                  help_text="Rooms already booked in this term drop out.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, f in self.fields.items():
            if isinstance(f.widget, forms.CheckboxInput):
                f.widget.attrs.setdefault("class", "h-4 w-4 accent-[#1E6B4A]")
            else:
                f.widget.attrs.setdefault("class", TAILWIND)

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end and end <= start:
            self.add_error("end", "End time must be after start time.")
        return cleaned


class ScheduleBlockForm(forms.ModelForm):
    day_of_week = forms.TypedChoiceField(
        choices=[
            (0, "Monday"),
            (1, "Tuesday"),
            (2, "Wednesday"),
            (3, "Thursday"),
            (4, "Friday"),
            (5, "Saturday"),
        ],
        coerce=int,
        label="Day of week",
    )
    start_time = forms.TimeField(
        label="Start time",
        widget=forms.TimeInput(attrs={"type": "time", "step": 300}, format="%H:%M"),
    )
    end_time = forms.TimeField(
        label="End time",
        widget=forms.TimeInput(attrs={"type": "time", "step": 300}, format="%H:%M"),
    )

    class Meta:
        model = ScheduleBlock
        fields = [
            "term",
            "room",
            "subject",
            "section",
            "title",
            "instructor",
            "day_of_week",
            "start_time",
            "end_time",
            "is_active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        modal_ids = {
            "room": "modal-room",
            "term": "modal-term",
            "title": "modal-title",
            "subject": "modal-subject",
            "section": "modal-section",
            "instructor": "modal-instructor",
            "day_of_week": "modal-day",
            "start_time": "modal-start-time",
            "end_time": "modal-end-time",
            "is_active": "modal-is-active",
        }
        if "room" in self.fields:
            self.fields["room"].queryset = Room.objects.filter(is_active=True).select_related("building")
            self.fields["room"].label_from_instance = lambda room: (
                f"{room.building.code} - {room.room_code} ({room.get_room_type_display()})"
            )
        if "term" in self.fields:
            self.fields["term"].queryset = Term.objects.filter(is_active=True).order_by("-start_date")
        if "subject" in self.fields:
            self.fields["subject"].required = False
            self.fields["subject"].empty_label = "No subject (Optional)"
        if "section" in self.fields:
            self.fields["section"].required = False
            self.fields["section"].empty_label = "No section (Optional)"
            self.fields["section"].queryset = Section.objects.filter(is_active=True)
            self.fields["section"].label_from_instance = lambda s: s.code
        if "instructor" in self.fields:
            self.fields["instructor"].required = False
            self.fields["instructor"].empty_label = "Unassigned (Optional)"
            self.fields["instructor"].label_from_instance = lambda borrower: (
                f"{borrower.last_name}, {borrower.first_name} ({borrower.id_number}) - "
                f"{borrower.get_borrower_type_display()}"
            )
        for name, f in self.fields.items():
            f.widget.attrs["id"] = modal_ids.get(name, f.widget.attrs.get("id", name))
            f.widget.attrs["data-form-control"] = ""
            if isinstance(f.widget, forms.CheckboxInput):
                f.widget.attrs.setdefault("class", "h-4 w-4 accent-[#1E6B4A]")
            else:
                f.widget.attrs.setdefault("class", TAILWIND)
            if self.is_bound and self.errors.get(name):
                f.widget.attrs["class"] += (
                    " border-red-500 text-red-700 focus:border-red-500 focus:ring-red-500"
                )
                f.widget.attrs["aria-invalid"] = "true"
                f.widget.attrs["aria-describedby"] = f"{modal_ids.get(name, name)}-error"

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_time")
        end = cleaned.get("end_time")
        room = cleaned.get("room")
        term = cleaned.get("term")
        day = cleaned.get("day_of_week")

        if start and end and end <= start:
            self.add_error("end_time", "End time must be after start time.")

        if room and term and day is not None and start and end and start < end:
            # B5.3 validate_block: term range, room usable, and the overlap
            # check (an inactive block never conflicts, so skip it there).
            probe = ScheduleBlock(pk=self.instance.pk if self.instance else None,
                                  room=room, term=term, day_of_week=day,
                                  start_time=start, end_time=end)
            try:
                if cleaned.get("is_active", True):
                    validate_block(probe)
                elif not room.is_active or room.status in {Room.Status.MAINTENANCE, Room.Status.INACTIVE}:
                    raise ValueError("Selected room is not usable for scheduling.")
            except ScheduleConflictError as e:
                conflict_names = ", ".join(
                    f"{b.title} ({b.start_time.strftime('%H:%M')}–{b.end_time.strftime('%H:%M')})"
                    for b in e.conflicts
                )
                raise forms.ValidationError(f"Conflicts with: {conflict_names}")
            except ValueError as e:
                raise forms.ValidationError(str(e))

            # The room rule above is only one of three. A manual edit must also
            # not double-book the professor or the section, or the auto-run and
            # the hand edits would disagree about what is possible.
            if cleaned.get("is_active", True):
                from .planner import blocker_summary, slot_blockers
                blockers = slot_blockers(
                    term=term, section=cleaned.get("section"),
                    instructor=cleaned.get("instructor"), room=None,
                    day_of_week=day, start_time=start, end_time=end,
                    exclude_block_id=self.instance.pk if self.instance else None,
                )
                if blockers:
                    raise forms.ValidationError(blocker_summary(blockers))

        return cleaned


class ScheduleExceptionForm(forms.ModelForm):
    """F5.5 — one-off change to a single date's meeting of a block."""

    class Meta:
        model = ScheduleException
        fields = ["date", "exception_type", "replacement_room", "reason"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, block=None, **kwargs):
        self.block = block
        super().__init__(*args, **kwargs)
        self.fields["replacement_room"].required = False
        self.fields["replacement_room"].queryset = (
            Room.objects.filter(is_active=True).select_related("building")
            .order_by("building__code", "room_code"))
        self.fields["replacement_room"].empty_label = "— Same room —"
        self.fields["reason"].required = True
        for f in self.fields.values():
            f.widget.attrs.setdefault("class", TAILWIND)

    def clean(self):
        cleaned = super().clean()
        date = cleaned.get("date")
        kind = cleaned.get("exception_type")
        replacement = cleaned.get("replacement_room")
        block = self.block
        if block and date:
            if date.weekday() != block.day_of_week:
                self.add_error("date", f"'{block.title}' meets on "
                                       f"{block.get_day_of_week_display()}s; pick one of those dates.")
            elif not (block.term.start_date <= date <= block.term.end_date):
                self.add_error("date", "That date is outside the block's term.")
            elif ScheduleException.objects.filter(schedule_block=block, date=date).exists():
                self.add_error("date", "An exception already exists for this date.")
        if kind == ScheduleException.ExceptionType.ROOM_CHANGE and not replacement:
            self.add_error("replacement_room", "Choose the room the class moves to.")
        if replacement and block and replacement.pk == block.room_id:
            self.add_error("replacement_room", "That is the block's own room.")
        if replacement and block and date and kind == ScheduleException.ExceptionType.ROOM_CHANGE:
            clash = find_conflicts(replacement, block.day_of_week, block.start_time,
                                   block.end_time, block.term)
            if clash:
                self.add_error("replacement_room",
                               "That room is booked then: " + ", ".join(c.title for c in clash))
        return cleaned


class ScheduleSettingsForm(forms.ModelForm):
    """How the POS treats the timetable, and which semester the guards see."""

    class Meta:
        model = ScheduleSettings
        fields = ["current_term", "enforcement", "no_schedule_policy"]
        widgets = {
            "enforcement": forms.RadioSelect,
            "no_schedule_policy": forms.RadioSelect,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["current_term"].queryset = Term.objects.filter(is_active=True).order_by(
            "-start_date")
        self.fields["current_term"].empty_label = "Follow today's date automatically"
        self.fields["current_term"].required = False
        for name in ("enforcement", "no_schedule_policy"):
            self.fields[name].widget.attrs["class"] = "h-4 w-4 accent-[#1E6B4A]"
        self.fields["current_term"].widget.attrs.update(
            {"class": TAILWIND, "data-form-control": ""})
