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


from scheduling.models import ScheduleBlock, Term
from audit.models import ActivityLog


@TESTSERVER
class RoleSplitDashboardTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(code="CCS", name="Computer Studies")
        self.admin = User.objects.create_user(
            username="admin_user", email="admin@example.edu", password="pw",
            role=User.Role.ADMIN, approval_status=User.Approval.APPROVED,
        )
        self.guard = User.objects.create_user(
            username="guard_user", email="guard@example.edu", password="pw",
            role=User.Role.GUARD, approval_status=User.Approval.APPROVED,
        )
        self.staff = User.objects.create_user(
            username="staff_user", email="staff@example.edu", password="pw",
            role=User.Role.FACULTY_STAFF, approval_status=User.Approval.APPROVED,
        )
        self.building = Building.objects.create(code="CCS", name="Computing")
        self.room = Room.objects.create(building=self.building, room_code="101", capacity=30)
        self.category = KeyCategory.objects.create(name="Classroom")
        self.key = Key.objects.create(key_code="CCS-101-1", room=self.room, category=self.category)
        self.url = reverse("dashboard")

    def test_anonymous_redirected_to_login(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 302)
        self.assertIn("login", res.url)

    def test_unauthorized_role_gets_403(self):
        self.client.force_login(self.staff)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 403)

    def test_admin_dashboard_renders_admin_view_and_stat_cards(self):
        pending_user = User.objects.create_user(
            username="pending_user", email="pending@example.edu", password="pw",
            role=User.Role.GUARD, approval_status=User.Approval.PENDING,
        )
        today = timezone.localdate()
        term = Term.objects.create(
            name="2026 Term", start_date=today - timedelta(days=10),
            end_date=today + timedelta(days=10), is_active=True,
        )
        block = ScheduleBlock.objects.create(
            term=term, room=self.room, title="Algorithms",
            day_of_week=today.weekday(), start_time="09:00", end_time="10:30",
            created_by=self.admin,
        )
        guard_shift = GuardShift.objects.create(guard=self.guard, post="Gate 1", is_on_duty=True)
        borrower = Borrower.objects.create(
            id_number="ST-101", first_name="Juan", last_name="Luna",
            borrower_type=Borrower.BorrowerType.STUDENT, department=self.dept,
        )
        overdue_txn = BorrowTransaction.objects.create(
            transaction_no="TXN-OVERDUE-01", key=self.key, borrower=borrower,
            room=self.room, released_by=self.guard, guard_shift=guard_shift,
            status=BorrowTransaction.Status.OVERDUE,
            expected_return_at=timezone.now() - timedelta(hours=2),
        )
        ActivityLog.objects.create(
            user=self.admin, action=ActivityLog.Action.CREATE,
            description="Created room 101",
        )

        self.client.force_login(self.admin)
        # Login signal creates an active shift for the admin user; remove it so only the guard is on duty
        GuardShift.objects.filter(guard=self.admin).delete()

        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.context["is_admin"])
        self.assertEqual(res.context["total_keys"], 1)
        self.assertEqual(res.context["available"], 1)
        self.assertEqual(res.context["overdue"], 1)
        self.assertEqual(res.context["total_rooms"], 1)
        self.assertEqual(res.context["blocks_scheduled_today"], 1)
        self.assertEqual(res.context["pending_approvals"], 1)
        self.assertEqual(len(res.context["guards_on_duty"]), 1)
        self.assertEqual(len(res.context["today_schedule"]), 1)
        self.assertEqual(len(res.context["overdue_keys"]), 1)
        self.assertEqual(len(res.context["recent_audit"]), 1)

        self.assertContains(res, "Admin Dashboard")
        self.assertContains(res, "Key Availability by Building")
        self.assertContains(res, "Guards on Duty")
        self.assertContains(res, "Gate 1")
        self.assertContains(res, "Today's Schedule Overview")
        self.assertContains(res, "Algorithms")
        self.assertContains(res, "Overdue Keys")
        self.assertContains(res, "TXN-OVERDUE-01")
        self.assertContains(res, "Recent Audit Activity")
        self.assertContains(res, "Created room 101")

    def test_guard_dashboard_renders_guard_view(self):
        self.client.force_login(self.guard)
        shift = GuardShift.objects.filter(guard=self.guard, is_on_duty=True).first()
        shift.post = "Lobby Desk"
        shift.save()

        borrower = Borrower.objects.create(
            id_number="FAC-202", first_name="Clara", last_name="Reyes",
            borrower_type=Borrower.BorrowerType.FACULTY, department=self.dept,
        )
        txn = BorrowTransaction.objects.create(
            transaction_no="TXN-GUARD-01", key=self.key, borrower=borrower,
            room=self.room, released_by=self.guard, guard_shift=shift,
            status=BorrowTransaction.Status.ACTIVE,
        )

        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.context["is_admin"])
        self.assertEqual(res.context["shift"], shift)
        self.assertEqual(res.context["my_released_count"], 1)
        self.assertEqual(res.context["currently_out_count"], 1)

        self.assertContains(res, "Guard Dashboard")
        self.assertContains(res, "Go to Key Transactions")
        self.assertContains(res, "Lobby Desk")
        self.assertContains(res, "On Duty")
        self.assertContains(res, "Keys Released This Shift")
        self.assertContains(res, "TXN-GUARD-01")
        self.assertContains(res, "Currently Out (All Campus Keys)")
        self.assertContains(res, "Clara Reyes")

        self.assertNotContains(res, "Admin Dashboard")
        self.assertNotContains(res, "Key Availability by Building")
        self.assertNotContains(res, "Recent Audit Activity")

    def test_guard_off_duty_state(self):
        self.client.force_login(self.guard)
        GuardShift.objects.filter(guard=self.guard).update(is_on_duty=False)

        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.context["is_admin"])
        self.assertIsNone(res.context["shift"])
        self.assertEqual(res.context["my_released_count"], 0)
        self.assertContains(res, "Off Duty")



