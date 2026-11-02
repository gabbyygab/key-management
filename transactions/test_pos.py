"""Tests for the POS release wrapper, schedule enforcement and overdue sweep
(B4.1, B4.4, B5.5, F3.5, F3.6)."""
import json
from datetime import datetime, time, timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Borrower, Department, User
from audit.models import ActivityLog
from facilities.models import Building, Room
from keys.models import Key, KeyCategory
from scheduling.models import ScheduleBlock, Term

from .models import BorrowTransaction, GuardShift
from .services import BorrowError, ScheduleWarning, mark_overdue, parse_due, pos_borrow

TESTSERVER = override_settings(ALLOWED_HOSTS=["testserver"])

# A Monday inside the test term, mid-morning, in the server's time zone.
MONDAY_10AM = timezone.make_aware(datetime(2026, 9, 7, 10, 0))


class PosFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        dept = Department.objects.create(code="CCS", name="Computer Studies")
        cls.guard = User.objects.create_user(
            username="guard", email="g@example.com", password="pw-12345-long",
            role=User.Role.GUARD, approval_status=User.Approval.APPROVED)
        cls.instructor = Borrower.objects.create(
            id_number="F-1", first_name="Maria", last_name="Cruz",
            borrower_type="FACULTY", department=dept)
        cls.other = Borrower.objects.create(
            id_number="F-2", first_name="Jose", last_name="Rizal",
            borrower_type="FACULTY", department=dept)
        building = Building.objects.create(code="CCS", name="Computing")
        cls.room = Room.objects.create(building=building, room_code="101")
        category = KeyCategory.objects.create(name="Classroom")
        cls.key = Key.objects.create(key_code="CCS-101-1", room=cls.room, category=category)
        cls.key2 = Key.objects.create(key_code="CCS-101-2", room=cls.room, category=category,
                                      copy_number=2)
        cls.term = Term.objects.create(name="2026 Fall", start_date="2026-08-01",
                                       end_date="2026-12-20", is_active=True)
        cls.block = ScheduleBlock.objects.create(
            term=cls.term, room=cls.room, title="CS 101", instructor=cls.instructor,
            day_of_week=0, start_time=time(9, 0), end_time=time(11, 0))

    def setUp(self):
        self.shift = GuardShift.objects.create(guard=self.guard, post="Main Desk")


class PosBorrowScheduleTests(PosFixture):
    def test_scheduled_instructor_gets_block_end_as_due_time(self):
        txn = pos_borrow(key_id=self.key.pk, borrower=self.instructor, guard=self.guard,
                         now=MONDAY_10AM)
        self.assertEqual(timezone.localtime(txn.expected_return_at).time(), time(11, 0))
        self.assertFalse(txn.remarks.startswith("Override:"))

    @override_settings(SCHEDULE_ENFORCEMENT="warn")
    def test_warn_mode_requires_reason_and_rolls_back(self):
        with self.assertRaises(ScheduleWarning) as ctx:
            pos_borrow(key_id=self.key.pk, borrower=self.other, guard=self.guard,
                       now=MONDAY_10AM)
        self.assertEqual(ctx.exception.schedule["state"], "other")
        self.assertNotIn("_expected_return_dt", ctx.exception.schedule)
        # Nothing was issued: the key is still on the board.
        self.key.refresh_from_db()
        self.assertEqual(self.key.status, Key.Status.AVAILABLE)
        self.assertFalse(BorrowTransaction.objects.exists())

    @override_settings(SCHEDULE_ENFORCEMENT="warn")
    def test_warn_mode_override_is_recorded_and_audited(self):
        txn = pos_borrow(key_id=self.key.pk, borrower=self.other, guard=self.guard,
                         override_reason="Instructor asked me to open it", now=MONDAY_10AM)
        self.assertEqual(txn.remarks, "Override: Instructor asked me to open it")
        self.assertTrue(ActivityLog.objects.filter(action=ActivityLog.Action.OVERRIDE).exists())
        self.assertTrue(ActivityLog.objects.filter(action=ActivityLog.Action.BORROW).exists())

    @override_settings(SCHEDULE_ENFORCEMENT="block")
    def test_block_mode_refuses_even_with_reason(self):
        with self.assertRaises(ScheduleWarning):
            pos_borrow(key_id=self.key.pk, borrower=self.other, guard=self.guard,
                       override_reason="Please", now=MONDAY_10AM)
        self.assertFalse(BorrowTransaction.objects.exists())

    @override_settings(SCHEDULE_ENFORCEMENT="off")
    def test_off_mode_skips_the_check(self):
        txn = pos_borrow(key_id=self.key.pk, borrower=self.other, guard=self.guard,
                         now=MONDAY_10AM)
        self.assertEqual(txn.status, BorrowTransaction.Status.ACTIVE)

    def test_free_room_needs_no_override(self):
        evening = MONDAY_10AM.replace(hour=19)
        txn = pos_borrow(key_id=self.key.pk, borrower=self.other, guard=self.guard, now=evening)
        self.assertIsNone(txn.expected_return_at)

    def test_past_due_time_rejected(self):
        with self.assertRaises(BorrowError):
            pos_borrow(key_id=self.key.pk, borrower=self.instructor, guard=self.guard,
                       expected_return_at=timezone.now() - timedelta(minutes=1))

    def test_borrower_with_overdue_key_cannot_borrow_another(self):
        txn = pos_borrow(key_id=self.key.pk, borrower=self.instructor, guard=self.guard,
                         expected_return_at=timezone.now() + timedelta(hours=1))
        mark_overdue(now=timezone.now() + timedelta(hours=2))
        txn.refresh_from_db()
        self.assertEqual(txn.status, BorrowTransaction.Status.OVERDUE)
        with self.assertRaisesMessage(BorrowError, "overdue"):
            pos_borrow(key_id=self.key2.pk, borrower=self.instructor, guard=self.guard)


