from django.db import models


class SyncCursor(models.Model):
    """Per-table bookkeeping for the Supabase mirror.

    `watermark` is the newest updated_at/created_at value already pushed; the
    next run re-sends rows from that instant onward (>=), so a row saved in
    the same instant is never skipped. Upserts make the overlap harmless.
    """

    class Status(models.TextChoices):
        OK = "OK", "OK"
        ERROR = "ERROR", "Error"

    table = models.CharField(max_length=100, unique=True)
    watermark = models.DateTimeField(null=True, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=10, choices=Status.choices, blank=True)
    last_error = models.TextField(blank=True)
    rows_last_run = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["table"]

    def __str__(self):
        return self.table
