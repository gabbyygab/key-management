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
        role=role, **extra)


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

    def test_deactivated_account_gets_its_own_message(self):
        self.guard.is_active = False
        self.guard.save()
        url = reverse("accounts:login")
        for ident in ("guard", "GUARD@example.com"):
            res = self.client.post(url, {"username": ident, "password": "pw-Strong-12345"})
            self.assertContains(res, "deactivated by an administrator", status_code=403)
            self.assertNotContains(res, "Invalid credentials", status_code=403)

    def test_deactivated_account_with_wrong_password_stays_generic(self):
        # Without the right password the page must not reveal the account state.
        self.guard.is_active = False
        self.guard.save()
        res = self.client.post(reverse("accounts:login"), {"username": "guard", "password": "wrong"})
        self.assertContains(res, "Invalid credentials")
        self.assertNotContains(res, "deactivated")

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
                "email": "new@example.com", "contact_number": "",
                "role": User.Role.GUARD, "department": "", "assigned_post": ""}
        data.update(overrides)
        return data

    def test_create_user_is_audited(self):
        res = self.client.post(reverse("accounts:user_create") + "?modal=1", self.user_payload())
        self.assertEqual(res.status_code, 201, res.content)
        user = User.objects.get(username="newguard")
        self.assertTrue(user.check_password("Admin2026"))  # DEFAULT_USER_PASSWORD
        # The admin never types one: it is generated (accounts.services).
        self.assertEqual(user.employee_id, "EMP-0001")
        self.assertTrue(ActivityLog.objects.filter(action=ActivityLog.Action.CREATE,
                                                   model_name="User").exists())

    def test_employee_ids_are_sequential_and_skip_hand_written_ones(self):
        User.objects.filter(pk=self.admin.pk).update(employee_id="EMP-GUARD1")
        for n, username in enumerate(("one", "two"), start=1):
            res = self.client.post(reverse("accounts:user_create") + "?modal=1",
                                   self.user_payload(username=username,
                                                     email=f"{username}@example.com"))
            self.assertEqual(res.status_code, 201, res.content)
            self.assertEqual(User.objects.get(username=username).employee_id,
                             "EMP-%04d" % n)

    def test_create_user_validation_is_422_and_keeps_input(self):
        res = self.client.post(reverse("accounts:user_create") + "?modal=1",
                               self.user_payload(email="ADMIN@example.com"))  # email already taken
        self.assertEqual(res.status_code, 422)
        self.assertContains(res, "newguard", status_code=422)
        self.assertFalse(User.objects.filter(username="newguard").exists())

    def test_admin_cannot_demote_or_deactivate_self(self):
        data = self.user_payload(username="admin", email="admin@example.com",
                                 role=User.Role.GUARD)
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

    def test_delete_user_requires_post_and_removes_account(self):
        guard = make_user("g5", User.Role.GUARD)
        url = reverse("accounts:user_delete", args=[guard.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url)
        self.assertFalse(User.objects.filter(pk=guard.pk).exists())
        self.assertTrue(ActivityLog.objects.filter(action=ActivityLog.Action.DELETE,
                                                   description__contains="g5").exists())

    def test_admin_cannot_delete_own_account(self):
        self.client.post(reverse("accounts:user_delete", args=[self.admin.pk]))
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())

    def test_on_duty_guard_cannot_be_edited_or_deleted(self):
        guard = make_user("g6", User.Role.GUARD)
        GuardShift.objects.create(guard=guard, post="Main Desk", is_on_duty=True)

        payload = self.user_payload(username="g6", email="g6@example.com",
                                    first_name="Renamed")
        edit = self.client.post(reverse("accounts:user_update", args=[guard.pk]), payload)
        self.assertRedirects(edit, reverse("accounts:user_list"))
        guard.refresh_from_db()
        self.assertNotEqual(guard.first_name, "Renamed")

        # The modal path answers 422 with the reason instead of redirecting.
        modal = self.client.post(
            reverse("accounts:user_update", args=[guard.pk]) + "?modal=1", payload)
        self.assertContains(modal, "is on duty", status_code=422)

        self.client.post(reverse("accounts:user_delete", args=[guard.pk]))
        self.assertTrue(User.objects.filter(pk=guard.pk).exists())

        # Off duty, the on-duty rule no longer applies — but the recorded
        # shift still protects the row (see the next test).
        GuardShift.objects.filter(guard=guard).update(is_on_duty=False)
        edit = self.client.post(
            reverse("accounts:user_update", args=[guard.pk]) + "?modal=1", payload)
        self.assertEqual(edit.status_code, 200, edit.content)
        guard.refresh_from_db()
        self.assertEqual(guard.first_name, "Renamed")

    def test_user_with_recorded_shift_is_not_deleted(self):
        guard = make_user("g7", User.Role.GUARD)
        GuardShift.objects.create(guard=guard, post="Main Desk", is_on_duty=False)
        res = self.client.post(reverse("accounts:user_delete", args=[guard.pk]), follow=True)
        self.assertTrue(User.objects.filter(pk=guard.pk).exists())
        self.assertContains(res, "cannot be")

    def test_guard_cannot_reach_user_admin(self):
        self.client.force_login(make_user("g4", User.Role.GUARD))
        self.assertEqual(self.client.get(reverse("accounts:user_create")).status_code, 403)
        self.assertEqual(
            self.client.post(reverse("accounts:user_delete", args=[self.admin.pk])).status_code, 403)

    def test_user_list_pages_at_ten(self):
        # guide.md §5 "List pages: pagination": 10 records per page.
        User.objects.filter(username="Admin").delete()  # built-in admin (migration 0007)
        for i in range(14):
            make_user(f"pg{i:02d}", User.Role.GUARD)
        url = reverse("accounts:user_list")
        page1 = self.client.get(url)
        self.assertEqual(len(page1.context["page_obj"]), 10)
        self.assertEqual(page1.context["page_obj"].paginator.count, 15)
        self.assertContains(page1, "data-list-region")
        page2 = self.client.get(url, {"page": 2})
        self.assertEqual(len(page2.context["page_obj"]), 5)
        # Out-of-range pages clamp to the last page instead of 404ing.
        self.assertEqual(self.client.get(url, {"page": 99}).context["page_obj"].number, 2)


