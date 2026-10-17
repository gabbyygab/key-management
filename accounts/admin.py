from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, Department, Borrower


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("Key System", {"fields": ("employee_id", "contact_number", "role", "department",
                                   "profile_picture", "approval_status",
                                   "approved_by", "approved_at")}),
    )
    list_display = ("username", "email", "role", "approval_status", "is_active")
    list_filter = ("role", "approval_status", "is_active")
    search_fields = ("username", "email", "employee_id", "first_name", "last_name")


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active")


@admin.register(Borrower)
class BorrowerAdmin(admin.ModelAdmin):
    list_display = ("id_number", "last_name", "first_name", "borrower_type",
                    "department", "is_authorized", "is_active")
    list_filter = ("borrower_type", "is_authorized", "is_active", "department")
    search_fields = ("id_number", "first_name", "last_name")