@TESTSERVER
class NoActiveShiftPosTests(OutKeyFixture):
    """TASK F3.8: POS screen blocking state when guard has no active shift."""

    def setUp(self):
        super().setUp()
        self.url = reverse("transactions:borrow_dashboard")
        self.confirm_url = reverse("transactions:borrow_confirm")
        self.client.force_login(self.guard)

    def test_no_active_shift_shows_full_panel_blocking_message(self):
        # Close the shift so the guard is off duty
        GuardShift.objects.filter(guard=self.guard).update(is_on_duty=False)

        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.context["shift"])

        # POS root data attribute signals shiftActive=0
        self.assertContains(res, 'data-shift-active="0"')

        # Full-panel blocking card and notice are present
        self.assertContains(res, "data-pos-blocked-panel")
        self.assertContains(res, "data-pos-blocked-notice")
        self.assertContains(res, "Key Release Blocked — No Active Shift")
        self.assertContains(res, "The backend refuses all borrowing without an open guard shift")
        self.assertContains(res, "Guard shifts start automatically on login")
        self.assertContains(res, "Log out and back in to start one")

        # Action button to trigger logout modal
        self.assertContains(res, 'data-modal-open="logout-modal"')
        self.assertContains(res, "Log Out to Start Shift")

        # Release button is explicitly disabled
        self.assertContains(res, "data-pos-release")
        self.assertContains(res, "disabled")
        self.assertContains(res, 'aria-disabled="true"')
        self.assertContains(res, "You must start a shift before releasing keys.")

        # Guard is NOT allowed to fill in the form (search form is blocked)
        self.assertNotContains(res, "1 · Search person")
        self.assertNotContains(res, 'id="pos-q-borrower"')

    def test_active_shift_renders_operational_pos_screen(self):
        # Shift is open
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertIsNotNone(res.context["shift"])

        self.assertContains(res, 'data-shift-active="1"')
        self.assertContains(res, "1 · Search person")
        self.assertContains(res, 'id="pos-q-borrower"')
        self.assertNotContains(res, "data-pos-blocked-panel")

    def test_off_duty_guard_with_borrower_query_param_still_blocked(self):
        GuardShift.objects.filter(guard=self.guard).update(is_on_duty=False)

        res = self.client.get(self.url, {"borrower_id": self.borrower.pk})
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.context["shift"])

        # Still shows full-panel blocking message and disabled release button
        self.assertContains(res, "data-pos-blocked-panel")
        self.assertContains(res, "data-pos-release")
        self.assertContains(res, "disabled")
        self.assertNotContains(res, "1 · Search person")

    def test_off_duty_guard_cannot_submit_borrow_confirm(self):
        GuardShift.objects.filter(guard=self.guard).update(is_on_duty=False)
        key = self.keys[0]

        res = self.client.post(self.confirm_url, {
            "borrower_id": self.borrower.pk,
            "key_id": key.pk,
            "purpose": "Lecture",
        }, follow=True)

        self.assertEqual(res.status_code, 200)
        # Error message added to messages
        self.assertContains(res, "You must start a shift before releasing keys.")
        # No transaction was created
        self.assertEqual(BorrowTransaction.objects.count(), 0)
        # Key status remains available
        key.refresh_from_db()
        self.assertEqual(key.status, Key.Status.AVAILABLE)
        # Landed back on borrow_dashboard with blocking panel
        self.assertContains(res, "data-pos-blocked-panel")

    def test_on_duty_guard_can_submit_borrow_confirm(self):
        key = self.keys[0]
        res = self.client.post(self.confirm_url, {
            "borrower_id": self.borrower.pk,
            "key_id": key.pk,
            "purpose": "Lecture",
        }, follow=True)

        self.assertEqual(res.status_code, 200)
        self.assertEqual(BorrowTransaction.objects.count(), 1)
        txn = BorrowTransaction.objects.first()
        self.assertEqual(txn.borrower, self.borrower)
        self.assertEqual(txn.key, key)
        self.assertEqual(txn.guard_shift, self.shift)
        self.assertEqual(txn.released_by, self.guard)
        key.refresh_from_db()
        self.assertEqual(key.status, Key.Status.BORROWED)
