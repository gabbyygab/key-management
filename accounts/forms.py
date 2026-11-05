from django import forms
from django.conf import settings
from django.contrib.auth import authenticate, password_validation
from django.db.models import Max, Q

from facilities.models import Building, GuardPost, Room
from keys.models import Key, KeyCategory
from .models import Borrower, Department, User
from .services import next_borrower_id, next_employee_id

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

    #: {field name: example value}. Rendered as the input's placeholder so the
    #: expected shape ("09XX-XXX-XXXX") is visible before anything is typed.
    #: partials/form_field.html marks every non-required field "(optional)", so
    #: a placeholder is a sample, never an instruction.
    placeholders = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, f in self.fields.items():
            if isinstance(f.widget, forms.CheckboxInput):
                f.widget.attrs.setdefault("class", "h-4 w-4 accent-[#1E6B4A]")
            else:
                f.widget.attrs.setdefault("class", TAILWIND)

            sample = self.placeholders.get(name)
            if sample and not isinstance(f.widget, (forms.CheckboxInput, forms.Select)):
                f.widget.attrs.setdefault("placeholder", sample)

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
    placeholders = {
        "first_name": "e.g. Juan",
        "last_name": "e.g. Dela Cruz",
        "contact_number": "09XX-XXX-XXXX",
        "email": "e.g. juan.delacruz@example.edu.ph",
    }

    class Meta:
        model = Borrower
        # id_number is absent: next_borrower_id() assigns it on create.
        fields = ("first_name", "last_name", "borrower_type",
                  "department", "contact_number", "email",
                  "is_authorized", "authorized_until", "is_active")
        widgets = {
            "authorized_until": forms.DateInput(attrs={"type": "date"}),
        }

    #: Only these people belong to a department. Students and others never do.
    DEPARTMENT_TYPES = (Borrower.BorrowerType.FACULTY, Borrower.BorrowerType.STAFF)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Archived departments cannot be picked, but an existing borrower
        # keeps the one they already have.
        keep = Q(pk=self.instance.department_id) if self.instance.pk and self.instance.department_id else Q()
        self.fields["department"].queryset = Department.objects.filter(Q(is_active=True) | keep)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("borrower_type") not in self.DEPARTMENT_TYPES:
            cleaned["department"] = None
        elif (cleaned.get("borrower_type") == Borrower.BorrowerType.FACULTY
              and not cleaned.get("department")):
            self.add_error("department", "Department is required for faculty.")
        return cleaned

    def save(self, commit=True):
        borrower = super().save(commit=False)
        if not borrower.id_number:
            borrower.id_number = next_borrower_id()
        if commit:
            borrower.save()
        return borrower


class GroupedRoomSelect(forms.Select):
    def __init__(self, attrs=None, room_map=None):
        super().__init__(attrs)
        self.room_map = room_map or {}

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        opt = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        if value:
            info = self.room_map.get(str(value)) or self.room_map.get(value)
            if info:
                opt["attrs"]["data-building"] = info.get("building", "")
                opt["attrs"]["data-room"] = info.get("room", "")
                opt["attrs"]["data-prefix"] = info.get("prefix", "")
                opt["attrs"]["data-category"] = str(info.get("category_id", ""))
                opt["attrs"]["data-next-copy"] = str(info.get("next_copy", "1"))
        return opt


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
    widget = GroupedRoomSelect

    def label_from_instance(self, obj):
        name_part = f" · {obj.name}" if obj.name else ""
        return f"{obj.room_code}{name_part} ({obj.get_room_type_display()})"


