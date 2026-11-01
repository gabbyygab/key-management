from django.contrib import admin
from .models import KeyCategory, Key


@admin.register(KeyCategory)
class KeyCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "key_count", "description")
    list_filter = ("is_active",)
    search_fields = ("name", "description")

    def key_count(self, obj):
        return obj.key_count
    key_count.short_description = "Keys"


@admin.register(Key)
class KeyAdmin(admin.ModelAdmin):
    list_display = ("key_code", "room", "category", "copy_number", "status")
    list_filter = ("status", "category", "room__building")
    search_fields = ("key_code", "room__room_code")
