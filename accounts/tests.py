from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from transactions.models import GuardShift

User = get_user_model()


class RoleAccessTests(TestCase):
    def setUp(self):
        self.guard = User.objects.create_user(
            username="guard1",
            email="guard1@example.com",
            password="secret123",
            role=User.Role.GUARD,
            approval_status=User.Approval.APPROVED,
        )
        self.admin = User.objects.create_user(
            username="admin1",
            email="admin1@example.com",
            password="secret123",
            role=User.Role.ADMIN,
            approval_status=User.Approval.APPROVED,
        )

    def test_guard_login_redirects_to_pos(self):
        response = self.client.post(
            reverse("accounts:login"),
            {"username": "guard1", "password": "secret123"},
            follow=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/transactions/borrow/")

    def test_admin_login_redirects_to_dashboard(self):
        response = self.client.post(
            reverse("accounts:login"),
            {"username": "admin1", "password": "secret123"},
            follow=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")

    def test_guard_cannot_access_admin_master_data(self):
        self.client.force_login(self.guard)
        response = self.client.get(reverse("keys:key_list"))
        self.assertEqual(response.status_code, 403)

    def test_admin_can_access_admin_master_data(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("keys:key_list"))
        self.assertEqual(response.status_code, 200)


# ---------------------------------------------------------------------------
# F2.3 — navbar context processor tests
# ---------------------------------------------------------------------------

@override_settings(ALLOWED_HOSTS=["testserver"])
class NavbarContextTests(TestCase):
    """The context processor must inject the right `navbar_shift` value."""

    def setUp(self):
        self.guard = User.objects.create_user(
            username="guard_nb",
            email="guard_nb@example.com",
            password="secret123",
            role=User.Role.GUARD,
            approval_status=User.Approval.APPROVED,
        )
        self.admin = User.objects.create_user(
            username="admin_nb",
            email="admin_nb@example.com",
            password="secret123",
            role=User.Role.ADMIN,
            approval_status=User.Approval.APPROVED,
        )

    # -- guard with no open shift ----------------------------------------

    def test_guard_no_shift_context_is_none(self):
        self.client.force_login(self.guard)
        # force_login fires user_logged_in, which opens a shift via signal.
        # Close it so the guard is genuinely off-duty for this test.
        GuardShift.objects.filter(guard=self.guard, is_on_duty=True).update(
            is_on_duty=False
        )
        response = self.client.get(reverse("transactions:borrow_dashboard"))
        self.assertIn("navbar_shift", response.context)
        self.assertIsNone(response.context["navbar_shift"])

    # -- guard with an open shift ----------------------------------------

    def test_guard_active_shift_context_has_shift(self):
        shift = GuardShift.objects.create(guard=self.guard)
        self.client.force_login(self.guard)
        response = self.client.get(reverse("transactions:borrow_dashboard"))
        self.assertIn("navbar_shift", response.context)
        self.assertEqual(response.context["navbar_shift"].pk, shift.pk)

    # -- admin never gets a shift in context (they see the + dropdown) ---

    def test_admin_context_shift_is_none(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard"))
        self.assertIn("navbar_shift", response.context)
        self.assertIsNone(response.context["navbar_shift"])


@override_settings(ALLOWED_HOSTS=["testserver"])
class NavbarRenderTests(TestCase):
    """Critical navbar elements must be present in the rendered HTML."""

    def setUp(self):
        self.guard = User.objects.create_user(
            username="guard_render",
            email="guard_render@example.com",
            password="secret123",
            role=User.Role.GUARD,
            approval_status=User.Approval.APPROVED,
        )
        self.admin = User.objects.create_user(
            username="admin_render",
            email="admin_render@example.com",
            password="secret123",
            role=User.Role.ADMIN,
            approval_status=User.Approval.APPROVED,
        )

    # -- breadcrumb ----------------------------------------------------------

    def test_breadcrumb_rendered(self):
        """Navbar breadcrumb contains 'KEYTRACK'."""
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "KEYTRACK")

    # -- guard shift indicator: no shift ------------------------------------

    def test_no_shift_warning_shown_for_guard(self):
        """Guard with no open shift must see the 'No active shift' warning."""
        self.client.force_login(self.guard)
        # force_login triggers the login signal which opens a shift.
        # Close it to put the guard into the off-duty state we want to test.
        GuardShift.objects.filter(guard=self.guard, is_on_duty=True).update(
            is_on_duty=False
        )
        response = self.client.get(reverse("transactions:borrow_dashboard"))
        self.assertContains(response, "No active shift")

    # -- guard shift indicator: shift open ----------------------------------

    def test_on_duty_indicator_shown_when_shift_open(self):
        """Guard with an active shift must see 'On duty since'."""
        GuardShift.objects.create(guard=self.guard)
        self.client.force_login(self.guard)
        response = self.client.get(reverse("transactions:borrow_dashboard"))
        self.assertContains(response, "On duty since")
        self.assertNotContains(response, "No active shift")

    # -- admin quick-add dropdown ------------------------------------------

    def test_admin_quickadd_dropdown_present(self):
        """Admin users must see the quick-add '+' dropdown trigger."""
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "navbar-quickadd-btn")
        # Dropdown must offer New Key, New Room, New Borrower, New Schedule
        self.assertContains(response, "New Key")
        self.assertContains(response, "New Room")
        self.assertContains(response, "New Borrower")
        self.assertContains(response, "New Schedule")

    def test_guard_cannot_see_quickadd_dropdown(self):
        """Guard users must NOT see the quick-add '+' dropdown."""
        self.client.force_login(self.guard)
        response = self.client.get(reverse("transactions:borrow_dashboard"))
        self.assertNotContains(response, "navbar-quickadd-btn")

    # -- server clock -------------------------------------------------------

    def test_server_clock_element_present(self):
        """The server-clock element with a data-server-now seed must be in the HTML."""
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "navbar-server-clock")
        self.assertContains(response, "data-server-now")
