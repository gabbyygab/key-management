from django import forms
from .models import Borrower
from facilities.models import Building, Room
from keys.models import Key, KeyCategory

TAILWIND = "w-full rounded-lg border border-stone-300 bg-white px-3 py-2 text-[14px] focus:border-[#1E6B4A] focus:ring-[#1E6B4A]"


class BorrowerForm(forms.ModelForm):
    class Meta:
        model = Borrower
        fields = ("id_number", "first_name", "last_name", "borrower_type",
                  "department", "contact_number", "email",
                  "is_authorized", "authorized_until", "is_active")
        widgets = {
            "authorized_until": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, f in self.fields.items():
            if isinstance(f.widget, forms.CheckboxInput):
                f.widget.attrs.setdefault("class", "h-4 w-4 accent-[#1E6B4A]")
            else:
                f.widget.attrs.setdefault("class", TAILWIND)


class KeyForm(forms.ModelForm):
    class Meta:
        model = Key
        fields = ("key_code", "room", "category", "description",
                  "copy_number", "status", "remarks")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, f in self.fields.items():
            if isinstance(f.widget, forms.CheckboxInput):
                f.widget.attrs.setdefault("class", "h-4 w-4 accent-[#1E6B4A]")
            else:
                f.widget.attrs.setdefault("class", TAILWIND)


class CategoryForm(forms.ModelForm):
    class Meta:
        model = KeyCategory
        fields = ("name", "description")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, f in self.fields.items():
            if isinstance(f.widget, forms.CheckboxInput):
                f.widget.attrs.setdefault("class", "h-4 w-4 accent-[#1E6B4A]")
            else:
                f.widget.attrs.setdefault("class", TAILWIND)


class BuildingForm(forms.ModelForm):
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
