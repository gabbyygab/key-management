from django import forms

from accounts.models import Borrower
from facilities.models import Building, Room
from .models import ScheduleBlock, Subject, Term
from .services import find_conflicts

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
        for name, f in self.fields.items():
            if isinstance(f.widget, forms.CheckboxInput):
                f.widget.attrs.setdefault("class", "h-4 w-4 accent-[#1E6B4A]")
            else:
                f.widget.attrs.setdefault("class", TAILWIND)
        if "room" in self.fields:
            self.fields["room"].queryset = Room.objects.filter(is_active=True).select_related("building")
        if "term" in self.fields:
            self.fields["term"].queryset = Term.objects.all().order_by("-is_active", "-start_date")
        if "subject" in self.fields:
            self.fields["subject"].required = False
            self.fields["subject"].empty_label = "No subject (Optional)"
        if "instructor" in self.fields:
            self.fields["instructor"].required = False
            self.fields["instructor"].empty_label = "Unassigned (Optional)"

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
            exclude_id = self.instance.pk if self.instance else None
            conflicts = find_conflicts(
                room=room,
                day_of_week=day,
                start_time=start,
                end_time=end,
                term=term,
                exclude_block_id=exclude_id,
            )
            if conflicts:
                conflict_names = ", ".join(
                    f"{b.title} ({b.start_time.strftime('%H:%M')}–{b.end_time.strftime('%H:%M')})"
                    for b in conflicts
                )
                raise forms.ValidationError(f"Conflicts with: {conflict_names}")

        return cleaned
