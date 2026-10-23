"""Tests for the Currently Out panel's backend half (TASK F3.7).

Covers the new service rules (return condition flag, due-state buckets, the
active-list query) and the two JSON endpoints the panel talks to.

ALLOWED_HOSTS has no "testserver" yet (backend B8.5), so every test class
overrides it; drop the override once that task lands.
"""
import json
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Borrower, Department, User
from facilities.models import Building, Room
from keys.models import Key, KeyCategory

from .models import BorrowTransaction, GuardShift
from .services import (
    BorrowError, active_transactions, borrow_key, due_state,
    return_key_with_condition,
)

TESTSERVER = override_settings(ALLOWED_HOSTS=["testserver"])


class OutKeyFixture(TestCase):
    """One guard on shift, one borrower, three keys in one room."""

    def setUp(self):
        self.dept = Department.objects.create(code="CCS", name="Computer Studies")
        self.guard = User.objects.create_user(
            username="guard1", email="guard1@example.edu", password="pw",
            role=User.Role.GUARD, approval_status=User.Approval.APPROVED)
        self.shift = GuardShift.objects.create(guard=self.guard, post="Main Desk")
        self.borrower = Borrower.objects.create(
            id_number="F-1001", first_name="Maria", last_name="Dela Cruz",
            borrower_type=Borrower.BorrowerType.FACULTY, department=self.dept)
        building = Building.objects.create(code="ENG", name="Engineering")
        self.room = Room.objects.create(building=building, room_code="303", name="Lecture Hall")
        self.category = KeyCategory.objects.create(name="Classroom")
        self.keys = [
            Key.objects.create(key_code=f"ENG-303-{n}", room=self.room,
                               category=self.category, copy_number=n)
            for n in (1, 2, 3)
        ]

    def out(self, key=None, borrower=None):
        """Release a key, bypassing the one-key-per-borrower rule for fixtures."""
        if borrower is None:
            borrower = Borrower.objects.create(
                id_number=f"F-200{BorrowTransaction.objects.count()}",
                first_name="Other", last_name=f"Borrower{BorrowTransaction.objects.count()}",
                borrower_type=Borrower.BorrowerType.STAFF, department=self.dept)
        return borrow_key(key_id=(key or self.keys[0]).pk, borrower=borrower,
                          guard=self.guard, purpose="Lecture")


