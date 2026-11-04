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
            role=User.Role.ADMIN)
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

    def test_guard_is_refused(self):
        guard = User.objects.create_user(username="g", email="g@example.com",
                                         password="pw-Strong-12345", role=User.Role.GUARD)
        self.client.force_login(guard)
        self.assertEqual(self.client.get(reverse("scheduling:room_timetable")).status_code, 403)
        self.assertEqual(self.save().status_code, 403)


class CatalogueBulkTests(SchedulingFixture):
    """Terms, subjects and sections: bulk archive / restore / delete forever."""

    def setUp(self):
        from .models import Section, Subject
        self.client.force_login(self.admin)
        self.subject = Subject.objects.create(code="IT101", title="Intro")
        self.section = Section.objects.create(program="BSIT", year_level=1, block="A")
        self.rows = {
            "term": (Term, self.term), "subject": (Subject, self.subject),
            "section": (Section, self.section),
        }

    def post(self, name, action, obj):
        return self.client.post(reverse(f"scheduling:{name}_bulk"),
                                {"action": action, "ids": [obj.pk]},
                                headers={"x-requested-with": "XMLHttpRequest"})

    def test_archive_restore_and_delete_forever(self):
        for name, (model, obj) in self.rows.items():
            with self.subTest(name):
                # delete is refused while the row is live
                self.assertEqual(self.post(name, "delete", obj).status_code, 422)
                self.assertTrue(model.objects.filter(pk=obj.pk).exists())

                self.assertEqual(self.post(name, "archive", obj).status_code, 200)
                obj.refresh_from_db()
                self.assertFalse(obj.is_active)
                self.assertIsNotNone(obj.archived_at)

                self.assertEqual(self.post(name, "restore", obj).status_code, 200)
                obj.refresh_from_db()
                self.assertTrue(obj.is_active)
                self.assertIsNone(obj.archived_at)

                self.post(name, "archive", obj)
                self.assertEqual(self.post(name, "delete", obj).status_code, 200)
                self.assertFalse(model.objects.filter(pk=obj.pk).exists())

    def test_row_delete_removes_term_with_its_blocks(self):
        self.block()
        self.term.is_active = False
        self.term.save()
        res = self.client.post(reverse("scheduling:term_delete", args=[self.term.pk]))
        self.assertEqual(res.status_code, 302)
        self.assertFalse(Term.objects.filter(pk=self.term.pk).exists())
        self.assertFalse(ScheduleBlock.objects.exists())

    def test_guard_gets_403(self):
        guard = User.objects.create_user(username="g2", email="g2@example.com",
                                         password="pw-Strong-12345", role=User.Role.GUARD)
        self.client.force_login(guard)
        for name in self.rows:
            self.assertEqual(self.client.post(reverse(f"scheduling:{name}_bulk"),
                                              {"action": "archive"}).status_code, 403)

    def test_lists_render_bulk_controls(self):
        for name in self.rows:
            res = self.client.get(reverse(f"scheduling:{name}_list"))
            self.assertContains(res, "data-bulk-item")
            self.assertContains(res, reverse(f"scheduling:{name}_bulk"))


class OfferingBulkTests(SchedulingFixture):
    def setUp(self):
        from .models import Section, Subject, SubjectOffering
        self.client.force_login(self.admin)
        subject = Subject.objects.create(code="IT102", title="Programming")
        section = Section.objects.create(program="BSIT", year_level=1, block="B")
        self.offering = SubjectOffering.objects.create(
            term=self.term, subject=subject, section=section)
        self.Offering = SubjectOffering

    def post(self, action):
        return self.client.post(reverse("scheduling:offering_bulk"),
                                {"action": action, "ids": [self.offering.pk]},
                                headers={"x-requested-with": "XMLHttpRequest"})

    def test_archive_unschedules_restore_and_delete(self):
        blk = self.block()
        blk.offering = self.offering
        blk.save()
        self.assertEqual(self.post("delete").status_code, 422)
        self.assertEqual(self.post("archive").status_code, 200)
        self.offering.refresh_from_db()
        blk.refresh_from_db()
        self.assertFalse(self.offering.is_active)
        self.assertIsNotNone(self.offering.archived_at)
        self.assertFalse(blk.is_active)
        self.assertContains(self.client.get(reverse("scheduling:offering_archived")),
                            "IT102")
        self.assertEqual(self.post("restore").status_code, 200)
        self.post("archive")
        self.assertEqual(self.post("delete").status_code, 200)
        self.assertFalse(self.Offering.objects.exists())
        blk.refresh_from_db()
        self.assertIsNone(blk.offering)

    def test_list_renders_bulk_controls(self):
        res = self.client.get(reverse("scheduling:offering_list"))
        self.assertContains(res, "data-bulk-item")


class ArchivedHiddenTests(SchedulingFixture):
    """Archived terms and subjects vanish from pickers and the block list."""

    def setUp(self):
        self.client.force_login(self.admin)

    def test_archived_term_not_offered_in_pickers(self):
        old = Term.objects.create(name="2025 Fall", start_date="2025-08-01",
                                  end_date="2025-12-20", is_active=False)
        for name in ("scheduling:section_timetable", "scheduling:offering_list",
                     "scheduling:room_timetable"):
            resp = self.client.get(reverse(name))
            self.assertNotIn(old, list(resp.context["terms"]), name)

class DepartmentPageTests(SchedulingFixture):
    def setUp(self):
        self.client.force_login(self.admin)

    def test_add_edit_archive(self):
        from accounts.models import Department
        url = reverse("scheduling:department_create") + "?modal=1"
        r = self.client.post(url, {"code": "ccs", "name": "Computer Studies", "is_active": "on"})
        self.assertEqual(r.status_code, 201)
        d = Department.objects.get(code="CCS")
        self.assertContains(self.client.get(reverse("scheduling:department_list")), "Computer Studies")
        self.client.post(reverse("scheduling:department_toggle", args=[d.pk]))
        d.refresh_from_db()
        self.assertFalse(d.is_active)
        self.assertContains(self.client.get(reverse("scheduling:department_archived")), "CCS")
        dup = self.client.post(url, {"code": "CCS", "name": "x"})
        self.assertEqual(dup.status_code, 422)


class DepartmentBulkTests(SchedulingFixture):
    def setUp(self):
        from accounts.models import Department
        self.client.force_login(self.admin)
        self.a = Department.objects.create(code="AAA", name="A")
        self.b = Department.objects.create(code="BBB", name="B")
        self.url = reverse("scheduling:department_bulk")

    def post(self, action, *depts):
        return self.client.post(self.url, {"action": action, "ids": [d.pk for d in depts]},
                                headers={"X-Requested-With": "XMLHttpRequest"})

    def test_bulk_archive_restore_delete(self):
        from accounts.models import Department
        self.post("archive", self.a, self.b)
        self.a.refresh_from_db()
        self.assertFalse(self.a.is_active)
        self.assertIsNotNone(self.a.archived_at)
        self.post("restore", self.b)
        self.b.refresh_from_db()
        self.assertTrue(self.b.is_active)
        # a live row cannot be deleted, an archived one can
        self.post("delete", self.b)
        self.assertTrue(Department.objects.filter(pk=self.b.pk).exists())
        self.post("delete", self.a)
        self.assertFalse(Department.objects.filter(pk=self.a.pk).exists())
