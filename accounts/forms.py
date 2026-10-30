from django import forms
from django.db.models import Q

from facilities.models import Building, Room
from keys.models import Key, KeyCategory
from .models import Borrower

TAILWIND = "w-full rounded-lg border border-stone-300 bg-white px-3 py-2 text-[14px] focus:border-[#1E6B4A] focus:ring-[#1E6B4A]"


class StyledFormMixin:
    """Applies the shared Tailwind widget classes and the F6.7 a11y attributes.

    This __init__ was copy-pasted into all four ModelForms; the accessibility
    wiring below now has exactly one place to live.

    - required fields get `required` so the browser and AT both know, and an
      `aria-required` for AT that predates HTML5 constraint validation
    - a field that failed validation gets aria-invalid + aria-describedby
      pointing at the <p id="...-error"> the templates render under it, so a
      screen reader reads the reason instead of just "invalid entry"
    """

    #: Rendered by the templates as the id of each field's error paragraph.
    error_id_suffix = "-error"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, f in self.fields.items():
            if isinstance(f.widget, forms.CheckboxInput):
                f.widget.attrs.setdefault("class", "h-4 w-4 accent-[#1E6B4A]")
            else:
                f.widget.attrs.setdefault("class", TAILWIND)

            if f.required:
                f.widget.attrs.setdefault("required", "required")
                f.widget.attrs.setdefault("aria-required", "true")

            # self.errors is only populated on a bound form, so this is a no-op
            # on a fresh GET and correct on a failed POST re-render.
            if self.is_bound and self.errors.get(name):
                f.widget.attrs["aria-invalid"] = "true"
                described = f.widget.attrs.get("aria-describedby", "")
                error_id = "%s%s" % (
                    self.add_prefix(name).replace("-", "_"),
                    self.error_id_suffix,
                )
                f.widget.attrs["aria-describedby"] = (
                    (described + " " + error_id).strip() if described else error_id
                )


class BorrowerForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Borrower
        fields = ("id_number", "first_name", "last_name", "borrower_type",
                  "department", "contact_number", "email",
                  "is_authorized", "authorized_until", "is_active")
        widgets = {
            "authorized_until": forms.DateInput(attrs={"type": "date"}),
        }


class GroupedRoomModelChoiceIterator(forms.models.ModelChoiceIterator):
    def __iter__(self):
        if self.field.empty_label is not None:
            yield ("", self.field.empty_label)
        groups = {}
        for room in self.queryset.select_related("building").order_by("building__code", "room_code"):
            b = room.building
            b_label = f"{b.code} — {b.name}" if b else "Other"
            if b_label not in groups:
                groups[b_label] = []
            groups[b_label].append(self.choice(room))
        for group, choices in groups.items():
            yield (group, choices)


class GroupedRoomChoiceField(forms.ModelChoiceField):
    iterator = GroupedRoomModelChoiceIterator

    def label_from_instance(self, obj):
        name_part = f" · {obj.name}" if obj.name else ""
        return f"{obj.room_code}{name_part} ({obj.get_room_type_display()})"


class KeyForm(StyledFormMixin, forms.ModelForm):
    room = GroupedRoomChoiceField(
        queryset=Room.objects.select_related("building").all(),
        empty_label="— Select a room —",
        help_text="Rooms are grouped by building. Use search to filter.",
    )
    copy_number = forms.IntegerField(
        min_value=1,
        initial=1,
        help_text="Copy 1 is designated Original. Each copy number must be unique per room and category.",
        widget=forms.NumberInput(attrs={"min": 1}),
    )

    class Meta:
        model = Key
        fields = (
            "key_code",
            "room",
            "category",
            "description",
            "copy_number",
            "status",
            "remarks",
        )
        widgets = {
            "remarks": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not (self.instance and self.instance.pk):
            self.fields["room"].queryset = Room.objects.select_related("building").filter(is_active=True)
        # F4.4: inactive categories cannot be picked for new keys, but an
        # existing key keeps its current (possibly inactive) category.
        if "category" in self.fields:
            if self.instance and self.instance.pk and self.instance.category_id:
                self.fields["category"].queryset = KeyCategory.objects.filter(
                    Q(is_active=True) | Q(pk=self.instance.category_id)
                )
            else:
                self.fields["category"].queryset = KeyCategory.objects.filter(is_active=True)

    def clean(self):
        cleaned_data = super().clean()
        room = cleaned_data.get("room")
        category = cleaned_data.get("category")
        copy_number = cleaned_data.get("copy_number")

        if room and category and copy_number is not None:
            qs = Key.objects.filter(room=room, category=category, copy_number=copy_number)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                error_message = (
                    f"Copy #{copy_number} of a key for room {room} in category '{category.name}' already exists. "
                    "Constraint rule: Each physical key for a room and category must have a unique copy number "
                    "(e.g., Copy 1 is Original, Copy 2 is duplicate). Please use the next available copy number."
                )
                self.add_error("copy_number", error_message)
        return cleaned_data


class BulkKeyForm(StyledFormMixin, forms.Form):
    room = GroupedRoomChoiceField(
        queryset=Room.objects.select_related("building").filter(is_active=True),
        empty_label="— Select a room —",
        help_text="Room for which duplicate keys will be generated.",
    )
    category = forms.ModelChoiceField(
        queryset=KeyCategory.objects.filter(is_active=True),
        empty_label="— Select a category —",
        help_text="Key category (e.g. Master, Office, Classroom).",
    )
    count = forms.IntegerField(
        min_value=1,
        max_value=50,
        initial=3,
        label="Number of copies (N)",
        help_text="Number of copies to generate. Automatically starts from next available copy number.",
        widget=forms.NumberInput(attrs={"min": 1, "max": 50}),
    )
    key_code_prefix = forms.CharField(
        max_length=40,
        required=False,
        label="Key code prefix",
        help_text="Optional prefix for key codes (defaults to [Building]-[Room]).",
    )
    status = forms.ChoiceField(
        choices=Key.Status.choices,
        initial=Key.Status.AVAILABLE,
        label="Initial status",
    )
    description = forms.CharField(
        max_length=255,
        required=False,
        help_text="Optional description applied to all copies.",
    )
    remarks = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}),
        required=False,
        help_text="Optional remarks/notes.",
    )


class CategoryForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = KeyCategory
        fields = ("name", "description")
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Master, Office, Laboratory"}),
            "description": forms.Textarea(attrs={"rows": 3, "placeholder": "Brief description of this category's purpose..."}),
        }


class BuildingForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Building
        fields = ("code", "name", "description", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, f in self.fields.items():
            if isinstance(f.widget, forms.CheckboxInput):
                f.widget.attrs.setdefault("class", "h-4 w-4 accent-[#1E6B4A]")
            else:
                f.widget.attrs.setdefault("class", TAILWIND)


class RoomForm(forms.ModelForm):
    class Meta:
        model = Room
        fields = (
            "building",
            "room_code",
            "name",
            "room_type",
            "floor",
            "capacity",
            "status",
            "is_active",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, f in self.fields.items():
            if isinstance(f.widget, forms.CheckboxInput):
                f.widget.attrs.setdefault("class", "h-4 w-4 accent-[#1E6B4A]")
            else:
                f.widget.attrs.setdefault("class", TAILWIND)
