from django.db import models


class Building(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class Room(models.Model):
    class RoomType(models.TextChoices):
        CLASSROOM = "CLASSROOM", "Classroom"
        LABORATORY = "LABORATORY", "Laboratory"
        OFFICE = "OFFICE", "Office"
        FACILITY = "FACILITY", "Facility"
        OTHER = "OTHER", "Other"

    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        OCCUPIED = "OCCUPIED", "Occupied"
        MAINTENANCE = "MAINTENANCE", "Maintenance"
        INACTIVE = "INACTIVE", "Inactive"

    building = models.ForeignKey(Building, on_delete=models.PROTECT, related_name="rooms")
    room_code = models.CharField(max_length=30)
    name = models.CharField(max_length=150, blank=True)
    room_type = models.CharField(max_length=15, choices=RoomType.choices,
                                 default=RoomType.CLASSROOM)
    floor = models.IntegerField(null=True, blank=True)
    capacity = models.IntegerField(null=True, blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.AVAILABLE)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["building__code", "room_code"]
        constraints = [
            models.UniqueConstraint(fields=["building", "room_code"],
                                    name="unique_room_per_building"),
        ]

    def __str__(self):
        return f"{self.building.code}-{self.room_code}"
