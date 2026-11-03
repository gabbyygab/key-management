from django import forms

from accounts.forms import StyledFormMixin
from .models import BorrowTransaction


class TransactionCorrectionForm(StyledFormMixin, forms.ModelForm):
    """B4.6: the fields an admin may amend.

    Key and borrower stay absent: a wrongly-issued key is fixed by receiving
    it, not by rewriting who held what. Status *is* editable, but never through
    `form.save()` — it is applied by `services.admin_set_status()`, which moves
    the key's own status with it and refuses a move that would leave two open
    rows on one key or one borrower. Leaving the field as "Leave unchanged"
    edits the other fields only.
    """
    # Declared with its choices, not patched in __init__: StyledFormMixin reads
    # self.errors there, so a bound form is already validated by then.
    status = forms.ChoiceField(
        required=False, label="Status",
        choices=[("", "Leave unchanged")] + list(BorrowTransaction.Status.choices),
        help_text="Changing the status also re-files the key (returned → available, "
                  "lost/damaged → written off, re-opened → back out).")
    reason = forms.CharField(
        max_length=255, label="Reason for correction",
        help_text="Required. Written to the audit log with the before/after values.")

    field_order = ("status", "purpose", "expected_return_at", "remarks", "reason")

    class Meta:
        model = BorrowTransaction
        fields = ("purpose", "expected_return_at", "remarks")
        widgets = {
            "expected_return_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "remarks": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["expected_return_at"].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"]
        if self.instance.pk:
            # Show where the record stands now without pre-selecting a move.
            self.fields["status"].help_text = (
                f"Currently {self.instance.get_status_display()}. "
                + self.fields["status"].help_text)