class ReturnConditionTests(OutKeyFixture):
    def test_ok_returns_key_to_circulation(self):
        txn = self.out(borrower=self.borrower)
        done = return_key_with_condition(
            transaction_id=txn.pk, guard=self.guard, remarks="all good", condition="OK")
        done.refresh_from_db()
        self.assertEqual(done.status, BorrowTransaction.Status.RETURNED)
        self.assertEqual(done.remarks, "all good")
        self.assertTrue(done.return_confirmed)
        self.assertEqual(Key.objects.get(pk=txn.key_id).status, Key.Status.AVAILABLE)

    def test_damaged_holds_the_key_out_of_circulation(self):
        txn = self.out(borrower=self.borrower)
        done = return_key_with_condition(
            transaction_id=txn.pk, guard=self.guard, condition="DAMAGED")
        self.assertEqual(done.status, BorrowTransaction.Status.DAMAGED)
        self.assertEqual(Key.objects.get(pk=txn.key_id).status, Key.Status.DAMAGED)

    def test_lost_marks_transaction_and_key_lost(self):
        txn = self.out(borrower=self.borrower)
        done = return_key_with_condition(
            transaction_id=txn.pk, guard=self.guard, condition="LOST")
        self.assertEqual(done.status, BorrowTransaction.Status.LOST)
        self.assertEqual(Key.objects.get(pk=txn.key_id).status, Key.Status.LOST)

    def test_condition_is_case_insensitive_and_defaults_to_ok(self):
        txn = self.out(borrower=self.borrower)
        done = return_key_with_condition(
            transaction_id=txn.pk, guard=self.guard, condition="damaged")
        self.assertEqual(done.status, BorrowTransaction.Status.DAMAGED)

    def test_unknown_condition_leaves_the_transaction_open(self):
        txn = self.out(borrower=self.borrower)
        with self.assertRaises(BorrowError):
            return_key_with_condition(
                transaction_id=txn.pk, guard=self.guard, condition="MELTED")
        txn.refresh_from_db()
        self.assertEqual(txn.status, BorrowTransaction.Status.ACTIVE)
        self.assertEqual(Key.objects.get(pk=txn.key_id).status, Key.Status.BORROWED)

    def test_an_overdue_key_can_still_be_received(self):
        """The red rows in the panel must be returnable, not stuck."""
        txn = self.out(borrower=self.borrower)
        BorrowTransaction.objects.filter(pk=txn.pk).update(
            status=BorrowTransaction.Status.OVERDUE)
        done = return_key_with_condition(transaction_id=txn.pk, guard=self.guard)
        self.assertEqual(done.status, BorrowTransaction.Status.RETURNED)
        self.assertEqual(Key.objects.get(pk=txn.key_id).status, Key.Status.AVAILABLE)

    def test_overdue_key_can_be_received_as_lost(self):
        txn = self.out(borrower=self.borrower)
        BorrowTransaction.objects.filter(pk=txn.pk).update(
            status=BorrowTransaction.Status.OVERDUE)
        done = return_key_with_condition(
            transaction_id=txn.pk, guard=self.guard, condition="LOST")
        self.assertEqual(done.status, BorrowTransaction.Status.LOST)
        self.assertEqual(Key.objects.get(pk=txn.key_id).status, Key.Status.LOST)

    def test_a_returned_row_is_not_reopened_by_the_overdue_clear(self):
        txn = self.out(borrower=self.borrower)
        return_key_with_condition(transaction_id=txn.pk, guard=self.guard)
        for status in (BorrowTransaction.Status.RETURNED,
                       BorrowTransaction.Status.LOST,
                       BorrowTransaction.Status.DAMAGED):
            with self.subTest(status=status):
                BorrowTransaction.objects.filter(pk=txn.pk).update(status=status)
                with self.assertRaises(BorrowError):
                    return_key_with_condition(transaction_id=txn.pk, guard=self.guard)

    def test_second_return_is_rejected(self):
        txn = self.out(borrower=self.borrower)
        return_key_with_condition(transaction_id=txn.pk, guard=self.guard)
        with self.assertRaises(BorrowError):
            return_key_with_condition(transaction_id=txn.pk, guard=self.guard)

    def test_guard_without_a_shift_cannot_receive_a_key(self):
        txn = self.out(borrower=self.borrower)
        GuardShift.objects.filter(pk=self.shift.pk).update(is_on_duty=False)
        with self.assertRaises(BorrowError):
            return_key_with_condition(transaction_id=txn.pk, guard=self.guard)


class DueStateTests(OutKeyFixture):
    def test_no_expected_return_is_ok(self):
        txn = self.out(borrower=self.borrower)
        self.assertEqual(due_state(txn), "ok")

    def test_overdue_status_wins_even_without_a_due_time(self):
        txn = self.out(borrower=self.borrower)
        txn.status = BorrowTransaction.Status.OVERDUE
        self.assertEqual(due_state(txn), "overdue")

    def test_past_due_time_is_overdue(self):
        txn = self.out(borrower=self.borrower)
        txn.expected_return_at = timezone.now() - timedelta(minutes=1)
        self.assertEqual(due_state(txn), "overdue")

    def test_within_thirty_minutes_is_due_soon(self):
        txn = self.out(borrower=self.borrower)
        txn.expected_return_at = timezone.now() + timedelta(minutes=20)
        self.assertEqual(due_state(txn), "due_soon")

    def test_further_out_is_ok(self):
        txn = self.out(borrower=self.borrower)
        txn.expected_return_at = timezone.now() + timedelta(hours=3)
        self.assertEqual(due_state(txn), "ok")


