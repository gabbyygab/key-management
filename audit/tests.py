import datetime

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from audit.models import ActivityLog


class AuditLogListTests(TestCase):
    """Admin-only, read-only audit log viewer with filters and pagination."""

    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            username="admin_audit", email="a@example.com", password="pass1234"
        )
        self.guard = User.objects.create_user(
            username="guard_audit", email="g@example.com", password="pass1234",
            role=User.Role.GUARD,
            approval_status=User.Approval.APPROVED,
        )

        now = timezone.now()
        self.log_borrow = ActivityLog.objects.create(
            user=self.admin,
            action=ActivityLog.Action.BORROW,
            model_name="Key",
            object_id="1",
            object_repr="RM303 master key",
            description="Released RM303 master key",
        )
        self.log_return = ActivityLog.objects.create(
            user=self.guard,
            action=ActivityLog.Action.RETURN,
            model_name="Key",
            object_id="1",
            object_repr="RM303 master key",
            description="Returned RM303 master key",
        )
        self.log_create = ActivityLog.objects.create(
            user=self.admin,
            action=ActivityLog.Action.CREATE,
            model_name="Borrower",
            object_id="9",
            object_repr="Jane Doe",
            description="Created borrower Jane Doe",
        )
        self.log_login = ActivityLog.objects.create(
            user=None,
            action=ActivityLog.Action.LOGIN,
            model_name="User",
            object_id="1",
            object_repr="admin_audit",
            description="System login",
        )
        # auto_now_add ignores created_at passed to create(), so back-date via update().
        ActivityLog.objects.filter(pk=self.log_borrow.pk).update(
            created_at=now - datetime.timedelta(days=2)
        )
        ActivityLog.objects.filter(pk=self.log_return.pk).update(
            created_at=now - datetime.timedelta(days=1)
        )
        ActivityLog.objects.filter(pk=self.log_create.pk).update(created_at=now)
        ActivityLog.objects.filter(pk=self.log_login.pk).update(
            created_at=now - datetime.timedelta(hours=3)
        )

    def test_admin_can_view_log_list(self):
        self.client.force_login(self.admin)
        res = self.client.get(reverse("audit:log_list"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "System Audit Log")
        self.assertContains(res, "RM303 master key")
        self.assertContains(res, "Borrow")
        self.assertContains(res, "Return")

    def test_guard_is_denied(self):
        self.client.force_login(self.guard)
        res = self.client.get(reverse("audit:log_list"))
        # Authenticated non-admin raises PermissionDenied -> 403.
        self.assertEqual(res.status_code, 403)

    def test_anonymous_is_denied(self):
        # login_required redirects unauthenticated users to login -> 302.
        res = self.client.get(reverse("audit:log_list"))
        self.assertEqual(res.status_code, 302)

    def test_filter_by_action(self):
        self.client.force_login(self.admin)
        res = self.client.get(reverse("audit:log_list") + "?action=BORROW")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Borrow")
        # Only the borrow row renders; the return log row content must not appear.
        self.assertNotContains(res, "Returned")

    def test_filter_by_user(self):
        self.client.force_login(self.admin)
        res = self.client.get(reverse("audit:log_list") + f"?user={self.admin.pk}")
        self.assertEqual(res.status_code, 200)
        # admin's borrow + create logs appear, guard's return does not
        self.assertContains(res, "Released RM303 master key")
        self.assertContains(res, "Created borrower Jane Doe")
        self.assertNotContains(res, "Returned RM303 master key")

    def test_filter_by_model(self):
        self.client.force_login(self.admin)
        res = self.client.get(reverse("audit:log_list") + "?model=Borrower")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Created borrower Jane Doe")
        self.assertNotContains(res, "RM303 master key")

    def test_filter_by_date_range(self):
        self.client.force_login(self.admin)
        yesterday = (timezone.localdate() - datetime.timedelta(days=1)).isoformat()
        today = timezone.localdate().isoformat()
        res = self.client.get(
            reverse("audit:log_list") + f"?start_date={yesterday}&end_date={today}"
        )
        self.assertEqual(res.status_code, 200)
        # The create log (today) and return/login logs (yesterday+ within range)
        # appear; the borrow log (2 days ago) does not.
        self.assertContains(res, "Created borrower Jane Doe")
        self.assertContains(res, "Returned RM303 master key")
        self.assertContains(res, "System login")
        self.assertNotContains(res, "Released RM303 master key")

    def test_free_text_search(self):
        self.client.force_login(self.admin)
        res = self.client.get(reverse("audit:log_list") + "?q=master")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "RM303 master key")
        self.assertNotContains(res, "Jane Doe")

    def test_pagination_present(self):
        self.client.force_login(self.admin)
        res = self.client.get(reverse("audit:log_list"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Showing")
        self.assertContains(res, "of")
        self.assertContains(res, "results")

    def test_action_icons_and_colours_rendered(self):
        self.client.force_login(self.admin)
        res = self.client.get(reverse("audit:log_list"))
        self.assertEqual(res.status_code, 200)
        # Borrow uses blue pill with key icon; Return uses green pill with check-circle icon.
        self.assertContains(res, "text-blue-800")
        self.assertContains(res, "text-green-800")
        self.assertContains(res, 'data-lucide="key"')
        self.assertContains(res, 'data-lucide="check-circle"')

    def test_system_log_entry_shows_system_label(self):
        self.client.force_login(self.admin)
        res = self.client.get(reverse("audit:log_list"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "System")
