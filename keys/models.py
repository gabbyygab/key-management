from django.conf import settings
from django.db import models


class KeyCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = "key categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Key(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        BORROWED = "BORROWED", "Borrowed"
        OVERDUE = "OVERDUE", "Overdue"
        LOST = "LOST", "Lost"
        DAMAGED = "DAMAGED", "Damaged"
        INACTIVE = "INACTIVE", "Inactive"

    key_code = models.CharField(max_length=50, unique=True)
    room = models.ForeignKey("facilities.Room", on_delete=models.PROTECT, related_name="keys")
    category = models.ForeignKey(KeyCategory, on_delete=models.PROTECT, related_name="keys")
    description = models.CharField(max_length=255, blank=True)
    copy_number = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.AVAILABLE)
    remarks = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                   related_name="keys_created")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key_code"]
        constraints = [
            models.UniqueConstraint(fields=["room", "category", "copy_number"],
                                    name="unique_key_copy_per_room_category"),
        ]

    @property
    def is_original(self):
        return self.copy_number == 1

    @property
    def is_available(self):
        return self.status == self.Status.AVAILABLE

    def __str__(self):
        return f"{self.key_code} ({self.room})"
