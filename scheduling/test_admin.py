"""Tests for the admin scheduling screens: CSV import (B5.7), repeat days
(F5.2), exceptions (F5.5), single/bulk toggles and the conflicts view (F5.4)."""
import json
import os
import tempfile
from datetime import time
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from facilities.models import Building, Room

from .models import ScheduleBlock, ScheduleException, Term


class SchedulingFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="admin", email="a@example.com", password="pw-Strong-12345",
            role=User.Role.ADMIN, approval_status=User.Approval.APPROVED)
        cls.term = Term.objects.create(name="2026 Fall", start_date="2026-08-01",
                                       end_date="2026-12-20", is_active=True)
        cls.building = Building.objects.create(code="CCS", name="Computing")
        cls.r101 = Room.objects.create(building=cls.building, room_code="101")
        cls.r102 = Room.objects.create(building=cls.building, room_code="102")

    def block(self, room=None, day=0, start=(9, 0), end=(10, 30), title="IT 101", active=True):
        return ScheduleBlock.objects.create(
            term=self.term, room=room or self.r101, title=title, day_of_week=day,
            start_time=time(*start), end_time=time(*end), is_active=active)


class ImportScheduleTests(SchedulingFixture):
    def write_csv(self, text):
        fd, path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "w", newline="") as fh:
            fh.write(text)
        self.addCleanup(os.remove, path)
        return path

    def run_import(self, text, *args):
        out, err = StringIO(), StringIO()
        call_command("import_schedule", self.write_csv(text), "--term", self.term.name,
                     *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def test_imports_valid_rows(self):
        out, _ = self.run_import("room_code,day,start,end,title\n"
                                 "101,Mon,09:00,10:30,IT 101\n"
                                 "CCS-102,tuesday,13:00,14:30,IT 102\n")
        self.assertIn("Imported 2", out)
        self.assertEqual(ScheduleBlock.objects.count(), 2)
        self.assertTrue(ScheduleBlock.objects.filter(room=self.r102, day_of_week=1).exists())

    def test_dry_run_writes_nothing(self):
        out, _ = self.run_import("room_code,day,start,end\n101,0,09:00,10:30\n", "--dry-run")
        self.assertIn("Dry run OK: 1", out)
        self.assertFalse(ScheduleBlock.objects.exists())

    def test_any_bad_row_imports_nothing(self):
        self.block(title="Existing")
        csv_text = ("room_code,day,start,end\n"
                    "102,Mon,09:00,10:30\n"      # fine on its own
                    "101,Mon,10:00,11:00\n"      # clashes with the DB
                    "999,Mon,09:00,10:00\n"      # unknown room
                    "102,Mon,10:00,11:00\n")     # overlaps row 2 of this file
        err = StringIO()
        with self.assertRaisesMessage(CommandError, "3 problem(s); nothing was imported."):
            call_command("import_schedule", self.write_csv(csv_text), "--term", self.term.name,
                         stdout=StringIO(), stderr=err)
        self.assertIn("conflicts with Existing", err.getvalue())
        self.assertIn("room '999' not found", err.getvalue())
        self.assertIn("overlaps row 2", err.getvalue())
        self.assertEqual(ScheduleBlock.objects.count(), 1)

    def test_missing_columns_and_unknown_term(self):
        with self.assertRaisesMessage(CommandError, "missing column(s): end"):
            self.run_import("room_code,day,start\n101,0,09:00\n")
        with self.assertRaisesMessage(CommandError, "not found"):
            call_command("import_schedule", self.write_csv("room_code,day,start,end\n"),
                         "--term", "No such term", stdout=StringIO(), stderr=StringIO())


@override_settings(ALLOWED_HOSTS=["testserver"])
class ScheduleAdminViewTests(SchedulingFixture):
    def setUp(self):
        self.client.force_login(self.admin)

    def save(self, **payload):
        data = {"term": self.term.pk, "room": self.r101.pk, "title": "IT 201",
                "day_of_week": 0, "start_time": "13:00", "end_time": "14:30", "is_active": True}
        data.update(payload)
        return self.client.post(reverse("scheduling:schedule_block_save"),
                                json.dumps(data), content_type="application/json")

    def test_repeat_days_create_one_block_per_day(self):
        res = self.save(repeat_days=[0, 2, 4])
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["created"], 3)
        self.assertEqual(sorted(ScheduleBlock.objects.values_list("day_of_week", flat=True)),
                         [0, 2, 4])

    def test_repeat_day_conflict_rolls_back_whole_batch(self):
        self.block(day=2, start=(13, 30), end=(15, 0), title="Wed class")
        res = self.save(repeat_days=[2, 4])
        self.assertEqual(res.status_code, 422)
        self.assertIn("Wed class", res.json()["error"])
        self.assertEqual(ScheduleBlock.objects.count(), 1)  # Monday's block rolled back too

    def test_repeat_days_rejects_out_of_range(self):
        self.assertEqual(self.save(repeat_days=[9]).status_code, 422)
        self.assertFalse(ScheduleBlock.objects.exists())

    def test_exception_create_validates_weekday_and_saves(self):
        block = self.block()  # Mondays
        url = reverse("scheduling:exception_create", args=[block.pk]) + "?modal=1"
        bad = self.client.post(url, {"date": "2026-09-08", "exception_type": "CANCELLED",
                                     "reason": "Holiday"})  # a Tuesday
        self.assertEqual(bad.status_code, 422)
        self.assertContains(bad, "meets on Mondays", status_code=422)

        ok = self.client.post(url, {"date": "2026-09-07", "exception_type": "CANCELLED",
                                    "reason": "Holiday"})
        self.assertEqual(ok.status_code, 201, ok.content)
        self.assertTrue(ScheduleException.objects.filter(schedule_block=block).exists())

        dup = self.client.post(url, {"date": "2026-09-07", "exception_type": "CANCELLED",
                                     "reason": "Again"})
        self.assertContains(dup, "already exists", status_code=422)

    def test_room_change_needs_a_replacement_room(self):
        block = self.block()
        url = reverse("scheduling:exception_create", args=[block.pk]) + "?modal=1"
        res = self.client.post(url, {"date": "2026-09-07", "exception_type": "ROOM_CHANGE",
                                     "reason": "Aircon broken"})
        self.assertContains(res, "Choose the room", status_code=422)

    def test_toggle_refuses_reactivation_that_would_conflict(self):
        old = self.block(title="Old", active=False)
        self.block(start=(10, 0), end=(11, 0), title="New")
        self.assertEqual(self.client.get(reverse("scheduling:schedule_toggle", args=[old.pk])).status_code, 405)
        self.client.post(reverse("scheduling:schedule_toggle", args=[old.pk]))
        old.refresh_from_db()
        self.assertFalse(old.is_active)

    def test_toggle_ignores_offsite_next(self):
        block = self.block()
        res = self.client.post(reverse("scheduling:schedule_toggle", args=[block.pk]),
                               {"next": "https://evil.example.com/"})
        self.assertEqual(res.url, reverse("scheduling:schedule_list"))
        block.refresh_from_db()
        self.assertFalse(block.is_active)  # soft delete, row kept

    def test_bulk_toggle_skips_conflicting_reactivations(self):
        a = self.block(title="A", active=False)
        b = self.block(room=self.r102, title="B", active=False)
        self.block(start=(10, 0), end=(11, 0), title="Blocker")
        res = self.client.post(reverse("scheduling:schedule_list"), {
            "bulk_action": "activate", "selected_schedule": [a.pk, b.pk],
            "return_qs": "state=inactive"})
        self.assertEqual(res.url, reverse("scheduling:schedule_list") + "?state=inactive")
        a.refresh_from_db(); b.refresh_from_db()
        self.assertFalse(a.is_active)
        self.assertTrue(b.is_active)

    def test_conflicts_view_lists_only_overlapping_blocks(self):
        # Overlaps can exist from before validation (e.g. legacy data); the
        # conflicts view is how an admin finds them.
        ScheduleBlock.objects.bulk_create([
            ScheduleBlock(term=self.term, room=self.r101, title="Clash A", day_of_week=0,
                          start_time=time(9), end_time=time(10, 30)),
            ScheduleBlock(term=self.term, room=self.r101, title="Clash B", day_of_week=0,
                          start_time=time(10), end_time=time(11)),
        ])
        self.block(room=self.r102, title="Peaceful")
        res = self.client.get(reverse("scheduling:conflicts"))
        self.assertContains(res, "Clash A")
        self.assertContains(res, "Clash B")
        self.assertNotContains(res, "Peaceful")

    def test_guard_is_refused(self):
        guard = User.objects.create_user(username="g", email="g@example.com",
                                         password="pw-Strong-12345", role=User.Role.GUARD,
                                         approval_status=User.Approval.APPROVED)
        self.client.force_login(guard)
        self.assertEqual(self.client.get(reverse("scheduling:schedule_list")).status_code, 403)
        self.assertEqual(self.save().status_code, 403)
