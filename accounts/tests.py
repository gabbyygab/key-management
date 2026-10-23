from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .context_processors import pending_approvals
from .models import Department
from .services import ApprovalError, approve_user, pending_count, reject_user

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