class MarkOverdueTests(PosFixture):
    def test_idempotent_and_flips_key(self):
        txn = pos_borrow(key_id=self.key.pk, borrower=self.other, guard=self.guard,
                         expected_return_at=timezone.now() + timedelta(minutes=30))
        later = timezone.now() + timedelta(hours=1)
        self.assertEqual(mark_overdue(now=later), 1)
        self.assertEqual(mark_overdue(now=later), 0)
        txn.refresh_from_db()
        self.key.refresh_from_db()
        self.assertEqual(txn.status, BorrowTransaction.Status.OVERDUE)
        self.assertEqual(self.key.status, Key.Status.OVERDUE)

    def test_without_due_time_never_overdue(self):
        pos_borrow(key_id=self.key.pk, borrower=self.other, guard=self.guard,
                   now=MONDAY_10AM.replace(hour=19))
        self.assertEqual(mark_overdue(now=timezone.now() + timedelta(days=30)), 0)

    def test_management_command(self):
        out = StringIO()
        call_command("mark_overdue", stdout=out)
        self.assertIn("0", out.getvalue())


class ParseDueTests(TestCase):
    def test_datetime_local_is_server_local(self):
        dt = parse_due("2026-11-01T08:00")
        self.assertTrue(timezone.is_aware(dt))
        self.assertEqual(timezone.localtime(dt).hour, 8)

    def test_blank_and_invalid(self):
        self.assertIsNone(parse_due(""))
        with self.assertRaises(ValueError):
            parse_due("tomorrow-ish")