class SuperuserRoleTests(TestCase):
    def test_createsuperuser_makes_an_admin(self):
        user = User.objects.create_superuser("root", "root@example.com", "pw-Strong-12345")
        self.assertEqual(user.role, User.Role.ADMIN)
        self.assertEqual(user.get_role_display(), "Admin")

    def test_explicit_role_still_wins(self):
        user = User.objects.create_superuser("ops", "ops@example.com", "pw-Strong-12345",
                                             role=User.Role.FACILITIES)
        self.assertEqual(user.role, User.Role.FACILITIES)


@TESTSERVER
class OwnPasswordChangeTests(TestCase):
    URL = reverse("accounts:password_change")

    def setUp(self):
        cache.clear()
        self.guard = make_user("desk", User.Role.GUARD)
        self.client.force_login(self.guard)

    def change(self, current="pw-Strong-12345", new="My-New-Pass-77", confirm=None):
        return self.client.post(self.URL, {"current_password": current, "new_password1": new,
                                           "new_password2": confirm or new})

    def test_guard_changes_own_password_and_stays_signed_in(self):
        self.assertEqual(self.client.get(self.URL).status_code, 200)
        res = self.change()
        self.assertEqual(res.status_code, 302)
        self.guard.refresh_from_db()
        self.assertTrue(self.guard.check_password("My-New-Pass-77"))
        self.assertEqual(self.client.get(self.URL).status_code, 200)  # session kept
        self.assertTrue(ActivityLog.objects.filter(user=self.guard,
                                                   description__contains="own password").exists())

    def test_admin_can_change_own_password(self):
        admin = make_user("boss", User.Role.ADMIN)
        self.client.force_login(admin)
        self.assertEqual(self.change().status_code, 302)
        admin.refresh_from_db()
        self.assertTrue(admin.check_password("My-New-Pass-77"))

    def test_wrong_current_password_is_refused(self):
        res = self.change(current="not-it")
        self.assertEqual(res.status_code, 422)
        self.assertContains(res, "current password is incorrect", status_code=422)
        self.guard.refresh_from_db()
        self.assertTrue(self.guard.check_password("pw-Strong-12345"))

    def test_default_password_and_mismatch_are_refused(self):
        self.assertEqual(self.change(new="Admin2026").status_code, 422)
        self.assertEqual(self.change(confirm="something-else-1").status_code, 422)
        self.guard.refresh_from_db()
        self.assertTrue(self.guard.check_password("pw-Strong-12345"))

    @override_settings(LOGIN_RATE_LIMIT=3)
    def test_repeated_wrong_current_password_locks_out(self):
        for _ in range(3):
            self.change(current="guess")
        res = self.change()  # even the right one is refused while locked
        self.assertEqual(res.status_code, 429)
        self.guard.refresh_from_db()
        self.assertTrue(self.guard.check_password("pw-Strong-12345"))

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.URL).status_code, 302)