class ActiveQueryTests(OutKeyFixture):
    def test_includes_active_and_overdue_but_not_returned(self):
        active = self.out(key=self.keys[0], borrower=self.borrower)
        overdue = self.out(key=self.keys[1])
        BorrowTransaction.objects.filter(pk=overdue.pk).update(
            status=BorrowTransaction.Status.OVERDUE)
        closed = self.out(key=self.keys[2])
        return_key_with_condition(transaction_id=closed.pk, guard=self.guard)

        ids = set(active_transactions().values_list("pk", flat=True))
        self.assertEqual(ids, {active.pk, overdue.pk})

    def test_search_matches_key_room_borrower_and_id_number(self):
        txn = self.out(key=self.keys[0], borrower=self.borrower)
        for term in ["eng-303-1", "303", "ENG", "dela", "F-1001", txn.transaction_no]:
            with self.subTest(term=term):
                self.assertEqual(
                    list(active_transactions(q=term).values_list("pk", flat=True)), [txn.pk])

    def test_search_excludes_non_matches(self):
        self.out(key=self.keys[0], borrower=self.borrower)
        self.assertEqual(active_transactions(q="zzz").count(), 0)


@TESTSERVER
class ActiveListEndpointTests(OutKeyFixture):
    def setUp(self):
        super().setUp()
        self.url = reverse("transactions_api:active")
        self.client.force_login(self.guard)

    def test_payload_shape_and_counters(self):
        txn = self.out(key=self.keys[0], borrower=self.borrower)
        soon = self.out(key=self.keys[1])
        BorrowTransaction.objects.filter(pk=soon.pk).update(
            expected_return_at=timezone.now() + timedelta(minutes=10))
        late = self.out(key=self.keys[2])
        BorrowTransaction.objects.filter(pk=late.pk).update(
            status=BorrowTransaction.Status.OVERDUE)

        body = self.client.get(self.url).json()
        self.assertEqual(body["count"], 3)
        self.assertEqual(body["overdue_count"], 1)
        self.assertEqual(body["due_soon_count"], 1)
        self.assertEqual(body["matched"], 3)
        self.assertIn("server_time", body)

        row = next(r for r in body["results"] if r["id"] == txn.pk)
        self.assertEqual(row["key_code"], "ENG-303-1")
        self.assertEqual(row["room"], "ENG-303")
        self.assertEqual(row["borrower"]["id_number"], "F-1001")
        self.assertEqual(row["borrower"]["sort_name"], "Dela Cruz, Maria")
        self.assertEqual(row["due_state"], "ok")
        self.assertGreaterEqual(row["elapsed_seconds"], 0)

    def test_search_filters_rows_but_not_counters(self):
        self.out(key=self.keys[0], borrower=self.borrower)
        self.out(key=self.keys[1])
        body = self.client.get(self.url, {"q": "F-1001"}).json()
        self.assertEqual(body["matched"], 1)
        self.assertEqual(body["count"], 2)

    def test_payload_never_leaks_a_user_email(self):
        self.out(key=self.keys[0], borrower=self.borrower)
        raw = self.client.get(self.url).content.decode()
        self.assertNotIn(self.guard.email, raw)
        self.assertNotIn("password", raw)

    def test_wrong_role_gets_403_json_not_a_redirect(self):
        staff = User.objects.create_user(
            username="prof", email="prof@example.edu", password="pw",
            role=User.Role.FACULTY_STAFF, approval_status=User.Approval.APPROVED)
        self.client.force_login(staff)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 403)
        self.assertIn("error", res.json())

    def test_anonymous_gets_403_json_not_a_login_redirect(self):
        self.client.logout()
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 403)
        self.assertIn("error", res.json())


