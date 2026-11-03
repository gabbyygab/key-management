"""B4.6: admin-only transaction corrections, audited with a reason."""
import datetime

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Borrower, User
from audit.models import ActivityLog
from facilities.models import Building, Room
from keys.models import Key, KeyCategory

from .models import BorrowTransaction, GuardShift
from .services import BorrowError, admin_set_status, pos_borrow


class TxnFixture:
    """One ACTIVE transaction, its key, its borrower, an admin and a guard."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin", email="a@example.com", password="pw-Strong-12345",
            role=User.Role.ADMIN)
        self.guard = User.objects.create_user(
            username="guard", email="g@example.com", password="pw-Strong-12345",
            role=User.Role.GUARD)
        GuardShift.objects.create(guard=self.guard, post="Main Desk")
        room = Room.objects.create(building=Building.objects.create(code="B", name="B"),
                                   room_code="1")
        key = Key.objects.create(key_code="B-1", room=room,
                                 category=KeyCategory.objects.create(name="Room"))
        borrower = Borrower.objects.create(id_number="F-1", first_name="A", last_name="B",
                                           borrower_type="FACULTY")
        self.key = key
        self.room = room
        self.borrower = borrower
        pos_borrow(key_id=key.pk, borrower=borrower, guard=self.guard, purpose="Class")
        self.txn = BorrowTransaction.objects.get()
        self.url = reverse("transactions:correct", args=[self.txn.pk])
        self.detail_url = reverse("transactions:detail", args=[self.txn.pk])


@override_settings(ALLOWED_HOSTS=["testserver"])
class TransactionCorrectionTests(TxnFixture, TestCase):
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

    def test_status_left_blank_changes_nothing(self):
        self.client.force_login(self.admin)
        res = self.client.post(self.url + "?modal=1", {
            "purpose": "Class", "remarks": "", "status": "", "reason": "No status move"})
        self.assertEqual(res.status_code, 200, res.content)
        self.txn.refresh_from_db()
        self.key.refresh_from_db()
        self.assertEqual(self.txn.status, BorrowTransaction.Status.ACTIVE)
        self.assertEqual(self.key.status, Key.Status.BORROWED)

    def test_admin_closes_record_and_key_goes_back_on_the_board(self):
        self.client.force_login(self.admin)
        res = self.client.post(self.url + "?modal=1", {
            "purpose": "Class", "remarks": "", "status": "RETURNED",
            "reason": "Key handed back off-system"})
        self.assertEqual(res.status_code, 200, res.content)
        self.txn.refresh_from_db()
        self.key.refresh_from_db()
        self.assertEqual(self.txn.status, BorrowTransaction.Status.RETURNED)
        self.assertEqual(self.key.status, Key.Status.AVAILABLE)
        self.assertTrue(self.txn.return_confirmed)
        self.assertIsNotNone(self.txn.returned_at)
        self.assertEqual(self.txn.received_by, self.admin)
        log = ActivityLog.objects.filter(action=ActivityLog.Action.UPDATE,
                                         description__startswith="Correction:").get()
        self.assertEqual(log.changes["status"], ["ACTIVE", "RETURNED"])
        self.assertEqual(log.changes["key_status"], ["BORROWED", "AVAILABLE"])

    def test_marking_lost_writes_the_key_off_without_a_receipt(self):
        self.client.force_login(self.admin)
        res = self.client.post(self.url + "?modal=1", {
            "purpose": "Class", "remarks": "", "status": "LOST",
            "reason": "Borrower reported it missing"})
        self.assertEqual(res.status_code, 200, res.content)
        self.txn.refresh_from_db()
        self.key.refresh_from_db()
        self.assertEqual(self.txn.status, BorrowTransaction.Status.LOST)
        self.assertEqual(self.key.status, Key.Status.LOST)
        self.assertIsNone(self.txn.returned_at)
        self.assertFalse(self.txn.return_confirmed)

    def test_reopening_is_refused_while_the_key_is_out_elsewhere(self):
        """And the refusal rolls the other edits back with it (422, modal stays)."""
        admin_set_status(transaction_id=self.txn.pk, new_status="RETURNED", admin=self.admin)
        other = Borrower.objects.create(id_number="F-2", first_name="C", last_name="D",
                                        borrower_type="FACULTY")
        pos_borrow(key_id=self.key.pk, borrower=other, guard=self.guard)

        self.client.force_login(self.admin)
        res = self.client.post(self.url + "?modal=1", {
            "purpose": "Changed too", "remarks": "", "status": "ACTIVE",
            "reason": "Re-open"})
        self.assertEqual(res.status_code, 422)
        self.assertContains(res, "already out on", status_code=422)
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, BorrowTransaction.Status.RETURNED)
        self.assertEqual(self.txn.purpose, "Class")

    def test_reopening_a_closed_record_puts_the_key_back_out(self):
        admin_set_status(transaction_id=self.txn.pk, new_status="RETURNED", admin=self.admin)
        self.client.force_login(self.admin)
        res = self.client.post(self.url + "?modal=1", {
            "purpose": "Class", "remarks": "", "status": "ACTIVE",
            "reason": "Closed by mistake"})
        self.assertEqual(res.status_code, 200, res.content)
        self.txn.refresh_from_db()
        self.key.refresh_from_db()
        self.assertEqual(self.txn.status, BorrowTransaction.Status.ACTIVE)
        self.assertEqual(self.key.status, Key.Status.BORROWED)
        self.assertIsNone(self.txn.returned_at)
        self.assertIsNone(self.txn.received_by)
        self.assertFalse(self.txn.return_confirmed)

    def test_reopening_is_refused_when_the_borrower_already_holds_a_key(self):
        admin_set_status(transaction_id=self.txn.pk, new_status="RETURNED", admin=self.admin)
        other_key = Key.objects.create(key_code="B-2", room=self.room,
                                       category=self.key.category, copy_number=2)
        pos_borrow(key_id=other_key.pk, borrower=self.borrower, guard=self.guard)
        with self.assertRaises(BorrowError):
            admin_set_status(transaction_id=self.txn.pk, new_status="ACTIVE",
                             admin=self.admin)
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, BorrowTransaction.Status.RETURNED)


@override_settings(ALLOWED_HOSTS=["testserver"])
class TransactionDetailTests(TxnFixture, TestCase):
    """The read-only record behind a history row."""

    def test_detail_modal_renders_the_record_and_its_audit_trail(self):
        self.client.force_login(self.admin)
        res = self.client.get(self.detail_url + "?modal=1")
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, "transactions/_detail_modal.html")
        self.assertContains(res, self.txn.transaction_no)
        self.assertContains(res, "B-1")
        self.assertContains(res, "Audit trail")
        self.assertContains(res, "Released B-1 to A B")
        # The admin's way into the editor.
        self.assertContains(res, reverse("transactions:correct", args=[self.txn.pk]))

    def test_detail_page_is_the_no_js_fallback(self):
        self.client.force_login(self.admin)
        res = self.client.get(self.detail_url)
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, "transactions/detail.html")
        self.assertContains(res, self.txn.transaction_no)

    def test_guard_may_read_a_record_but_sees_no_editor(self):
        self.client.force_login(self.guard)
        res = self.client.get(self.detail_url + "?modal=1")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, self.txn.transaction_no)
        self.assertNotContains(res, reverse("transactions:correct", args=[self.txn.pk]))

    def test_a_closed_record_is_never_flagged_overdue(self):
        """expected_return_at stays in the past once a key is back, so the
        overdue banner has to key off the status, not the clock."""
        self.txn.expected_return_at = timezone.now() - datetime.timedelta(hours=2)
        self.txn.save(update_fields=["expected_return_at"])
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.detail_url + "?modal=1"),
                            "Key is overdue")
        admin_set_status(transaction_id=self.txn.pk, new_status="RETURNED",
                         admin=self.admin)
        self.assertNotContains(self.client.get(self.detail_url + "?modal=1"),
                               "Key is overdue")

    def test_unknown_transaction_is_404(self):
        self.client.force_login(self.admin)
        res = self.client.get(reverse("transactions:detail", args=[self.txn.pk + 999]))
        self.assertEqual(res.status_code, 404)
