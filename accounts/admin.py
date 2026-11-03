from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, Department, Borrower


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("Key System", {"fields": ("employee_id", "contact_number", "role", "department",
                                   "profile_picture", "must_change_password")}),
    )
    list_display = ("username", "email", "role", "is_active",
                    "must_change_password")
    list_filter = ("role", "is_active")
    search_fields = ("username", "email", "employee_id", "first_name", "last_name")

    def has_add_permission(self, request):
        # Only admins add accounts. A staff flag alone (e.g. a guard who was
        # given is_staff) is not enough.
        return request.user.is_admin_role and super().has_add_permission(request)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active")


@admin.register(Borrower)
class BorrowerAdmin(admin.ModelAdmin):
    list_display = ("id_number", "last_name", "first_name", "borrower_type",
                    "department", "is_authorized", "is_active")
    list_filter = ("borrower_type", "is_authorized", "is_active", "department")
    search_fields = ("id_number", "first_name", "last_name")
