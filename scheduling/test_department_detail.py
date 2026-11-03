"""Department detail: faculty, their held times and the department's subjects;
and the borrower form only keeps a department for Faculty/Staff."""
from datetime import time

from django.urls import reverse
from django.utils import timezone

from accounts.forms import BorrowerForm
from accounts.models import Borrower, Department, User

from .models import ScheduleBlock, Subject
from .services import faculty_schedule
from .test_admin import SchedulingFixture


class DepartmentDetailTests(SchedulingFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.dept = Department.objects.create(code="CCS", name="Computing")
        cls.prof = Borrower.objects.create(
            id_number="F-1", first_name="Ada", last_name="Lovelace",
            borrower_type=Borrower.BorrowerType.FACULTY, department=cls.dept)
        cls.student = Borrower.objects.create(
            id_number="S-1", first_name="Stu", last_name="Dent",
            borrower_type=Borrower.BorrowerType.STUDENT, department=cls.dept)
        cls.subject = Subject.objects.create(code="IT101", title="Intro", department=cls.dept)
        cls.guard = User.objects.create_user(
            username="g", email="g@example.com", password="pw-Strong-12345", role=User.Role.GUARD)

    def hold(self, **kw):
        b = self.block(**kw)
        b.instructor, b.subject = self.prof, self.subject
        b.save()
        return b

    def test_detail_lists_faculty_times_and_subjects(self):
        self.hold(day=2, start=(9, 0), end=(10, 30))
        self.client.force_login(self.admin)
        r = self.client.get(reverse("scheduling:department_detail", args=[self.dept.pk]))
        self.assertContains(r, "Lovelace")
        self.assertContains(r, "IT101")
        self.assertContains(r, "9:00 AM")
        self.assertNotContains(r, "Dent")

    def test_detail_is_admin_only(self):
        self.client.force_login(self.guard)
        r = self.client.get(reverse("scheduling:department_detail", args=[self.dept.pk]))
        self.assertEqual(r.status_code, 403)

    def test_faculty_schedule_flags_now(self):
        today = timezone.localtime()
        self.term.start_date, self.term.end_date = today.date(), today.date()
        self.term.save()
        self.hold(day=today.weekday(), start=(0, 0), end=(23, 59))
        blocks = faculty_schedule([self.prof], when=today.replace(hour=12, minute=0))[self.prof.pk]
        self.assertTrue(blocks[0].is_today)
        self.assertTrue(blocks[0].is_now)

    def test_guard_lookup_shows_todays_class(self):
        today = timezone.localtime()
        self.term.start_date, self.term.end_date = today.date(), today.date()
        self.term.save()
        self.hold(day=today.weekday(), start=(0, 0), end=(23, 59))
        self.client.force_login(self.guard)
        r = self.client.get(reverse("accounts:borrower_lookup"))
        self.assertContains(r, "IT101")


class BorrowerDepartmentFormTests(SchedulingFixture):
    def data(self, type_, dept):
        return {"id_number": "X-1", "first_name": "A", "last_name": "B",
                "borrower_type": type_, "department": dept.pk, "is_authorized": "on",
                "is_active": "on"}

    def test_department_kept_for_faculty_and_staff_only(self):
        dept = Department.objects.create(code="CCS", name="Computing")
        for type_, kept in (("FACULTY", True), ("STAFF", True), ("STUDENT", False), ("OTHER", False)):
            form = BorrowerForm(self.data(type_, dept))
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.cleaned_data["department"] == dept, kept, type_)
