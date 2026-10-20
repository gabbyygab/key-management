from django import forms
from .models import Borrower
from facilities.models import Building, Room
from keys.models import Key, KeyCategory

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


class KeyForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Key
        fields = ("key_code", "room", "category", "description",
                  "copy_number", "status", "remarks")


class CategoryForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = KeyCategory
        fields = ("name", "description")


class BuildingForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Building
        fields = ("code", "name", "description", "is_active")
