from django.db import models

from accounts.retention import ArchiveClockMixin


class Building(ArchiveClockMixin):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    # When this row was archived (is_active=False). Starts the retention clock
    # read by accounts.retention / `manage.py purge_archived`; cleared on
    # restore. Never set by hand — go through retention.mark_archived().
    archived_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class Room(ArchiveClockMixin):
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
    # The department this room is designated to. The auto-scheduler only puts a
    # department's classes in that department's rooms (scheduling/planner.py
    # candidate_rooms); a room with no department is shared and open to all.
    department = models.ForeignKey("accounts.Department", null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="rooms",
                                   help_text="Leave blank for a shared room any department may use.")
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.AVAILABLE)
    is_active = models.BooleanField(default=True)
    # See Building.archived_at.
    archived_at = models.DateTimeField(null=True, blank=True, editable=False)
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


class GuardPost(ArchiveClockMixin):
    """A desk or gate a guard is stationed at (B2.5).

    The admin assigns a post to each guard (User.assigned_post); the login
    signal stamps it onto the GuardShift so shift reports can tell gates apart.
    """
    name = models.CharField(max_length=100, unique=True)
    building = models.ForeignKey(Building, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="guard_posts")
    is_active = models.BooleanField(default=True)
    # See Building.archived_at.
    archived_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
