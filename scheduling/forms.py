from django import forms

from accounts.models import Borrower
from facilities.models import Building, Room
from .models import ScheduleBlock, ScheduleException, Subject, Term
from .services import ScheduleConflictError, find_conflicts, validate_block

TAILWIND = "w-full rounded-lg border border-stone-300 bg-white px-3 py-2 text-[14px] focus:border-[#1E6B4A] focus:ring-[#1E6B4A]"

DAY_CHOICES = [(0, "Monday"), (1, "Tuesday"), (2, "Wednesday"), (3, "Thursday"),
               (4, "Friday"), (5, "Saturday"), (6, "Sunday")]


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
    term = forms.ModelChoiceField(queryset=Term.objects.all().order_by("-is_active", "-start_date"),
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
            self.fields["term"].queryset = Term.objects.all().order_by("-is_active", "-start_date")
        if "subject" in self.fields:
            self.fields["subject"].required = False
            self.fields["subject"].empty_label = "No subject (Optional)"
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
