from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .test_components import SharedComponentsTests
from .context_processors import pending_approvals
from .models import Department
from .services import ApprovalError, approve_user, pending_count, reject_user
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


class PendingApprovalsWorkflowTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(code="CS", name="Computer Science")
        self.admin = User.objects.create_user(
            username="admin_user",
            email="admin_user@example.com",
            password="secret123",
            role=User.Role.ADMIN,
            approval_status=User.Approval.APPROVED,
        )
        self.guard = User.objects.create_user(
            username="guard_user",
            email="guard_user@example.com",
            password="secret123",
            role=User.Role.GUARD,
            approval_status=User.Approval.APPROVED,
        )
        self.pending_user = User.objects.create_user(
            username="pending_faculty",
            first_name="Ada",
            last_name="Lovelace",
            email="ada@example.com",
            password="secret123",
            role=User.Role.FACULTY_STAFF,
            department=self.dept,
            employee_id="EMP-001",
            approval_status=User.Approval.PENDING,
        )

    def test_approve_user_service(self):
        user = approve_user(user_id=self.pending_user.pk, approved_by=self.admin)
        self.assertEqual(user.approval_status, User.Approval.APPROVED)
        self.assertEqual(user.approved_by, self.admin)
        self.assertIsNotNone(user.approved_at)
        self.assertTrue(user.is_approved)

        # Cannot approve twice
        with self.assertRaises(ApprovalError):
            approve_user(user_id=self.pending_user.pk, approved_by=self.admin)

    def test_reject_user_service(self):
        # Empty reason should raise ApprovalError
        with self.assertRaises(ApprovalError):
            reject_user(user_id=self.pending_user.pk, rejected_by=self.admin, reason="   ")

        user = reject_user(
            user_id=self.pending_user.pk,
            rejected_by=self.admin,
            reason="Incomplete ID proof",
        )
        self.assertEqual(user.approval_status, User.Approval.REJECTED)
        self.assertEqual(user.approved_by, self.admin)
        self.assertIsNotNone(user.approved_at)
        self.assertFalse(user.is_approved)

        # Cannot reject already-rejected user
        with self.assertRaises(ApprovalError):
            reject_user(user_id=self.pending_user.pk, rejected_by=self.admin, reason="Duplicate")

    def test_pending_count_service(self):
        self.assertEqual(pending_count(), 1)
        approve_user(user_id=self.pending_user.pk, approved_by=self.admin)
        self.assertEqual(pending_count(), 0)

    def test_guard_cannot_access_pending_approval_views(self):
        self.client.force_login(self.guard)

        list_resp = self.client.get(reverse("accounts:pending_user_list"))
        self.assertEqual(list_resp.status_code, 403)

        approve_resp = self.client.post(
            reverse("accounts:user_approve", kwargs={"pk": self.pending_user.pk})
        )
        self.assertEqual(approve_resp.status_code, 403)

        reject_resp = self.client.post(
            reverse("accounts:user_reject", kwargs={"pk": self.pending_user.pk}),
            {"reason": "Unauthorized"},
        )
        self.assertEqual(reject_resp.status_code, 403)

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get(reverse("accounts:pending_user_list"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("accounts:login"), resp.url)

    def test_admin_view_pending_list_and_empty_state(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("accounts:pending_user_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Ada Lovelace")
        self.assertContains(resp, "@pending_faculty")
        self.assertContains(resp, "Approve")
        self.assertContains(resp, "Reject")
        # Sidebar should display pending badge count
        self.assertContains(resp, "Pending Approvals")

        # Approve and confirm empty state
        approve_user(user_id=self.pending_user.pk, approved_by=self.admin)
        empty_resp = self.client.get(reverse("accounts:pending_user_list"))
        self.assertEqual(empty_resp.status_code, 200)
        self.assertContains(empty_resp, "No pending approvals")

    def test_admin_approve_flow_post_only(self):
        self.client.force_login(self.admin)

        # GET should be rejected with 405 Method Not Allowed
        get_resp = self.client.get(
            reverse("accounts:user_approve", kwargs={"pk": self.pending_user.pk})
        )
        self.assertEqual(get_resp.status_code, 405)

        # POST approves and redirects to pending list
        post_resp = self.client.post(
            reverse("accounts:user_approve", kwargs={"pk": self.pending_user.pk}),
            follow=True,
        )
        self.assertEqual(post_resp.status_code, 200)
        self.pending_user.refresh_from_db()
        self.assertEqual(self.pending_user.approval_status, User.Approval.APPROVED)
        self.assertContains(post_resp, "Approved registration for")

    def test_admin_reject_flow_requires_reason(self):
        self.client.force_login(self.admin)

        # GET should be rejected with 405 Method Not Allowed
        get_resp = self.client.get(
            reverse("accounts:user_reject", kwargs={"pk": self.pending_user.pk})
        )
        self.assertEqual(get_resp.status_code, 405)

        # POST without reason should fail and keep status PENDING
        post_no_reason = self.client.post(
            reverse("accounts:user_reject", kwargs={"pk": self.pending_user.pk}),
            {"reason": "   "},
            follow=True,
        )
        self.assertEqual(post_no_reason.status_code, 200)
        self.pending_user.refresh_from_db()
        self.assertEqual(self.pending_user.approval_status, User.Approval.PENDING)
        self.assertContains(post_no_reason, "A reason is required to reject an account registration.")

        # POST with reason succeeds
        post_with_reason = self.client.post(
            reverse("accounts:user_reject", kwargs={"pk": self.pending_user.pk}),
            {"reason": "Not an active faculty member"},
            follow=True,
        )
        self.assertEqual(post_with_reason.status_code, 200)
        self.pending_user.refresh_from_db()
        self.assertEqual(self.pending_user.approval_status, User.Approval.REJECTED)
        self.assertContains(post_with_reason, "Rejected registration for")

    def test_login_blocked_for_pending_and_rejected_users(self):
        from django.contrib.auth import authenticate

        # Pending user cannot authenticate
        user = authenticate(username="pending_faculty", password="secret123")
        self.assertIsNone(user)

        # Rejected user cannot authenticate
        reject_user(user_id=self.pending_user.pk, rejected_by=self.admin, reason="Rejected")
        user = authenticate(username="pending_faculty", password="secret123")
        self.assertIsNone(user)

        # Re-set to approved
        self.pending_user.approval_status = User.Approval.APPROVED
        self.pending_user.save()
        user = authenticate(username="pending_faculty", password="secret123")
        self.assertIsNotNone(user)
        self.assertEqual(user.username, "pending_faculty")

    def test_context_processor(self):
        from django.test import RequestFactory
        factory = RequestFactory()

        # Admin request
        admin_req = factory.get("/")
        admin_req.user = self.admin
        data = pending_approvals(admin_req)
        self.assertEqual(data["pending_approvals_count"], 1)

        # Guard request
        guard_req = factory.get("/")
        guard_req.user = self.guard
        data = pending_approvals(guard_req)
        self.assertEqual(data["pending_approvals_count"], 0)


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


# ---------------------------------------------------------------------------
# F7.1 / B8.1 — Login 'next' parameter handling and open redirect prevention
# ---------------------------------------------------------------------------

@override_settings(ALLOWED_HOSTS=["testserver"])
class LoginNextParameterTests(TestCase):
    def setUp(self):
        self.guard = User.objects.create_user(
            username="guard_test",
            email="guard_test@example.com",
            password="password123",
            role=User.Role.GUARD,
            approval_status=User.Approval.APPROVED,
        )
        self.admin = User.objects.create_user(
            username="admin_test",
            email="admin_test@example.com",
            password="password123",
            role=User.Role.ADMIN,
            approval_status=User.Approval.APPROVED,
        )

    def test_login_get_includes_hidden_next_input(self):
        """GET /accounts/login/?next=/keys/ renders hidden input with next value."""
        response = self.client.get(reverse("accounts:login"), {"next": "/keys/"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<input type="hidden" name="next" value="/keys/">',
            html=True,
        )

    def test_login_get_without_next_renders_empty_hidden_input(self):
        """GET /accounts/login/ renders hidden input with empty value."""
        response = self.client.get(reverse("accounts:login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<input type="hidden" name="next" value="">',
            html=True,
        )

    def test_login_get_disallows_external_next(self):
        """GET /accounts/login/?next=https://evil.example.com clears unsafe next."""
        response = self.client.get(
            reverse("accounts:login"),
            {"next": "https://evil.example.com"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<input type="hidden" name="next" value="">',
            html=True,
        )
        self.assertNotContains(response, "evil.example.com")

    def test_login_post_redirects_to_next_from_form_field(self):
        """POST /accounts/login/ with hidden input 'next' honors redirect destination."""
        response = self.client.post(
            reverse("accounts:login"),
            {
                "username": "guard_test",
                "password": "password123",
                "next": "/keys/",
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/keys/")

    def test_login_post_blocks_external_redirect_in_next(self):
        """POST with unsafe next falls back to default post-login redirect."""
        response = self.client.post(
            reverse("accounts:login"),
            {
                "username": "guard_test",
                "password": "password123",
                "next": "https://evil.example.com/phishing",
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)
        # Should redirect to guard default (/transactions/borrow/), NOT external url
        self.assertEqual(response.url, "/transactions/borrow/")

    def test_login_post_blocks_scheme_relative_redirect_in_next(self):
        """POST with scheme-relative //evil.example.com falls back to default redirect."""
        response = self.client.post(
            reverse("accounts:login"),
            {
                "username": "admin_test",
                "password": "password123",
                "next": "//evil.example.com/phishing",
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)
        # Should redirect to admin default (/), NOT evil.example.com
        self.assertEqual(response.url, "/")

    def test_login_post_failed_auth_preserves_valid_next(self):
        """Failed POST credentials preserves the valid next parameter in re-rendered form."""
        response = self.client.post(
            reverse("accounts:login"),
            {
                "username": "guard_test",
                "password": "wrongpassword",
                "next": "/keys/",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<input type="hidden" name="next" value="/keys/">',
            html=True,
        )
        self.assertContains(response, "Invalid credentials")

    def test_login_post_failed_auth_clears_unsafe_next(self):
        """Failed POST credentials clears unsafe next parameter in re-rendered form."""
        response = self.client.post(
            reverse("accounts:login"),
            {
                "username": "guard_test",
                "password": "wrongpassword",
                "next": "https://evil.example.com",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<input type="hidden" name="next" value="">',
            html=True,
        )
        self.assertNotContains(response, "evil.example.com")



class SidebarRoleSplitTests(TestCase):
    """TASK F2.1 — Split the sidebar by role (Admin vs Guard)."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin_jane",
            first_name="Jane",
            last_name="Doe",
            email="jane.admin@example.com",
            password="adminpassword123",
            role=User.Role.ADMIN,
            approval_status=User.Approval.APPROVED,
        )
        self.guard = User.objects.create_user(
            username="guard_bob",
            first_name="Bob",
            last_name="Smith",
            email="bob.guard@example.com",
            password="guardpassword123",
            role=User.Role.GUARD,
            approval_status=User.Approval.APPROVED,
        )
        self.superuser = User.objects.create_superuser(
            username="super_root",
            email="root@example.com",
            password="rootpassword123",
            approval_status=User.Approval.APPROVED,
        )

    def test_admin_sidebar_full_menu_rendered(self):
        """Admin sees full menu: Workspace, Scheduling, Inventory, People, Oversight."""
        self.client.force_login(self.admin)
        res = self.client.get(reverse("dashboard"))
        self.assertEqual(res.status_code, 200)

        # Admin Section Headers
        self.assertContains(res, "Workspace")
        self.assertContains(res, "Scheduling")
        self.assertContains(res, "Inventory")
        self.assertContains(res, "People")
        self.assertContains(res, "Oversight")

        # Admin Navigation Links
        self.assertContains(res, reverse("dashboard"))
        self.assertContains(res, reverse("scheduling:room_timetable"))
        self.assertContains(res, "Room Schedule")
        self.assertContains(res, reverse("scheduling:free_rooms"))
        self.assertContains(res, "Room Finder")
        self.assertContains(res, reverse("scheduling:conflicts"))
        self.assertContains(res, "Conflicts")

        self.assertContains(res, reverse("keys:key_list"))
        self.assertContains(res, "Key Inventory")
        self.assertContains(res, reverse("keys:category_list"))
        self.assertContains(res, "Key Categories")
        self.assertContains(res, reverse("facilities:building_list"))
        self.assertContains(res, "Buildings")
        self.assertContains(res, reverse("facilities:room_list"))
        self.assertContains(res, "Rooms")

        self.assertContains(res, reverse("accounts:borrower_list"))
        self.assertContains(res, "Borrowers")
        self.assertContains(res, reverse("accounts:user_list"))
        self.assertContains(res, "Users &amp; Guards")
        self.assertContains(res, reverse("accounts:pending_user_list"))
        self.assertContains(res, "Pending Approvals")

        self.assertContains(res, reverse("transactions:history"))
        self.assertContains(res, "Transaction History")
        self.assertContains(res, reverse("transactions:shift_history"))
        self.assertContains(res, "Guard Shifts")
        self.assertContains(res, reverse("audit:log_list"))
        self.assertContains(res, "Audit Log")
        self.assertContains(res, reverse("transactions:reports"))
        self.assertContains(res, "Reports")

        # Guard-specific menu items MUST NOT appear for Admin
        self.assertNotContains(res, "Key Transactions (POS)")
        self.assertNotContains(res, "Key Availability (read-only)")
        self.assertNotContains(res, "Borrower Lookup (read-only)")
        self.assertNotContains(res, "My Shift Log")
        self.assertNotContains(res, "Today's Transactions")

    def test_guard_sidebar_minimal_menu_rendered(self):
        """Guard sees deliberately minimal menu: POS, read-only lookups, shift log, today's txns."""
        self.client.force_login(self.guard)
        res = self.client.get(reverse("transactions:borrow_dashboard"))
        self.assertEqual(res.status_code, 200)

        # Guard Section Headers
        self.assertContains(res, "Workspace")
        self.assertContains(res, "Lookup")
        self.assertContains(res, "My Records")

        # Guard Navigation Links
        self.assertContains(res, reverse("transactions:borrow_dashboard"))
        self.assertContains(res, "Key Transactions (POS)")
        self.assertContains(res, reverse("keys:key_availability"))
        self.assertContains(res, "Key Availability (read-only)")
        self.assertContains(res, reverse("accounts:borrower_lookup"))
        self.assertContains(res, "Borrower Lookup (read-only)")
        self.assertContains(res, reverse("transactions:shift_history"))
        self.assertContains(res, "My Shift Log")
        self.assertContains(res, reverse("transactions:history") + "?today=1")
        self.assertContains(res, "Today's Transactions")

        # Admin-only items MUST NOT appear for Guard
        self.assertNotContains(res, reverse("scheduling:room_timetable"))
        self.assertNotContains(res, "Room Schedule")
        self.assertNotContains(res, reverse("scheduling:conflicts"))
        self.assertNotContains(res, "Conflicts")
        self.assertNotContains(res, reverse("keys:category_list"))
        self.assertNotContains(res, "Key Categories")
        self.assertNotContains(res, reverse("facilities:building_list"))
        self.assertNotContains(res, "Buildings")
        self.assertNotContains(res, reverse("facilities:room_list"))
        self.assertNotContains(res, "Rooms")
        self.assertNotContains(res, reverse("accounts:user_list"))
        self.assertNotContains(res, "Users &amp; Guards")
        self.assertNotContains(res, reverse("accounts:pending_user_list"))
        self.assertNotContains(res, "Pending Approvals")
        self.assertNotContains(res, reverse("audit:log_list"))
        self.assertNotContains(res, "Audit Log")
        self.assertNotContains(res, reverse("transactions:reports"))
        self.assertNotContains(res, "Reports")

    def test_superuser_sees_admin_sidebar(self):
        """Superuser gets full admin sidebar."""
        self.client.force_login(self.superuser)
        res = self.client.get(reverse("dashboard"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Audit Log")
        self.assertContains(res, "Key Inventory")
        self.assertNotContains(res, "Key Transactions (POS)")

    def test_pending_approvals_count_badge_display(self):
        """Pending approvals count badge shows for admin when count > 0, hidden when 0."""
        self.client.force_login(self.admin)

        # When 0 pending users
        res = self.client.get(reverse("dashboard"))
        self.assertEqual(res.context["pending_approvals_count"], 0)
        self.assertNotContains(res, 'title="0 pending approval"')

        # Create a pending user
        User.objects.create_user(
            username="pending_applicant",
            email="pending@example.com",
            password="secretpassword",
            role=User.Role.GUARD,
            approval_status=User.Approval.PENDING,
        )

        res = self.client.get(reverse("dashboard"))
        self.assertEqual(res.context["pending_approvals_count"], 1)
        self.assertContains(res, "1 pending approval")

    def test_user_chip_role_display_and_initials(self):
        """User chip uses user.get_role_display (not 'GUARD' fallback) and proper initials."""
        # Admin test
        self.client.force_login(self.admin)
        res = self.client.get(reverse("dashboard"))
        self.assertContains(res, "Jane Doe")
        self.assertContains(res, "JD")  # initials for Jane Doe
        self.assertContains(res, "Admin")

        # Guard test
        self.client.force_login(self.guard)
        res = self.client.get(reverse("transactions:borrow_dashboard"))
        self.assertContains(res, "Bob Smith")
        self.assertContains(res, "BS")  # initials for Bob Smith
        self.assertContains(res, "Guard")

    def test_active_link_styling_for_admin_and_guard(self):
        """Active link receives bg-white text-[#0F2E1F] font-semibold styling."""
        # Admin on Dashboard
        self.client.force_login(self.admin)
        res = self.client.get(reverse("dashboard"))
        # Dashboard is active
        self.assertContains(
            res,
            'href="/" class="flex items-center gap-2 px-3 py-2 rounded-lg mb-0.5 bg-white text-[#0F2E1F] font-semibold"',
        )
        # Key Inventory is inactive
        self.assertContains(
            res,
            'href="/keys/" class="flex items-center gap-2 px-3 py-2 rounded-lg mb-0.5 text-white/80 hover:bg-white/10"',
        )

        # Guard on POS
        self.client.force_login(self.guard)
        res = self.client.get(reverse("transactions:borrow_dashboard"))
        self.assertContains(
            res,
            'href="/transactions/borrow/" class="flex items-center gap-2 px-3 py-2 rounded-lg mb-0.5 bg-white text-[#0F2E1F] font-semibold"',
        )
        # Shift log is inactive
        self.assertContains(
            res,
            'href="/transactions/shifts/" class="flex items-center gap-2 px-3 py-2 rounded-lg mb-0.5 text-white/80 hover:bg-white/10"',
        )

    def test_new_endpoints_permission_gating(self):
        """Verify role protection on newly wired endpoints."""
        # Admin views
        self.client.force_login(self.guard)
        self.assertEqual(self.client.get(reverse("accounts:user_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("audit:log_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("transactions:reports")).status_code, 403)

        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("accounts:user_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("audit:log_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("transactions:reports")).status_code, 200)

        # Guard read-only lookups: accessible by guard and admin
        self.client.force_login(self.guard)
        self.assertEqual(self.client.get(reverse("keys:key_availability")).status_code, 200)
        self.assertEqual(self.client.get(reverse("accounts:borrower_lookup")).status_code, 200)

        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("keys:key_availability")).status_code, 200)
        self.assertEqual(self.client.get(reverse("accounts:borrower_lookup")).status_code, 200)
