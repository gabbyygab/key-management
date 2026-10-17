from django.contrib import admin
from .models import KeyCategory, Key

admin.site.register(KeyCategory)


@admin.register(Key)
class KeyAdmin(admin.ModelAdmin):
    list_display = ("key_code", "room", "category", "copy_number", "status")
    list_filter = ("status", "category", "room__building")
    search_fields = ("key_code", "room__room_code")