@TESTSERVER
class ForcedPasswordChangeTests(TestCase):
    """Accounts whose password an admin set must replace it before anything else."""

    def setUp(self):
        cache.clear()
        self.admin = make_user("boss", User.Role.ADMIN)
        self.client.force_login(self.admin)
        self.client.post(reverse("accounts:user_create") + "?modal=1", {
            "username": "fresh", "first_name": "Fresh", "last_name": "Guard",
            "email": "fresh@example.com", "role": User.Role.GUARD})
        self.fresh = User.objects.get(username="fresh")
        self.client.logout()

    def test_new_account_is_flagged_and_sent_to_change_page(self):
        self.assertTrue(self.fresh.must_change_password)
        res = self.client.post(reverse("accounts:login"),
                               {"username": "fresh", "password": "Admin2026"})
        self.assertEqual(res.status_code, 302)
        page = self.client.get(reverse("transactions:borrow_dashboard"))
        self.assertRedirects(page, reverse("accounts:password_change"), fetch_redirect_response=False)
        form = self.client.get(reverse("accounts:password_change"))
        self.assertContains(form, "temporary password")
        self.assertNotContains(form, "Cancel</a>")  # the form's cancel link

    def test_flag_clears_after_choosing_own_password(self):
        self.client.force_login(self.fresh)
        res = self.client.post(reverse("accounts:password_change"), {
            "current_password": "Admin2026", "new_password1": "Desk-Pass-2026x",
            "new_password2": "Desk-Pass-2026x"})
        self.assertEqual(res.status_code, 302)
        self.fresh.refresh_from_db()
        self.assertFalse(self.fresh.must_change_password)
        self.assertEqual(self.client.get(reverse("transactions:borrow_dashboard")).status_code, 200)

    def test_logout_and_static_stay_reachable(self):
        self.client.force_login(self.fresh)
        self.assertEqual(self.client.post(reverse("accounts:logout")).status_code, 302)
        self.client.force_login(self.fresh)
        res = self.client.get("/static/css/navbar.css")
        self.assertNotEqual(res.get("Location"), reverse("accounts:password_change"))

    def test_admin_reset_flags_the_account_again(self):
        guard = make_user("g9", User.Role.GUARD)
        self.assertFalse(guard.must_change_password)
        self.client.force_login(self.admin)
        self.client.post(reverse("accounts:user_reset_password", args=[guard.pk]) + "?modal=1",
                         {"new_password1": "Abc-12345-xyz", "new_password2": "Abc-12345-xyz"})
        guard.refresh_from_db()
        self.assertTrue(guard.must_change_password)

    def test_existing_accounts_are_not_flagged(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("accounts:user_list")).status_code, 200)


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

    def test_pages_split_live_from_archived(self):
        gone = Borrower.objects.create(id_number="F-2", first_name="Ben",
                                       last_name="Dizon", borrower_type="STAFF",
                                       is_active=False)
        self.client.force_login(self.admin)

        res = self.client.get(reverse("accounts:borrower_list"))
        self.assertIn(self.borrower, res.context["borrowers"])
        self.assertNotIn(gone, res.context["borrowers"])
        self.assertEqual(res.context["archived_count"], 1)

        res = self.client.get(reverse("accounts:borrower_archived"))
        self.assertIn(gone, res.context["borrowers"])
        self.assertNotIn(self.borrower, res.context["borrowers"])
        self.assertTrue(res.context["archived"])

    def test_archived_page_is_admin_only(self):
        self.client.force_login(self.guard)
        res = self.client.get(reverse("accounts:borrower_archived"))
        self.assertEqual(res.status_code, 403)

    def test_bulk_archive_and_restore(self):
        other = Borrower.objects.create(id_number="F-3", first_name="Cy",
                                        last_name="Reyes", borrower_type="STUDENT")
        self.client.force_login(self.admin)
        url = reverse("accounts:borrower_bulk")
        ids = [self.borrower.pk, other.pk]

        res = self.client.post(url, {"action": "archive", "ids": ids}, **MODAL)
        self.assertEqual(res.status_code, 200)
        self.assertIn("2 borrower(s) archived", res.json()["message"])
        self.assertFalse(Borrower.objects.filter(is_active=True).exists())
        # Archive is a soft delete: both rows are still there.
        self.assertEqual(Borrower.objects.count(), 2)

        res = self.client.post(url, {"action": "restore", "ids": ids}, **MODAL)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(Borrower.objects.filter(is_active=True).count(), 2)

    def test_bulk_archive_skips_someone_holding_a_key(self):
        building = Building.objects.create(code="B", name="B")
        room = Room.objects.create(building=building, room_code="1")
        key = Key.objects.create(key_code="B-1", room=room,
                                 category=KeyCategory.objects.create(name="Room"))
        GuardShift.objects.create(guard=self.guard, post="Main Desk")
        pos_borrow(key_id=key.pk, borrower=self.borrower, guard=self.guard)

        self.client.force_login(self.admin)
        res = self.client.post(reverse("accounts:borrower_bulk"),
                               {"action": "archive", "ids": [self.borrower.pk]},
                               **MODAL)
        self.assertEqual(res.status_code, 422)
        self.assertIn("still holds a key", res.json()["message"])
        self.borrower.refresh_from_db()
        self.assertTrue(self.borrower.is_active)

    def test_bulk_requires_post_and_admin(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("accounts:borrower_bulk")).status_code, 405)
        self.client.force_login(self.guard)
        res = self.client.post(reverse("accounts:borrower_bulk"),
                               {"action": "archive", "ids": [self.borrower.pk]})
        self.assertEqual(res.status_code, 403)


