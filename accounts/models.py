from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class Department(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        FACILITIES = "FACILITIES", "Facilities"
        CUSTODIAN = "CUSTODIAN", "Custodian"
        GUARD = "GUARD", "Guard"
        FACULTY_STAFF = "FACULTY_STAFF", "Faculty / Staff"

    class Approval(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    email = models.EmailField(unique=True)
    employee_id = models.CharField(max_length=50, unique=True, null=True, blank=True)
    contact_number = models.CharField(max_length=30, blank=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.FACULTY_STAFF)
    department = models.ForeignKey(Department, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="staff")
    profile_picture = models.ImageField(upload_to="profiles/", blank=True)
    approval_status = models.CharField(max_length=10, choices=Approval.choices,
                                       default=Approval.PENDING)
    approved_by = models.ForeignKey("self", null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="approvals")
    approved_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_approved(self):
        return self.approval_status == self.Approval.APPROVED

    @property
    def full_name(self):
        return self.get_full_name() or self.username

    def has_role(self, *roles):
        return self.role in roles

    def __str__(self):
        return f"{self.full_name} ({self.role})"


class Borrower(models.Model):
    """Searchable person who can borrow keys (professor/staff/student).

    Guard searches this in Borrow Management. Linked User is optional
    (only if the person also logs in).
    """
    class BorrowerType(models.TextChoices):
        FACULTY = "FACULTY", "Faculty"
        STAFF = "STAFF", "Staff"
        STUDENT = "STUDENT", "Student"
        OTHER = "OTHER", "Other"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="borrower_profile")
    id_number = models.CharField(max_length=50, unique=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    borrower_type = models.CharField(max_length=10, choices=BorrowerType.choices)
    department = models.ForeignKey(Department, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="borrowers")
    contact_number = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    is_authorized = models.BooleanField(default=True)
    authorized_until = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["last_name", "first_name"]

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def can_borrow(self):
        from django.utils import timezone
        if not (self.is_active and self.is_authorized):
            return False
        if self.authorized_until and self.authorized_until < timezone.localdate():
            return False
        return True

    def active_transaction(self):
        from transactions.models import BorrowTransaction
        return self.transactions.filter(
            status=BorrowTransaction.Status.ACTIVE).select_related("key__room__building").first()

    def __str__(self):
        return f"{self.last_name}, {self.first_name} ({self.id_number})"
