from django import forms

from accounts.forms import StyledFormMixin
from .models import BorrowTransaction


class TransactionCorrectionForm(StyledFormMixin, forms.ModelForm):
    """B4.6: the fields an admin may amend. Status, key and borrower are
    deliberately absent: changing them would desync the key's own status. A
    wrongly-issued key is fixed by receiving it, not by editing history."""
    reason = forms.CharField(
        max_length=255, label="Reason for correction",
        help_text="Required. Written to the audit log with the before/after values.")

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
