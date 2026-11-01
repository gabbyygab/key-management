"""B4.6: admin-only transaction corrections, audited with a reason."""
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Borrower, User
from audit.models import ActivityLog
from facilities.models import Building, Room
from keys.models import Key, KeyCategory

from .models import BorrowTransaction, GuardShift
from .services import pos_borrow


@override_settings(ALLOWED_HOSTS=["testserver"])
class TransactionCorrectionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin", email="a@example.com", password="pw-Strong-12345",
            role=User.Role.ADMIN, approval_status=User.Approval.APPROVED)
        self.guard = User.objects.create_user(
            username="guard", email="g@example.com", password="pw-Strong-12345",
            role=User.Role.GUARD, approval_status=User.Approval.APPROVED)
        GuardShift.objects.create(guard=self.guard, post="Main Desk")
        room = Room.objects.create(building=Building.objects.create(code="B", name="B"),
                                   room_code="1")
        key = Key.objects.create(key_code="B-1", room=room,
                                 category=KeyCategory.objects.create(name="Room"))
        borrower = Borrower.objects.create(id_number="F-1", first_name="A", last_name="B",
                                           borrower_type="FACULTY")
        pos_borrow(key_id=key.pk, borrower=borrower, guard=self.guard, purpose="Class")
        self.txn = BorrowTransaction.objects.get()
        self.url = reverse("transactions:correct", args=[self.txn.pk])

    def test_guard_has_no_edit_path(self):
        self.client.force_login(self.guard)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {"purpose": "x", "reason": "x"}).status_code, 403)
        self.assertNotContains(self.client.get(reverse("transactions:history")), self.url)

    def test_reason_is_required(self):
        self.client.force_login(self.admin)
        res = self.client.post(self.url + "?modal=1", {"purpose": "Lab", "remarks": ""})
        self.assertEqual(res.status_code, 422)
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.purpose, "Class")

    def test_correction_saves_and_audits_diff_with_reason(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("transactions:history")), self.url)
        self.assertContains(self.client.get(self.url + "?modal=1"), "Reason for correction")
        res = self.client.post(self.url + "?modal=1", {
            "purpose": "Laboratory", "remarks": "", "reason": "Guard typo"})
        self.assertEqual(res.status_code, 200, res.content)
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.purpose, "Laboratory")
        log = ActivityLog.objects.filter(action=ActivityLog.Action.UPDATE,
                                         description__startswith="Correction:").get()
        self.assertEqual(log.user, self.admin)
        self.assertIn("Guard typo", log.description)
        self.assertEqual(log.changes["purpose"], ["Class", "Laboratory"])
        self.assertNotIn("reason", log.changes)
