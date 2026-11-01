"""Tests for the admin user/borrower screens, login hardening and the demo
seed (B2.1, B2.2, B7.4, B8.2, B8.3, F4.5, F4.6)."""
from io import StringIO

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from audit.models import ActivityLog
from facilities.models import Building, Room
from keys.models import Key, KeyCategory
from transactions.models import GuardShift
from transactions.services import pos_borrow

from .models import Borrower, Department, User

TESTSERVER = override_settings(ALLOWED_HOSTS=["testserver"])
MODAL = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


def make_user(username, role, **extra):
    return User.objects.create_user(
        username=username, email=f"{username}@example.com", password="pw-Strong-12345",
        role=role, approval_status=User.Approval.APPROVED, **extra)


@TESTSERVER
class LoginHardeningTests(TestCase):
    def setUp(self):
        cache.clear()
        self.guard = make_user("guard", User.Role.GUARD)

    def test_logout_requires_post(self):
        self.client.force_login(self.guard)
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        res = self.client.post(reverse("accounts:logout"))
        self.assertEqual(res.status_code, 302)
        self.assertFalse(GuardShift.objects.filter(guard=self.guard, is_on_duty=True).exists())

    @override_settings(LOGIN_RATE_LIMIT=3, LOGIN_RATE_WINDOW=300)
    def test_rate_limit_after_repeated_failures(self):
        url = reverse("accounts:login")
        for _ in range(3):
            res = self.client.post(url, {"username": "guard", "password": "wrong"})
            self.assertEqual(res.status_code, 200)
        # Even the right password is refused while throttled.
        res = self.client.post(url, {"username": "guard", "password": "pw-Strong-12345"})
        self.assertEqual(res.status_code, 429)
        self.assertContains(res, "Too many failed sign-in attempts", status_code=429)

    def test_login_and_logout_are_audited(self):
        self.client.post(reverse("accounts:login"),
                         {"username": "guard", "password": "pw-Strong-12345"})
        self.client.post(reverse("accounts:logout"))
        actions = set(ActivityLog.objects.filter(user=self.guard).values_list("action", flat=True))
        self.assertTrue({ActivityLog.Action.LOGIN, ActivityLog.Action.LOGOUT} <= actions)


@TESTSERVER
class UserAdminTests(TestCase):
    def setUp(self):
        self.admin = make_user("admin", User.Role.ADMIN)
        self.client.force_login(self.admin)

    def user_payload(self, **overrides):
        data = {"username": "newguard", "first_name": "New", "last_name": "Guard",
                "email": "new@example.com", "employee_id": "", "contact_number": "",
                "role": User.Role.GUARD, "department": "", "assigned_post": "",
                "password1": "Correct-Horse-9", "password2": "Correct-Horse-9"}
        data.update(overrides)
        return data

    def test_create_user_is_preapproved_and_audited(self):
        res = self.client.post(reverse("accounts:user_create") + "?modal=1", self.user_payload())
        self.assertEqual(res.status_code, 201, res.content)
        user = User.objects.get(username="newguard")
        self.assertEqual(user.approval_status, User.Approval.APPROVED)
        self.assertEqual(user.approved_by, self.admin)
        self.assertTrue(user.check_password("Correct-Horse-9"))
        self.assertIsNone(user.employee_id)  # blank stored as NULL, not ""
        self.assertTrue(ActivityLog.objects.filter(action=ActivityLog.Action.CREATE,
                                                   model_name="User").exists())

    def test_create_user_validation_is_422_and_keeps_input(self):
        res = self.client.post(reverse("accounts:user_create") + "?modal=1",
                               self.user_payload(password2="different", email="ADMIN@example.com"))
        self.assertEqual(res.status_code, 422)
        self.assertContains(res, "newguard", status_code=422)
        self.assertFalse(User.objects.filter(username="newguard").exists())

    def test_admin_cannot_demote_or_deactivate_self(self):
        data = self.user_payload(username="admin", email="admin@example.com",
                                 role=User.Role.GUARD)
        data.pop("password1"); data.pop("password2")
        res = self.client.post(reverse("accounts:user_update", args=[self.admin.pk]) + "?modal=1", data)
        self.assertEqual(res.status_code, 422)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, User.Role.ADMIN)

        self.client.post(reverse("accounts:user_toggle", args=[self.admin.pk]))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_deactivating_guard_ends_open_shift(self):
        guard = make_user("g2", User.Role.GUARD)
        GuardShift.objects.create(guard=guard, post="Main Desk")
        self.assertEqual(self.client.get(reverse("accounts:user_toggle", args=[guard.pk])).status_code, 405)
        self.client.post(reverse("accounts:user_toggle", args=[guard.pk]))
        guard.refresh_from_db()
        self.assertFalse(guard.is_active)
        self.assertFalse(GuardShift.objects.filter(guard=guard, is_on_duty=True).exists())

    def test_password_reset(self):
        guard = make_user("g3", User.Role.GUARD)
        url = reverse("accounts:user_reset_password", args=[guard.pk]) + "?modal=1"
        bad = self.client.post(url, {"new_password1": "Abc-12345-xyz", "new_password2": "nope"})
        self.assertEqual(bad.status_code, 422)
        ok = self.client.post(url, {"new_password1": "Abc-12345-xyz", "new_password2": "Abc-12345-xyz"})
        self.assertEqual(ok.status_code, 200)
        guard.refresh_from_db()
        self.assertTrue(guard.check_password("Abc-12345-xyz"))

    def test_guard_cannot_reach_user_admin(self):
        self.client.force_login(make_user("g4", User.Role.GUARD))
        self.assertEqual(self.client.get(reverse("accounts:user_create")).status_code, 403)


@TESTSERVER
class BorrowerAdminTests(TestCase):
    def setUp(self):
        self.admin = make_user("admin", User.Role.ADMIN)
        self.guard = make_user("guard", User.Role.GUARD)
        self.dept = Department.objects.create(code="CCS", name="Computer Studies")
        self.borrower = Borrower.objects.create(id_number="F-1", first_name="Ana",
                                                last_name="Cruz", borrower_type="FACULTY")

    def test_cannot_deactivate_while_holding_a_key(self):
        building = Building.objects.create(code="B", name="B")
        room = Room.objects.create(building=building, room_code="1")
        key = Key.objects.create(key_code="B-1", room=room,
                                 category=KeyCategory.objects.create(name="Room"))
        GuardShift.objects.create(guard=self.guard, post="Main Desk")
        pos_borrow(key_id=key.pk, borrower=self.borrower, guard=self.guard)

        self.client.force_login(self.admin)
        self.client.post(reverse("accounts:borrower_toggle", args=[self.borrower.pk]))
        self.borrower.refresh_from_db()
        self.assertTrue(self.borrower.is_active)

        res = self.client.get(reverse("accounts:borrower_list"), {"auth": "holding"})
        self.assertContains(res, "Holding a key")

    def test_history_modal_and_page(self):
        self.client.force_login(self.admin)
        url = reverse("accounts:borrower_history", args=[self.borrower.pk])
        self.assertContains(self.client.get(url, **MODAL), "No transactions yet")
        self.assertContains(self.client.get(url), "Ana Cruz")


class SeedDemoTests(TestCase):
    def test_seed_is_idempotent(self):
        call_command("seed_demo", stdout=StringIO())
        counts = (User.objects.count(), Borrower.objects.count(), Key.objects.count())
        call_command("seed_demo", stdout=StringIO())
        self.assertEqual(counts, (User.objects.count(), Borrower.objects.count(), Key.objects.count()))
        self.assertTrue(User.objects.filter(username="admin", role=User.Role.ADMIN).exists())
