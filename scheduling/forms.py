from django import forms

from facilities.models import Building, Room

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