@TESTSERVER
class ReturnEndpointTests(OutKeyFixture):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.guard)
        self.txn = self.out(key=self.keys[0], borrower=self.borrower)
        self.url = reverse("transactions_api:return", args=[self.txn.pk])

    def post(self, payload, url=None):
        return self.client.post(url or self.url, data=json.dumps(payload),
                                content_type="application/json")

    def test_return_ok(self):
        res = self.post({"remarks": "on time", "condition": "OK"})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["transaction_no"], self.txn.transaction_no)
        self.assertEqual(body["key_status"], Key.Status.AVAILABLE)
        self.assertEqual(body["condition"], "OK")
        self.assertEqual(body["counts"], {"count": 0, "overdue_count": 0, "due_soon_count": 0})
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, BorrowTransaction.Status.RETURNED)
        self.assertEqual(self.txn.remarks, "on time")

    def test_return_lost(self):
        res = self.post({"condition": "LOST"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["key_status"], Key.Status.LOST)

    def test_form_encoded_body_also_works(self):
        res = self.client.post(self.url, {"condition": "DAMAGED", "remarks": "bent"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], BorrowTransaction.Status.DAMAGED)

    def test_already_returned_is_422_with_a_readable_error(self):
        self.post({"condition": "OK"})
        res = self.post({"condition": "OK"})
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.json()["error"], "This transaction is already closed.")

    def test_unknown_condition_is_422(self):
        res = self.post({"condition": "MELTED"})
        self.assertEqual(res.status_code, 422)
        self.assertIn("MELTED", res.json()["error"])

    def test_missing_transaction_is_404(self):
        res = self.post({}, url=reverse("transactions_api:return", args=[99999]))
        self.assertEqual(res.status_code, 404)
        self.assertIn("error", res.json())

    def test_overdue_transaction_returns_200(self):
        BorrowTransaction.objects.filter(pk=self.txn.pk).update(
            status=BorrowTransaction.Status.OVERDUE)
        res = self.post({"condition": "OK"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["key_status"], Key.Status.AVAILABLE)

    def test_malformed_json_is_422(self):
        res = self.client.post(self.url, data="{nope", content_type="application/json")
        self.assertEqual(res.status_code, 422)

    def test_get_is_rejected(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_no_shift_is_422(self):
        GuardShift.objects.filter(pk=self.shift.pk).update(is_on_duty=False)
        res = self.post({"condition": "OK"})
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.json()["error"], "You must have an active shift to receive keys.")

    def test_wrong_role_is_403_and_does_not_return_the_key(self):
        staff = User.objects.create_user(
            username="prof", email="prof@example.edu", password="pw",
            role=User.Role.FACULTY_STAFF, approval_status=User.Approval.APPROVED)
        self.client.force_login(staff)
        res = self.post({"condition": "OK"})
        self.assertEqual(res.status_code, 403)
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, BorrowTransaction.Status.ACTIVE)



@TESTSERVER
class LoadingAndErrorStatesTests(TestCase):
    """TASK F6.8 — Loading and error states, session expired path, and skeletons."""

    def setUp(self):
        self.guard = User.objects.create_user(
            username="guard_f68", email="guard_f68@example.edu", password="pw",
            role=User.Role.GUARD, approval_status=User.Approval.APPROVED)

    def test_base_template_includes_session_expired_modal_and_skeletons_script(self):
        self.client.force_login(self.guard)
        res = self.client.get(reverse("transactions:borrow_dashboard"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "session-expired-modal")
        self.assertContains(res, "Session expired")
        self.assertContains(res, "Log in again")
        self.assertContains(res, "js/skeletons.js")
        self.assertContains(res, "js/toast.js")
        self.assertContains(res, "js/csrf.js")

    def test_borrow_dashboard_includes_both_scripts_and_search_targets(self):
        self.client.force_login(self.guard)
        res = self.client.get(reverse("transactions:borrow_dashboard"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "js/pos.js")
        self.assertContains(res, "js/currently_out.js")
        self.assertContains(res, "data-pos-borrower-results")

    def test_active_api_unauthenticated_returns_403_session_expired(self):
        res = self.client.get(reverse("transactions_api:active"))
        self.assertEqual(res.status_code, 403)
        data = res.json()
        self.assertIn("error", data)
        self.assertIn("session has expired", data["error"].lower())

    def test_login_preserves_next_query_parameter_for_reauthentication(self):
        login_url = reverse("accounts:login") + "?next=/transactions/borrow/"
        res = self.client.get(login_url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'action="?next=/transactions/borrow/"')
