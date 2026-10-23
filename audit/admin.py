from django.contrib import admin
from .models import ActivityLog


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "action", "model_name", "object_repr", "ip_address")
    list_filter = ("action", "model_name", "created_at")
    search_fields = ("user__username", "user__email", "description", "object_repr")
