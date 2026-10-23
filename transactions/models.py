from django.conf import settings
from django.db import models
from django.utils import timezone


class GuardShift(models.Model):
    guard = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                              related_name="shifts")
    post = models.CharField(max_length=100, default="Main Desk")
    shift_start = models.DateTimeField(auto_now_add=True)
    shift_end = models.DateTimeField(null=True, blank=True)
    is_on_duty = models.BooleanField(default=True)
    remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["-shift_start"]

    def __str__(self):
        return f"{self.guard} @ {self.post} ({self.shift_start:%Y-%m-%d %H:%M})"


class BorrowTransaction(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        RETURNED = "RETURNED", "Returned"
        OVERDUE = "OVERDUE", "Overdue"
        LOST = "LOST", "Lost"
        DAMAGED = "DAMAGED", "Damaged"

    transaction_no = models.CharField(max_length=30, unique=True)
    key = models.ForeignKey("keys.Key", on_delete=models.PROTECT, related_name="transactions")
    borrower = models.ForeignKey("accounts.Borrower", on_delete=models.PROTECT,
                                 related_name="transactions")
    room = models.ForeignKey("facilities.Room", on_delete=models.PROTECT,
                             related_name="transactions")
    purpose = models.CharField(max_length=255, default="Room use", blank=True)
    borrowed_at = models.DateTimeField(auto_now_add=True)
    expected_return_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    related_name="keys_released")
    guard_shift = models.ForeignKey(GuardShift, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="transactions")
    returned_at = models.DateTimeField(null=True, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="keys_received")
    return_confirmed = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    remarks = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-borrowed_at"]

    def __str__(self):
        return self.transaction_no

    @property
    def is_overdue(self):
        if self.status == self.Status.OVERDUE:
            return True
        if self.status == self.Status.ACTIVE and self.expected_return_at:
            return timezone.now() >= self.expected_return_at
        return False

    @property
    def override_reason(self):
        if self.remarks:
            for line in self.remarks.splitlines():
                s = line.strip()
                if s.lower().startswith("override:"):
                    return s[len("override:"):].strip()
                if s.lower().startswith("[override]"):
                    return s[len("[override]"):].strip(" :")
        try:
            from audit.models import ActivityLog
            log = ActivityLog.objects.filter(
                action=ActivityLog.Action.OVERRIDE,
                model_name="BorrowTransaction",
                object_id=str(self.pk),
            ).first()
            if log:
                return log.description
        except Exception:
            pass
        return ""
