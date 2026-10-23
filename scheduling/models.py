from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q


class Term(models.Model):
    name = models.CharField(max_length=100, unique=True)
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_date", "name"]

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date must be after the start date."})

    def __str__(self):
        return self.name


class Subject(models.Model):
    code = models.CharField(max_length=50, unique=True)
    title = models.CharField(max_length=200)
    department = models.ForeignKey("accounts.Department", null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="subjects")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.title}"


class ScheduleBlock(models.Model):
    class DayOfWeek(models.IntegerChoices):
        MONDAY = 0, "Monday"
        TUESDAY = 1, "Tuesday"
        WEDNESDAY = 2, "Wednesday"
        THURSDAY = 3, "Thursday"
        FRIDAY = 4, "Friday"
        SATURDAY = 5, "Saturday"
        SUNDAY = 6, "Sunday"

    term = models.ForeignKey("Term", on_delete=models.PROTECT, related_name="schedule_blocks")
    room = models.ForeignKey("facilities.Room", on_delete=models.PROTECT, related_name="schedule_blocks")
    subject = models.ForeignKey("Subject", null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="schedule_blocks")
    title = models.CharField(max_length=200)
    instructor = models.ForeignKey("accounts.Borrower", null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="schedule_blocks")
    day_of_week = models.PositiveSmallIntegerField(choices=DayOfWeek.choices, default=DayOfWeek.MONDAY)
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="schedule_blocks_created")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["day_of_week", "start_time", "end_time"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_time__gt=F("start_time")),
                name="scheduleblock_end_after_start",
            ),
        ]
        indexes = [
            models.Index(fields=["room", "day_of_week", "start_time"],
                         name="schedule_room_day_start_idx"),
        ]

    def __str__(self):
        return f"{self.title} ({self.term})"


class ScheduleException(models.Model):
    class ExceptionType(models.TextChoices):
        CANCELLED = "CANCELLED", "Cancelled"
        MOVED = "MOVED", "Moved"
        ROOM_CHANGE = "ROOM_CHANGE", "Room change"

    schedule_block = models.ForeignKey("ScheduleBlock", on_delete=models.PROTECT,
                                      related_name="exceptions")
    date = models.DateField()
    exception_type = models.CharField(max_length=20, choices=ExceptionType.choices)
    replacement_room = models.ForeignKey("facilities.Room", null=True, blank=True,
                                        on_delete=models.PROTECT, related_name="replacement_schedule_exceptions")
    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "schedule_block__start_time"]

    def __str__(self):
        return f"{self.schedule_block} {self.date} ({self.exception_type})"