class KeyForm(StyledFormMixin, forms.ModelForm):
    key_code = forms.CharField(
        max_length=50,
        required=False,
        label="Key Code",
        help_text="Leave blank to automatically generate from room and copy number.",
        widget=forms.TextInput(attrs={
            "placeholder": "Auto-generated if left blank (e.g. ENG-101-1)",
            "autocomplete": "off",
        }),
    )
    room = GroupedRoomChoiceField(
        queryset=Room.objects.select_related("building").all(),
        empty_label="— Select a room —",
        help_text="Rooms are grouped by building. Use search to filter.",
    )
    category = forms.ModelChoiceField(
        queryset=KeyCategory.objects.filter(is_active=True),
        required=False,
        empty_label="— Select a category —",
        help_text="Category (defaults to Office/Room based on room type).",
    )
    copy_number = forms.IntegerField(
        min_value=1,
        initial=1,
        required=False,
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
        keep = Q(pk=self.instance.room_id) if self.instance and self.instance.pk and self.instance.room_id else Q()
        rooms_qs = Room.objects.select_related("building").filter(Q(is_active=True) | keep)
        self.fields["room"].queryset = rooms_qs

        # Build metadata room_map for widget
        from keys.services import default_key_category
        max_copies = dict(
            Key.objects.values("room_id").annotate(max_copy=Max("copy_number")).values_list("room_id", "max_copy")
        )
        room_map = {}
        # default_key_category() only tells offices from everything else, so
        # look it up twice at most, not once per room in the list.
        cat_by_kind = {}
        for r in rooms_qs:
            b_code = r.building.code if r.building else ""
            kind = r.room_type == "OFFICE"
            if kind not in cat_by_kind:
                cat_by_kind[kind] = default_key_category(r)
            cat = cat_by_kind[kind]
            room_map[str(r.pk)] = {
                "building": b_code,
                "room": r.room_code,
                "prefix": f"{b_code}-{r.room_code}" if b_code else r.room_code,
                "category_id": cat.pk if cat else "",
                "next_copy": (max_copies.get(r.pk) or 0) + 1,
            }
        self.fields["room"].widget.room_map = room_map
        self.fields["room"].widget.choices = self.fields["room"].choices

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
        key_code = (cleaned_data.get("key_code") or "").strip()
        room = cleaned_data.get("room")
        category = cleaned_data.get("category")
        copy_number = cleaned_data.get("copy_number")

        # Default category if not chosen
        if room and not category:
            from keys.services import default_key_category
            category = default_key_category(room)
            cleaned_data["category"] = category

        # Default copy_number if not provided
        if room and copy_number is None:
            max_c = Key.objects.filter(room=room).aggregate(Max("copy_number"))["copy_number__max"] or 0
            copy_number = max_c + 1
            cleaned_data["copy_number"] = copy_number

        # Auto-generate key_code if blank
        if room and copy_number is not None:
            if not key_code:
                from keys.services import generate_key_code
                exclude_id = self.instance.pk if self.instance and self.instance.pk else None
                key_code = generate_key_code(
                    room=room,
                    copy_number=copy_number,
                    category=category,
                    exclude_key_id=exclude_id,
                )
                cleaned_data["key_code"] = key_code
            else:
                cleaned_data["key_code"] = key_code

        # Validate unique key_code across system
        if key_code:
            qs = Key.objects.filter(key_code=key_code)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                self.add_error("key_code", f"Key code '{key_code}' is already in use. Please enter a unique code.")

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
            "department",
            "status",
            "is_active",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["department"].queryset = Department.objects.filter(is_active=True)
        self.fields["department"].empty_label = "Shared (any department)"
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
    department = forms.ModelChoiceField(
        queryset=Department.objects.filter(is_active=True), required=False,
        empty_label="Shared (any department)",
        help_text="The department these rooms are designated to.")

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


# employee_id is deliberately absent: accounts.services.next_employee_id()
# assigns it on create, so nobody can type a duplicate or leave it blank.
USER_FIELDS = ("username", "first_name", "last_name", "email",
               "contact_number", "role", "department", "assigned_post")

USER_PLACEHOLDERS = {
    "username": "e.g. jdelacruz",
    "first_name": "e.g. Juan",
    "last_name": "e.g. Dela Cruz",
    "email": "e.g. juan.delacruz@example.edu.ph",
    "contact_number": "09XX-XXX-XXXX",
}


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
    """New accounts start with settings.DEFAULT_USER_PASSWORD; the user
    changes it themselves from the navbar (accounts:password_change)."""

    class Meta:
        model = User
        fields = USER_FIELDS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _tune_user_fields(self)

    placeholders = USER_PLACEHOLDERS

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(settings.DEFAULT_USER_PASSWORD)
        user.must_change_password = True
        if not user.employee_id:
            user.employee_id = next_employee_id()
        if commit:
            user.save()
        return user


class UserEditForm(_UserFieldsMixin, StyledFormMixin, forms.ModelForm):
    placeholders = USER_PLACEHOLDERS

    class Meta:
        model = User
        fields = USER_FIELDS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _tune_user_fields(self)


class AdminPasswordResetForm(StyledFormMixin, forms.Form):
    #: `data-password-strength` is what static/js/password-strength.js looks
    #: for: it draws the live Weak/Fair/Good/Strong meter and the rule
    #: checklist under the field. Guidance only — this form's clean() is still
    #: the only thing that decides what is accepted.
    new_password1 = forms.CharField(
        label="New password", strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password",
                                          "data-password-strength": "1"}))
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

    #: An admin chose this password, so the user must replace it on next sign-in.
    must_change = True

    def save(self):
        self.user.set_password(self.cleaned_data["new_password1"])
        self.user.must_change_password = self.must_change
        self.user.save(update_fields=["password", "must_change_password"])
        return self.user


class OwnPasswordChangeForm(AdminPasswordResetForm):
    """A signed-in admin or guard changing their own password.

    The current password goes through authenticate(), so with Supabase Auth
    on it is Supabase that confirms it, not just the local hash.
    """

    current_password = forms.CharField(label="Current password", strip=False,
                                       widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    field_order = ["current_password", "new_password1", "new_password2"]
    must_change = False  # the user chose it themselves

    def __init__(self, user, *args, request=None, **kwargs):
        self.request = request
        super().__init__(user, *args, **kwargs)

    def clean_current_password(self):
        pw = self.cleaned_data.get("current_password") or ""
        who = authenticate(self.request, username=self.user.username, password=pw)
        if who is None or who.pk != self.user.pk:
            if getattr(self.request, "supabase_auth_unavailable", False):
                raise forms.ValidationError(
                    "The sign-in service cannot be reached right now. Try again shortly.")
            raise forms.ValidationError("Your current password is incorrect.")
        return pw

    def clean(self):
        cleaned = super().clean()
        new = cleaned.get("new_password1")
        if new and new == cleaned.get("current_password"):
            self.add_error("new_password1", "Choose a password different from your current one.")
        elif new and new == settings.DEFAULT_USER_PASSWORD:
            self.add_error("new_password1", "Choose a password other than the default one.")
        return cleaned
