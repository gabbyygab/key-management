from django.contrib import admin

from .models import SyncCursor


@admin.register(SyncCursor)
class SyncCursorAdmin(admin.ModelAdmin):
    list_display = ("table", "last_status", "rows_last_run", "last_run_at", "watermark")
    list_filter = ("last_status",)
    readonly_fields = ("table", "watermark", "last_run_at", "last_status", "last_error",
                       "rows_last_run")

    def has_add_permission(self, request):
        return False
