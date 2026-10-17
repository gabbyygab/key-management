from django.contrib import admin
from .models import GuardShift, BorrowTransaction


@admin.register(GuardShift)
class GuardShiftAdmin(admin.ModelAdmin):
    list_display = ("guard", "post", "shift_start", "shift_end", "is_on_duty")
    list_filter = ("is_on_duty", "post")


@admin.register(BorrowTransaction)
class BorrowTransactionAdmin(admin.ModelAdmin):
    list_display = ("transaction_no", "key", "borrower", "status", "borrowed_at", "released_by")
    list_filter = ("status",)
    search_fields = ("transaction_no", "key__key_code", "borrower__id_number",
                      "borrower__first_name", "borrower__last_name")
