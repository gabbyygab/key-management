from django.contrib import admin
from .models import Building, Room


@admin.register(Building)
class BuildingAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active")


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("__str__", "name", "room_type", "floor", "status", "is_active")
    list_filter = ("building", "room_type", "status", "is_active")
    search_fields = ("room_code", "name")