@TESTSERVER
class BorrowApiTests(PosFixture):
    url = reverse("transactions_api:borrow")

    def post(self, **body):
        return self.client.post(self.url, json.dumps(body), content_type="application/json")

    def test_guard_releases_with_due_time(self):
        self.client.force_login(self.guard)
        due = (timezone.localtime() + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")
        res = self.post(borrower_id=self.other.pk, key_id=self.key.pk, purpose="Meeting",
                        expected_return_at=due)
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual(body["key_code"], "CCS-101-1")
        self.assertIsNotNone(body["expected_return_at"])
        self.assertIn("counts", body)

    def test_rule_failures_are_422_json(self):
        self.client.force_login(self.guard)
        self.assertEqual(self.post(borrower_id=self.other.pk).status_code, 422)
        self.assertEqual(self.post(borrower_id=self.other.pk, key_id=self.key.pk,
                                   expected_return_at="nonsense").status_code, 422)

    def test_no_shift_is_422(self):
        GuardShift.objects.filter(guard=self.guard).update(is_on_duty=False)
        self.client.force_login(self.guard)
        # force_login opened a new shift; close it too.
        GuardShift.objects.filter(guard=self.guard).update(is_on_duty=False)
        res = self.post(borrower_id=self.other.pk, key_id=self.key.pk)
        self.assertEqual(res.status_code, 422)
        self.assertIn("error", res.json())

    def test_anonymous_and_admin_get_403_json(self):
        res = self.post(borrower_id=self.other.pk, key_id=self.key.pk)
        self.assertEqual(res.status_code, 403)
        admin = User.objects.create_user(username="adm", email="a@example.com",
                                         password="pw-12345-long", role=User.Role.ADMIN,
                                         approval_status=User.Approval.APPROVED)
        self.client.force_login(admin)
        self.assertEqual(self.post(borrower_id=self.other.pk, key_id=self.key.pk).status_code, 403)

    def test_get_not_allowed(self):
        self.client.force_login(self.guard)
        self.assertEqual(self.client.get(self.url).status_code, 405)


@TESTSERVER
class SearchApiTests(PosFixture):
    def test_borrower_search_flags_holder(self):
        self.client.force_login(self.guard)
        pos_borrow(key_id=self.key.pk, borrower=self.other, guard=self.guard,
                   now=MONDAY_10AM.replace(hour=19))
        res = self.client.get(reverse("accounts_api:borrower_search"), {"q": "F-"})
        self.assertEqual(res.status_code, 200)
        rows = {r["id_number"]: r for r in res.json()["results"]}
        self.assertIsNotNone(rows["F-2"]["active_transaction"])
        self.assertIsNone(rows["F-1"]["active_transaction"])

    def test_key_search_exact_match_and_availability(self):
        self.client.force_login(self.guard)
        res = self.client.get(reverse("keys_api:search"), {"q": "ccs-101-2"})
        self.assertEqual(res.json()["exact"], self.key2.pk)
        pos_borrow(key_id=self.key2.pk, borrower=self.other, guard=self.guard,
                   now=MONDAY_10AM.replace(hour=19))
        res = self.client.get(reverse("keys_api:search"), {"q": "CCS-101"})
        codes = [k["key_code"] for k in res.json()["results"]]
        self.assertEqual(codes, ["CCS-101-1"])

    def test_key_detail_includes_schedule_and_enforcement(self):
        self.client.force_login(self.guard)
        res = self.client.get(reverse("keys_api:detail", args=[self.key.pk]),
                              {"borrower": self.other.pk})
        body = res.json()
        self.assertIn(body["schedule"]["state"], {"free", "other", "holder"})
        self.assertIn("enforcement", body["schedule"])
        self.assertNotIn("_expected_return_dt", body["schedule"])

    def test_admin_gets_403_on_guard_apis(self):
        admin = User.objects.create_user(username="adm", email="a@example.com",
                                         password="pw-12345-long", role=User.Role.ADMIN,
                                         approval_status=User.Approval.APPROVED)
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse("accounts_api:borrower_search")).status_code, 403)
        self.assertEqual(self.client.get(reverse("keys_api:search")).status_code, 403)


class GuardPostShiftTests(TestCase):
    def test_shift_opens_at_assigned_post_for_guards_only(self):
        from facilities.models import GuardPost
        post = GuardPost.objects.create(name="Main Gate")
        guard = User.objects.create_user(username="g", email="g@example.com",
                                         password="pw-12345-long", role=User.Role.GUARD,
                                         approval_status=User.Approval.APPROVED,
                                         assigned_post=post)
        admin = User.objects.create_user(username="a", email="a@example.com",
                                         password="pw-12345-long", role=User.Role.ADMIN,
                                         approval_status=User.Approval.APPROVED)
        self.client.force_login(guard)
        self.client.force_login(admin)
        self.assertEqual(GuardShift.objects.get(guard=guard, is_on_duty=True).post, "Main Gate")
        self.assertFalse(GuardShift.objects.filter(guard=admin).exists())