class SeedDemoTests(TestCase):
    def test_seed_is_idempotent(self):
        call_command("seed_demo", stdout=StringIO())
        counts = (User.objects.count(), Borrower.objects.count(), Key.objects.count())
        call_command("seed_demo", stdout=StringIO())
        self.assertEqual(counts, (User.objects.count(), Borrower.objects.count(), Key.objects.count()))
        self.assertTrue(User.objects.filter(username="admin", role=User.Role.ADMIN).exists())


@TESTSERVER
class DefaultAdminTests(TestCase):
    """Migration 0007 seeds Admin / Admin2026 so no createsuperuser is needed."""

    def test_default_admin_exists_and_must_change_password(self):
        admin = User.objects.get(username="Admin")
        self.assertEqual(admin.role, User.Role.ADMIN)
        self.assertTrue(admin.is_active)
        self.assertFalse(admin.is_superuser)
        self.assertTrue(admin.check_password("Admin2026"))
        self.assertTrue(admin.must_change_password)

    def test_default_admin_can_sign_in_and_is_sent_to_change_password(self):
        cache.clear()
        res = self.client.post(reverse("accounts:login"),
                               {"username": "Admin", "password": "Admin2026"})
        self.assertEqual(res.status_code, 302)
        self.assertRedirects(self.client.get(reverse("dashboard")),
                             reverse("accounts:password_change"), fetch_redirect_response=False)
