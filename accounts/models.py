from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
from django.db import models

from .retention import ArchiveClockMixin


class Department(ArchiveClockMixin):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)
    # See facilities.Building.archived_at.
    archived_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class UserManager(DjangoUserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        # A superuser is an admin: without this it got the model default
        # (Faculty / Staff) and showed up as such in the navbar and the
        # Users & Guards list.
        extra_fields.setdefault("role", User.Role.ADMIN)
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        FACILITIES = "FACILITIES", "Facilities"
        CUSTODIAN = "CUSTODIAN", "Custodian"
        GUARD = "GUARD", "Guard"
        FACULTY_STAFF = "FACULTY_STAFF", "Faculty / Staff"

    email = models.EmailField(unique=True)
    employee_id = models.CharField(max_length=50, unique=True, null=True, blank=True)
    contact_number = models.CharField(max_length=30, blank=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.FACULTY_STAFF)
    department = models.ForeignKey(Department, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="staff")
    profile_picture = models.ImageField(upload_to="profiles/", blank=True)
    # B2.5: the post this guard staffs. Read by the login signal when it opens
    # the GuardShift; null falls back to GuardShift's "Main Desk" default.
    assigned_post = models.ForeignKey("facilities.GuardPost", null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name="guards")
    # The auth.users id in Supabase once this account signs in through Supabase
    # Auth (accounts/supabase_auth.py). Set by admin provisioning or on the
    # first login after the switch; never from a bare email match.
    supabase_uid = models.UUIDField(null=True, blank=True, unique=True, editable=False)
    # Set when an admin picks the password (Add user gives DEFAULT_USER_PASSWORD,
    # or Reset password). ForcePasswordChangeMiddleware keeps the user on
    # accounts:password_change until they choose their own.
    must_change_password = models.BooleanField(default=False)

    objects = UserManager()

    @property
    def full_name(self):
        return self.get_full_name() or self.username

    @property
    def initials(self):
        if self.first_name and self.last_name:
            return (self.first_name[:1] + self.last_name[:1]).upper()
        return (self.username or "?")[:2].upper()

    @property
    def is_admin_role(self):
        return self.is_superuser or self.role == self.Role.ADMIN

    @property
    def is_on_duty(self):
        """True while this account has an open guard shift.

        A guard is "on duty" from the login that opens the shift until logout
        (or deactivation) closes it. Editing or deleting the account mid-shift
        would change or remove the person a running shift and its transactions
        point at, so both are refused while this is True.
        """
        from transactions.models import GuardShift
        return GuardShift.objects.filter(guard=self, is_on_duty=True).exists()

    def has_role(self, *roles):
        return self.role in roles

    def __str__(self):
        return f"{self.full_name} ({self.role})"


class Borrower(ArchiveClockMixin):
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
    # See facilities.Building.archived_at.
    archived_at = models.DateTimeField(null=True, blank=True, editable=False)
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
