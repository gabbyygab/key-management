from django import forms
from django.contrib.auth import password_validation
from django.db.models import Q

from facilities.models import Building, GuardPost, Room
from keys.models import Key, KeyCategory
from .models import Borrower, Department, User

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
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}


class RoomForm(StyledFormMixin, forms.ModelForm):
    """B3.1 — the unique_room_per_building constraint surfaces as a field
    error instead of an IntegrityError 500."""

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
        qs = Building.objects.filter(is_active=True)
        if self.instance and self.instance.pk and self.instance.building_id:
            qs = Building.objects.filter(Q(is_active=True) | Q(pk=self.instance.building_id))
        self.fields["building"].queryset = qs

    def clean(self):
        cleaned = super().clean()
        building = cleaned.get("building")
        code = cleaned.get("room_code")
        if building and code:
            clash = Room.objects.filter(building=building, room_code__iexact=code)
            if self.instance and self.instance.pk:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                self.add_error(
                    "room_code",
                    f"Room {code} already exists in {building.code}. Room codes must be "
                    "unique within a building (the same code is fine in another building).",
                )
        capacity = cleaned.get("capacity")
        if capacity is not None and capacity < 0:
            self.add_error("capacity", "Capacity cannot be negative.")
        return cleaned


class RoomBulkForm(StyledFormMixin, forms.Form):
    """F4.2 bulk-add helper: generate e.g. rooms 101-110 in one go."""
    building = forms.ModelChoiceField(queryset=Building.objects.filter(is_active=True))
    prefix = forms.CharField(max_length=20, required=False,
                             help_text='Optional text before the number, e.g. "RM" gives RM101.')
    start_number = forms.IntegerField(min_value=0, initial=101)
    end_number = forms.IntegerField(min_value=0, initial=110)
    room_type = forms.ChoiceField(choices=Room.RoomType.choices, initial=Room.RoomType.CLASSROOM)
    floor = forms.IntegerField(required=False)
    capacity = forms.IntegerField(required=False, min_value=0)

    MAX_ROOMS = 100

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_number"), cleaned.get("end_number")
        if start is not None and end is not None:
            if end < start:
                self.add_error("end_number", "End number must be greater than or equal to the start.")
            elif end - start + 1 > self.MAX_ROOMS:
                self.add_error("end_number", f"At most {self.MAX_ROOMS} rooms can be generated at once.")
        return cleaned


class GuardPostForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = GuardPost
        fields = ("name", "building", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["building"].required = False
        self.fields["building"].empty_label = "— Not tied to a building —"


class _UserFieldsMixin:
    """Shared validation for the admin user forms (B2.1)."""

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip()
        if not email:
            raise forms.ValidationError("Email is required.")
        qs = User.objects.filter(email__iexact=email)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Another account already uses this email address.")
        return email

    def clean_employee_id(self):
        emp = (self.cleaned_data.get("employee_id") or "").strip()
        if not emp:
            return None  # unique + null: store NULL, never "", or two blanks collide
        qs = User.objects.filter(employee_id__iexact=emp)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Another account already uses this employee ID.")
        return emp


USER_FIELDS = ("username", "first_name", "last_name", "email", "employee_id",
               "contact_number", "role", "department", "assigned_post")


def _tune_user_fields(form):
    form.fields["email"].required = True
    form.fields["department"].queryset = Department.objects.filter(is_active=True)
    form.fields["department"].required = False
    form.fields["assigned_post"].queryset = GuardPost.objects.filter(is_active=True)
    form.fields["assigned_post"].required = False
    form.fields["assigned_post"].label = "Guard post"
    form.fields["assigned_post"].help_text = "Guards only: where their shift is recorded on login."
    form.fields["assigned_post"].empty_label = "— Main Desk (default) —"


class UserCreateForm(_UserFieldsMixin, StyledFormMixin, forms.ModelForm):
    password1 = forms.CharField(label="Password", strip=False,
                                widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(label="Confirm password", strip=False,
                                widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    class Meta:
        model = User
        fields = USER_FIELDS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _tune_user_fields(self)

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "The two passwords do not match.")
        elif p1:
            candidate = User(username=cleaned.get("username") or "",
                             email=cleaned.get("email") or "",
                             first_name=cleaned.get("first_name") or "",
                             last_name=cleaned.get("last_name") or "")
            try:
                password_validation.validate_password(p1, candidate)
            except forms.ValidationError as e:
                self.add_error("password1", e)
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class UserEditForm(_UserFieldsMixin, StyledFormMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = USER_FIELDS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _tune_user_fields(self)


class AdminPasswordResetForm(StyledFormMixin, forms.Form):
    new_password1 = forms.CharField(label="New password", strip=False,
                                    widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    new_password2 = forms.CharField(label="Confirm new password", strip=False,
                                    widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("new_password1"), cleaned.get("new_password2")
        if p1 and p2 and p1 != p2:
            self.add_error("new_password2", "The two passwords do not match.")
        elif p1:
            try:
                password_validation.validate_password(p1, self.user)
            except forms.ValidationError as e:
                self.add_error("new_password1", e)
        return cleaned

    def save(self):
        self.user.set_password(self.cleaned_data["new_password1"])
        self.user.save(update_fields=["password"])
        return self.user
