"""Tests for the section-aware FCFS planner (scheduling/planner.py)."""
import csv
import io
from datetime import date, time, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Borrower, Department, User
from facilities.models import Building, Room
from scheduling.models import (ScheduleBlock, Section, Subject,
                               SubjectOffering, Term)
from scheduling import planner


def mk_term(name="AY 2026-27 1st"):
    today = timezone.localdate()
    # Start on a Monday so every weekday falls inside the range.
    start = today - timedelta(days=today.weekday())
    return Term.objects.create(name=name, start_date=start,
                               end_date=start + timedelta(days=120))


class SectionFamilyTests(TestCase):
    def setUp(self):
        self.block = Section.objects.create(program="BSIT", year_level=4, block="D")
        self.g1 = Section.objects.create(program="BSIT", year_level=4, block="D", group="G1")
        self.g2 = Section.objects.create(program="BSIT", year_level=4, block="D", group="G2")
        self.other = Section.objects.create(program="BSIT", year_level=4, block="E")

    def test_codes(self):
        self.assertEqual(self.block.code, "BSIT-4D")
        self.assertEqual(self.g1.code, "BSIT-4DG1")
        self.assertEqual(self.g1.block_code, "BSIT-4D")
        self.assertTrue(self.block.is_whole_block)
        self.assertFalse(self.g1.is_whole_block)

    def test_whole_block_clashes_with_every_group(self):
        ids = self.block.clashing_section_ids()
        self.assertEqual(ids, {self.block.pk, self.g1.pk, self.g2.pk})

    def test_group_clashes_with_block_but_not_its_sibling(self):
        ids = self.g1.clashing_section_ids()
        self.assertEqual(ids, {self.g1.pk, self.block.pk})
        self.assertNotIn(self.g2.pk, ids)

    def test_a_different_block_is_a_different_family(self):
        self.assertNotIn(self.other.pk, self.block.clashing_section_ids())

    def test_program_and_group_are_upper_cased_on_save(self):
        s = Section.objects.create(program="bsce", year_level=1, block="a", group="g2")
        self.assertEqual(s.code, "BSCE-1AG2")


class AutoScheduleTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(code="CCS", name="Computer Studies")
        self.term = mk_term()
        self.building = Building.objects.create(code="SCI", name="Science")
        self.room_a = Room.objects.create(building=self.building, room_code="101", capacity=40)
        self.room_b = Room.objects.create(building=self.building, room_code="102", capacity=40)
        self.block = Section.objects.create(program="BSIT", year_level=4, block="D")
        self.g1 = Section.objects.create(program="BSIT", year_level=4, block="D", group="G1")
        self.g2 = Section.objects.create(program="BSIT", year_level=4, block="D", group="G2")
        self.prof1 = Borrower.objects.create(
            id_number="P-1", first_name="Ana", last_name="Reyes",
            borrower_type=Borrower.BorrowerType.FACULTY, department=self.dept)
        self.prof2 = Borrower.objects.create(
            id_number="P-2", first_name="Ben", last_name="Cruz",
            borrower_type=Borrower.BorrowerType.FACULTY, department=self.dept)
        self.math = Subject.objects.create(code="MATH1", title="Calculus")
        self.prog = Subject.objects.create(code="IT101", title="Programming")

    def offer(self, subject, section, instructor, *, meetings=1, minutes=90,
              requested_at=None, **kwargs):
        return SubjectOffering.objects.create(
            term=self.term, subject=subject, section=section, instructor=instructor,
            meetings_per_week=meetings, minutes_per_meeting=minutes,
            requested_at=requested_at or timezone.now(), **kwargs)

    def test_first_offering_takes_the_earliest_slot(self):
        self.offer(self.math, self.block, self.prof1)
        run = planner.auto_schedule(term=self.term)
        self.assertTrue(run.ok, run.unplaced)
        block = ScheduleBlock.objects.get()
        self.assertEqual(block.day_of_week, 0)          # Monday
        self.assertEqual(block.start_time, time(7, 0))  # the window's first slot
        self.assertEqual(block.end_time, time(8, 30))
        self.assertEqual(block.section, self.block)
        self.assertEqual(block.offering.subject, self.math)

    def test_fcfs_order_decides_who_gets_monday_morning(self):
        early = timezone.now() - timedelta(hours=2)
        first = self.offer(self.math, self.block, self.prof1, requested_at=early)
        second = self.offer(self.prog, self.g1, self.prof2, requested_at=timezone.now())
        planner.auto_schedule(term=self.term)
        a = ScheduleBlock.objects.get(offering=first)
        b = ScheduleBlock.objects.get(offering=second)
        self.assertEqual((a.day_of_week, a.start_time), (0, time(7, 0)))
        # The section clash (G1 is part of 4D) pushes the later request later.
        self.assertGreater((b.day_of_week, b.start_time), (a.day_of_week, a.start_time))

    def test_priority_beats_the_request_stamp(self):
        late_but_urgent = self.offer(self.prog, self.g1, self.prof2,
                                     requested_at=timezone.now(), priority=5)
        ordinary = self.offer(self.math, self.block, self.prof1,
                              requested_at=timezone.now() - timedelta(hours=2))
        queue = planner.offering_queue(self.term)
        self.assertEqual(queue[0].pk, late_but_urgent.pk)
        self.assertEqual(queue[1].pk, ordinary.pk)

    def test_sibling_groups_may_share_a_slot_in_different_rooms(self):
        o1 = self.offer(self.math, self.g1, self.prof1)
        o2 = self.offer(self.prog, self.g2, self.prof2)
        run = planner.auto_schedule(term=self.term)
        self.assertTrue(run.ok, run.unplaced)
        a = ScheduleBlock.objects.get(offering=o1)
        b = ScheduleBlock.objects.get(offering=o2)
        self.assertEqual((a.day_of_week, a.start_time), (b.day_of_week, b.start_time))
        self.assertNotEqual(a.room_id, b.room_id)

    def test_whole_block_cannot_share_a_slot_with_its_group(self):
        o1 = self.offer(self.math, self.block, self.prof1)
        o2 = self.offer(self.prog, self.g1, self.prof2)
        planner.auto_schedule(term=self.term)
        a = ScheduleBlock.objects.get(offering=o1)
        b = ScheduleBlock.objects.get(offering=o2)
        self.assertFalse(a.start_time < b.end_time and a.end_time > b.start_time
                         and a.day_of_week == b.day_of_week)

    def test_one_professor_is_not_double_booked(self):
        o1 = self.offer(self.math, self.g1, self.prof1)
        o2 = self.offer(self.prog, self.g2, self.prof1)   # same professor
        planner.auto_schedule(term=self.term)
        a = ScheduleBlock.objects.get(offering=o1)
        b = ScheduleBlock.objects.get(offering=o2)
        self.assertFalse(a.day_of_week == b.day_of_week
                         and a.start_time < b.end_time and a.end_time > b.start_time)

    def test_meetings_of_one_offering_land_on_separate_days(self):
        self.offer(self.math, self.block, self.prof1, meetings=3)
        run = planner.auto_schedule(term=self.term)
        self.assertTrue(run.ok, run.unplaced)
        days = list(ScheduleBlock.objects.values_list("day_of_week", flat=True))
        self.assertEqual(len(days), 3)
        self.assertEqual(len(set(days)), 3)

    def test_dry_run_writes_nothing_but_reports_the_same_placements(self):
        self.offer(self.math, self.block, self.prof1, meetings=2)
        preview = planner.auto_schedule(term=self.term, dry_run=True)
        self.assertEqual(ScheduleBlock.objects.count(), 0)
        self.assertEqual(preview.placed_count, 2)
        self.assertTrue(all(p.block_id is None for p in preview.placements))

        real = planner.auto_schedule(term=self.term)
        self.assertEqual(ScheduleBlock.objects.count(), 2)
        self.assertEqual(
            [(p.day_of_week, p.start_time, p.room.pk) for p in preview.placements],
            [(p.day_of_week, p.start_time, p.room.pk) for p in real.placements])

    def test_a_second_run_does_not_duplicate_what_is_already_placed(self):
        self.offer(self.math, self.block, self.prof1, meetings=2)
        planner.auto_schedule(term=self.term)
        again = planner.auto_schedule(term=self.term)
        self.assertEqual(ScheduleBlock.objects.filter(is_active=True).count(), 2)
        self.assertEqual(again.placed_count, 0)
        self.assertEqual(len(again.skipped), 1)

    def test_unplaceable_offering_is_reported_not_crashed(self):
        # Needs more weekly meetings than there are schedulable days.
        self.offer(self.math, self.block, self.prof1, meetings=6, minutes=480)
        run = planner.auto_schedule(term=self.term, days=(0, 1))
        self.assertFalse(run.ok)
        self.assertEqual(run.unplaced[0].meetings_missing, 4)
        self.assertIn("No free", run.unplaced[0].reason)

    def test_room_type_with_no_match_reports_a_room_reason(self):
        self.offer(self.math, self.block, self.prof1,
                   room_type=Room.RoomType.LABORATORY)
        run = planner.auto_schedule(term=self.term)
        self.assertFalse(run.ok)
        self.assertIn("No usable room", run.unplaced[0].reason)

    def test_a_class_is_never_put_in_a_non_teaching_room(self):
        """A generator or stock room is usable for keys but is not a classroom."""
        self.room_a.room_type = Room.RoomType.FACILITY
        self.room_a.save(update_fields=["room_type"])
        self.room_b.room_type = Room.RoomType.OFFICE
        self.room_b.save(update_fields=["room_type"])
        self.offer(self.math, self.block, self.prof1)
        run = planner.auto_schedule(term=self.term)
        self.assertFalse(run.ok)
        self.assertIn("classroom or laboratory", run.unplaced[0].reason)

    def test_an_explicit_preferred_room_overrides_the_teaching_filter(self):
        self.room_a.room_type = Room.RoomType.FACILITY
        self.room_a.save(update_fields=["room_type"])
        self.room_b.room_type = Room.RoomType.OFFICE
        self.room_b.save(update_fields=["room_type"])
        self.offer(self.math, self.block, self.prof1, preferred_room=self.room_a)
        run = planner.auto_schedule(term=self.term)
        self.assertTrue(run.ok, run.unplaced)
        self.assertEqual(ScheduleBlock.objects.get().room, self.room_a)

    def test_a_room_with_unknown_capacity_is_tried_last(self):
        self.room_a.capacity = None
        self.room_a.save(update_fields=["capacity"])
        self.block.student_count = 20
        self.block.save(update_fields=["student_count"])
        self.offer(self.math, self.block, self.prof1)
        planner.auto_schedule(term=self.term)
        # room_b has a known 40 seats, so it beats the unknown one.
        self.assertEqual(ScheduleBlock.objects.get().room, self.room_b)

    def test_the_smallest_fitting_room_is_used_first(self):
        self.room_a.capacity = 80
        self.room_a.save(update_fields=["capacity"])
        self.room_b.capacity = 45
        self.room_b.save(update_fields=["capacity"])
        self.block.student_count = 40
        self.block.save(update_fields=["student_count"])
        self.offer(self.math, self.block, self.prof1)
        planner.auto_schedule(term=self.term)
        self.assertEqual(ScheduleBlock.objects.get().room, self.room_b)

    def test_preferred_room_is_used_first(self):
        self.offer(self.math, self.block, self.prof1, preferred_room=self.room_b)
        planner.auto_schedule(term=self.term)
        self.assertEqual(ScheduleBlock.objects.get().room, self.room_b)

    def test_student_count_excludes_rooms_that_are_too_small(self):
        self.room_a.capacity = 10
        self.room_a.save(update_fields=["capacity"])
        self.block.student_count = 35
        self.block.save(update_fields=["student_count"])
        self.offer(self.math, self.block, self.prof1)
        planner.auto_schedule(term=self.term)
        self.assertEqual(ScheduleBlock.objects.get().room, self.room_b)

    def test_window_options_are_honoured(self):
        self.offer(self.math, self.block, self.prof1)
        planner.auto_schedule(term=self.term, day_start=time(13, 0), days=(2,))
        block = ScheduleBlock.objects.get()
        self.assertEqual(block.day_of_week, 2)
        self.assertEqual(block.start_time, time(13, 0))

    def test_clear_term_schedule_leaves_hand_made_bookings_alone(self):
        self.offer(self.math, self.block, self.prof1)
        planner.auto_schedule(term=self.term)
        manual = ScheduleBlock.objects.create(
            term=self.term, room=self.room_b, title="Faculty meeting",
            day_of_week=4, start_time=time(15, 0), end_time=time(16, 0))
        planner.clear_term_schedule(self.term)
        manual.refresh_from_db()
        self.assertTrue(manual.is_active)
        self.assertFalse(ScheduleBlock.objects.get(offering__isnull=False).is_active)

    def test_unschedule_offering_puts_it_back_in_the_queue(self):
        offering = self.offer(self.math, self.block, self.prof1, meetings=2)
        planner.auto_schedule(term=self.term)
        self.assertEqual(offering.remaining_meetings, 0)
        planner.unschedule_offering(offering)
        self.assertEqual(offering.remaining_meetings, 2)


class SwapTests(TestCase):
    def setUp(self):
        self.term = mk_term()
        self.building = Building.objects.create(code="SCI", name="Science")
        self.room_a = Room.objects.create(building=self.building, room_code="101")
        self.room_b = Room.objects.create(building=self.building, room_code="102")
        self.s1 = Section.objects.create(program="BSIT", year_level=4, block="D", group="G1")
        self.s2 = Section.objects.create(program="BSIT", year_level=4, block="D", group="G2")
        self.prof = Borrower.objects.create(
            id_number="P-9", first_name="Cia", last_name="Lim",
            borrower_type=Borrower.BorrowerType.FACULTY)

    def mk_block(self, *, section, room, day, start, end, title="X", instructor=None):
        return ScheduleBlock.objects.create(
            term=self.term, room=room, section=section, title=title,
            instructor=instructor, day_of_week=day, start_time=start, end_time=end)

    def test_swap_exchanges_day_and_time(self):
        a = self.mk_block(section=self.s1, room=self.room_a, day=0,
                          start=time(8, 0), end=time(9, 30), title="A")
        b = self.mk_block(section=self.s2, room=self.room_b, day=2,
                          start=time(13, 0), end=time(14, 30), title="B")
        planner.swap_blocks(a, b)
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual((a.day_of_week, a.start_time), (2, time(13, 0)))
        self.assertEqual((b.day_of_week, b.start_time), (0, time(8, 0)))
        self.assertEqual(a.room, self.room_a)   # rooms stay put by default

    def test_swap_can_take_the_rooms_along(self):
        a = self.mk_block(section=self.s1, room=self.room_a, day=0,
                          start=time(8, 0), end=time(9, 30), title="A")
        b = self.mk_block(section=self.s2, room=self.room_b, day=2,
                          start=time(13, 0), end=time(14, 30), title="B")
        planner.swap_blocks(a, b, include_rooms=True)
        a.refresh_from_db()
        self.assertEqual(a.room, self.room_b)

    def test_swap_that_would_double_book_a_professor_is_refused(self):
        a = self.mk_block(section=self.s1, room=self.room_a, day=0,
                          start=time(8, 0), end=time(9, 30), title="A",
                          instructor=self.prof)
        b = self.mk_block(section=self.s2, room=self.room_b, day=2,
                          start=time(13, 0), end=time(14, 30), title="B")
        # The professor already teaches on Wednesday at 13:00 elsewhere, so
        # moving A there is a double booking.
        self.mk_block(section=None, room=self.room_a, day=2, start=time(13, 0),
                      end=time(14, 30), title="Busy", instructor=self.prof)
        with self.assertRaises(planner.SwapError) as cm:
            planner.swap_blocks(a, b)
        self.assertIn("Professor busy", str(cm.exception))
        a.refresh_from_db()
        self.assertEqual(a.day_of_week, 0)   # rolled back

    def test_swap_of_two_blocks_in_the_same_room_is_allowed(self):
        """The partner is mid-swap, so it must not count as its own blocker."""
        a = self.mk_block(section=self.s1, room=self.room_a, day=0,
                          start=time(8, 0), end=time(9, 30), title="A")
        b = self.mk_block(section=self.s2, room=self.room_a, day=0,
                          start=time(10, 0), end=time(11, 30), title="B")
        planner.swap_blocks(a, b)
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(a.start_time, time(10, 0))
        self.assertEqual(b.start_time, time(8, 0))

    def test_swap_across_terms_is_refused(self):
        other = mk_term("AY 2027-28 1st")
        a = self.mk_block(section=self.s1, room=self.room_a, day=0,
                          start=time(8, 0), end=time(9, 30))
        b = ScheduleBlock.objects.create(
            term=other, room=self.room_b, section=self.s2, title="Z",
            day_of_week=1, start_time=time(8, 0), end_time=time(9, 30))
        with self.assertRaises(planner.SwapError):
            planner.swap_blocks(a, b)

    def test_move_block_validates_the_section(self):
        whole = Section.objects.create(program="BSIT", year_level=4, block="D")
        self.mk_block(section=whole, room=self.room_b, day=1, start=time(8, 0),
                      end=time(9, 30), title="Block-wide")
        a = self.mk_block(section=self.s1, room=self.room_a, day=0,
                          start=time(8, 0), end=time(9, 30), title="G1 class")
        with self.assertRaises(planner.SwapError) as cm:
            planner.move_block(a, day_of_week=1)
        self.assertIn("Section busy", str(cm.exception))


class SectionTimetableAndExportTests(TestCase):
    def setUp(self):
        self.term = mk_term()
        self.building = Building.objects.create(code="SCI", name="Science")
        self.room = Room.objects.create(building=self.building, room_code="101")
        self.room2 = Room.objects.create(building=self.building, room_code="102")
        self.whole = Section.objects.create(program="BSIT", year_level=4, block="D")
        self.g1 = Section.objects.create(program="BSIT", year_level=4, block="D", group="G1")
        self.g2 = Section.objects.create(program="BSIT", year_level=4, block="D", group="G2")
        self.prof = Borrower.objects.create(
            id_number="P-3", first_name="Dee", last_name="Santos",
            borrower_type=Borrower.BorrowerType.FACULTY)
        self.subj = Subject.objects.create(code="IT401", title="Capstone 1")

        self.block_wide = ScheduleBlock.objects.create(
            term=self.term, room=self.room, section=self.whole, subject=self.subj,
            title="IT401 · BSIT-4D", instructor=self.prof, day_of_week=0,
            start_time=time(8, 0), end_time=time(9, 30))
        self.g1_block = ScheduleBlock.objects.create(
            term=self.term, room=self.room, section=self.g1, subject=self.subj,
            title="IT401 · BSIT-4DG1", instructor=self.prof, day_of_week=1,
            start_time=time(8, 0), end_time=time(9, 30))
        self.g2_block = ScheduleBlock.objects.create(
            term=self.term, room=self.room2, section=self.g2, subject=self.subj,
            title="IT401 · BSIT-4DG2", instructor=self.prof, day_of_week=1,
            start_time=time(8, 0), end_time=time(9, 30))

    def test_whole_block_view_includes_its_groups(self):
        codes = {b["section_code"] for b in
                 planner.get_section_timetable(self.whole, self.term)["blocks"]}
        self.assertEqual(codes, {"BSIT-4D", "BSIT-4DG1", "BSIT-4DG2"})

    def test_block_only_view_drops_the_groups(self):
        codes = {b["section_code"] for b in planner.get_section_timetable(
            self.whole, self.term, include_groups=False)["blocks"]}
        self.assertEqual(codes, {"BSIT-4D"})

    def test_group_view_shows_the_group_and_the_block_but_not_the_sibling(self):
        codes = {b["section_code"] for b in
                 planner.get_section_timetable(self.g1, self.term)["blocks"]}
        self.assertEqual(codes, {"BSIT-4D", "BSIT-4DG1"})

    def test_concurrent_group_blocks_get_separate_lanes(self):
        data = planner.get_section_timetable(self.whole, self.term)
        tuesday = [b for b in data["blocks"] if b["day_of_week"] == 1]
        self.assertEqual(len(tuesday), 2)
        self.assertEqual({b["lane"] for b in tuesday}, {0, 1})
        self.assertEqual({b["lane_count"] for b in tuesday}, {2})

    def test_grid_rows_match_the_half_hour_window(self):
        monday = [b for b in planner.get_section_timetable(self.whole, self.term)["blocks"]
                  if b["day_of_week"] == 0][0]
        self.assertEqual(monday["row_start"], 3)   # 08:00 is the third half hour from 07:00
        self.assertEqual(monday["span"], 3)        # 90 minutes
        self.assertEqual(monday["col"], 2)         # Monday column

    def test_csv_export_lists_the_section_week(self):
        response = planner.export_section_timetable_csv(self.g1, self.term)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("BSIT-4DG1", response["Content-Disposition"])
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8"))))
        self.assertEqual(rows[0], planner.EXPORT_HEADERS)
        body = [r for r in rows[1:] if r]
        self.assertEqual(len(body), 2)                    # block-wide + G1
        self.assertEqual({r[0] for r in body}, {"BSIT-4D", "BSIT-4DG1"})
        self.assertEqual(body[0][1], "Monday")

    def test_csv_export_sanitises_a_formula_looking_title(self):
        self.subj.title = "=cmd|' /c calc'!A0"
        self.subj.save(update_fields=["title"])
        response = planner.export_section_timetable_csv(self.whole, self.term)
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8"))))
        self.assertFalse(rows[1][5].startswith("="))


class SchedulingPageTests(TestCase):
    """The new pages: admin-only, and the guard gets 403 rather than a redirect."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username="sched_admin", email="sched_admin@school.edu",
            password="pw-Admin-2026", role=User.Role.ADMIN)
        self.guard = User.objects.create_user(
            username="sched_guard", email="sched_guard@school.edu",
            password="pw-Guard-2026", role=User.Role.GUARD)
        self.term = mk_term()
        self.building = Building.objects.create(code="SCI", name="Science")
        self.room = Room.objects.create(building=self.building, room_code="101")
        self.section = Section.objects.create(program="BSIT", year_level=4, block="D")
        self.subject = Subject.objects.create(code="IT401", title="Capstone 1")
        self.prof = Borrower.objects.create(
            id_number="P-4", first_name="Eve", last_name="Dela Cruz",
            borrower_type=Borrower.BorrowerType.FACULTY)

    NAMES = ["subject_list", "section_list", "offering_list", "autoschedule",
             "section_timetable"]

    def test_pages_render_for_an_admin(self):
        self.client.force_login(self.admin)
        for name in self.NAMES:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(f"scheduling:{name}")).status_code, 200)

    def test_a_guard_is_refused_every_page(self):
        self.client.force_login(self.guard)
        for name in self.NAMES:
            with self.subTest(name=name):
                response = self.client.get(reverse(f"scheduling:{name}"))
                self.assertEqual(response.status_code, 403)

    def test_section_create_modal_post_answers_201(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("scheduling:section_create") + "?modal=1",
            {"program": "BSCE", "year_level": 2, "block": "A", "group": "G1",
             "is_active": "on"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 201)
        self.assertTrue(Section.objects.filter(program="BSCE", group="G1").exists())

    def test_duplicate_section_is_refused_with_422(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("scheduling:section_create") + "?modal=1",
            {"program": "BSIT", "year_level": 4, "block": "D", "group": "",
             "is_active": "on"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, "already exists", status_code=422)

    def test_offering_create_then_preview_then_apply(self):
        self.client.force_login(self.admin)
        created = self.client.post(
            reverse("scheduling:offering_create") + "?modal=1",
            {"term": self.term.pk, "subject": self.subject.pk,
             "section": self.section.pk, "instructor": self.prof.pk,
             "meetings_per_week": 2, "minutes_per_meeting": 90,
             "priority": 3, "is_active": "on"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(created.status_code, 201)

        options = {"term": self.term.pk, "day_start": "07:00", "day_end": "21:00",
                   "include_saturday": "on", "one_meeting_per_day": "on"}
        preview = self.client.post(reverse("scheduling:autoschedule"), options)
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(ScheduleBlock.objects.count(), 0)   # dry run wrote nothing

        applied = self.client.post(reverse("scheduling:autoschedule"),
                                   {**options, "apply": "1"})
        self.assertEqual(applied.status_code, 200)
        self.assertEqual(ScheduleBlock.objects.filter(is_active=True).count(), 2)

    def test_autoschedule_add_offering_button_links_to_subject_offerings_add(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("scheduling:autoschedule") + f"?term={self.term.pk}")
        self.assertEqual(response.status_code, 200)
        expected_url = f"{reverse('scheduling:offering_list')}?term={self.term.pk}&amp;add=1"
        self.assertContains(response, f'href="{expected_url}"')

    def test_export_returns_csv(self):
        self.client.force_login(self.admin)
        ScheduleBlock.objects.create(
            term=self.term, room=self.room, section=self.section, subject=self.subject,
            title="IT401", instructor=self.prof, day_of_week=0,
            start_time=time(8, 0), end_time=time(9, 30))
        response = self.client.get(
            reverse("scheduling:section_export"),
            {"section": self.section.pk, "term": self.term.pk})
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment", response["Content-Disposition"])

    def test_room_timetable_shows_the_section_and_exports_csv(self):
        self.client.force_login(self.admin)
        ScheduleBlock.objects.create(
            term=self.term, room=self.room, section=self.section, subject=self.subject,
            instructor=self.prof, title="IT401 · BSIT-4D", day_of_week=1,
            start_time=time(10, 0), end_time=time(11, 30))
        page = self.client.get(reverse("scheduling:room_timetable"),
                               {"room": self.room.pk, "term": self.term.pk})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "BSIT-4D")

        response = self.client.get(reverse("scheduling:room_export"),
                                   {"room": self.room.pk, "term": self.term.pk})
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        self.assertEqual(rows[0][:3], ["Room", "Building", "Day"])
        self.assertEqual(rows[1][:5], ["101", "SCI", "Tuesday", "10:00", "11:30"])
        self.assertEqual(rows[1][7], "BSIT-4D")

        self.client.force_login(self.guard)
        self.assertEqual(self.client.get(reverse("scheduling:room_export"),
                                         {"room": self.room.pk, "term": self.term.pk}
                                         ).status_code, 403)

    def test_swap_endpoint_swaps_two_blocks(self):
        self.client.force_login(self.admin)
        a = ScheduleBlock.objects.create(
            term=self.term, room=self.room, section=self.section, title="A",
            day_of_week=0, start_time=time(8, 0), end_time=time(9, 30))
        b = ScheduleBlock.objects.create(
            term=self.term, room=self.room, section=self.section, title="B",
            day_of_week=2, start_time=time(13, 0), end_time=time(14, 30))
        response = self.client.post(reverse("scheduling:block_swap"),
                                    {"block_a": a.pk, "block_b": b.pk})
        self.assertEqual(response.status_code, 302)
        a.refresh_from_db()
        self.assertEqual((a.day_of_week, a.start_time), (2, time(13, 0)))

    def test_swap_refusal_is_reported_and_changes_nothing(self):
        self.client.force_login(self.admin)
        a = ScheduleBlock.objects.create(
            term=self.term, room=self.room, section=self.section, title="A",
            instructor=self.prof, day_of_week=0,
            start_time=time(8, 0), end_time=time(9, 30))
        b = ScheduleBlock.objects.create(
            term=self.term, room=self.room, section=self.section, title="B",
            day_of_week=2, start_time=time(13, 0), end_time=time(14, 30))
        other_room = Room.objects.create(building=self.building, room_code="999")
        ScheduleBlock.objects.create(
            term=self.term, room=other_room, title="Busy", instructor=self.prof,
            day_of_week=2, start_time=time(13, 0), end_time=time(14, 30))
        response = self.client.post(
            reverse("scheduling:block_swap"), {"block_a": a.pk, "block_b": b.pk},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 422)
        a.refresh_from_db()
        self.assertEqual(a.day_of_week, 0)

    def test_move_api_refuses_a_section_clash_with_422(self):
        self.client.force_login(self.admin)
        ScheduleBlock.objects.create(
            term=self.term, room=self.room, section=self.section, title="Busy",
            day_of_week=1, start_time=time(8, 0), end_time=time(9, 30))
        movable = ScheduleBlock.objects.create(
            term=self.term, room=self.room, section=self.section, title="Movable",
            day_of_week=0, start_time=time(8, 0), end_time=time(9, 30))
        response = self.client.post(
            reverse("scheduling:block_move", args=[movable.pk]),
            {"day_of_week": 1}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 422)
        self.assertIn("Section busy", response.json()["error"])


class DepartmentRoomRuleTests(TestCase):
    """A department's classes only go to that department's rooms (or shared ones)."""

    def setUp(self):
        self.ccs = Department.objects.create(code="CCS", name="Computer Studies")
        self.coe = Department.objects.create(code="COE", name="Engineering")
        self.term = mk_term("AY 2025-2026 1st")
        self.building = Building.objects.create(code="MAIN", name="Main")
        self.ccs_room = Room.objects.create(building=self.building, room_code="C1",
                                            capacity=40, department=self.ccs)
        self.coe_room = Room.objects.create(building=self.building, room_code="E1",
                                            capacity=30, department=self.coe)
        self.section = Section.objects.create(program="BSIT", year_level=1, block="A",
                                              department=self.ccs)
        self.subject = Subject.objects.create(code="IT101", title="Programming")

    def offering(self, **kw):
        return SubjectOffering.objects.create(
            term=self.term, subject=self.subject, section=self.section,
            meetings_per_week=2, minutes_per_meeting=90, **kw)

    def test_only_the_departments_own_rooms_are_candidates(self):
        rooms = planner.candidate_rooms(self.offering())
        self.assertEqual(rooms, [self.ccs_room])

    def test_shared_rooms_are_allowed_but_tried_after_the_departments_own(self):
        shared = Room.objects.create(building=self.building, room_code="S1", capacity=20)
        rooms = planner.candidate_rooms(self.offering())
        self.assertEqual(rooms, [self.ccs_room, shared])  # shared is smaller, still last

    def test_auto_schedule_never_places_a_class_in_another_departments_room(self):
        offering = self.offering()
        run = planner.auto_schedule(term=self.term)
        self.assertTrue(run.ok)
        rooms = {b.room_id for b in ScheduleBlock.objects.filter(offering=offering)}
        self.assertEqual(rooms, {self.ccs_room.pk})

    def test_no_free_department_room_leaves_the_offering_unplaced(self):
        # Fill the only CCS room all week; the free COE room must stay off limits.
        ScheduleBlock.objects.bulk_create([
            ScheduleBlock(term=self.term, room=self.ccs_room, title="Booked",
                          day_of_week=d, start_time=time(7, 0), end_time=time(21, 0))
            for d in range(6)])
        offering = self.offering()
        run = planner.auto_schedule(term=self.term)
        self.assertEqual(run.placed_count, 0)
        self.assertEqual([u.offering for u in run.unplaced], [offering])
        self.assertFalse(ScheduleBlock.objects.filter(room=self.coe_room).exists())

    def test_subject_department_applies_when_the_section_has_none(self):
        self.section.department = None
        self.section.save()
        self.subject.department = self.coe
        self.subject.save()
        self.assertEqual(planner.candidate_rooms(self.offering()), [self.coe_room])

    def test_no_department_anywhere_means_any_room(self):
        self.section.department = None
        self.section.save()
        self.assertEqual({r.pk for r in planner.candidate_rooms(self.offering())},
                         {self.ccs_room.pk, self.coe_room.pk})

    def test_preferred_room_of_another_department_is_refused_by_the_form(self):
        from scheduling.forms import SubjectOfferingForm
        form = SubjectOfferingForm(data={
            "term": self.term.pk, "subject": self.subject.pk, "section": self.section.pk,
            "meetings_per_week": 2, "minutes_per_meeting": 90, "priority": 3,
            "preferred_room": self.coe_room.pk, "is_active": "on"})
        self.assertFalse(form.is_valid())
        self.assertIn("preferred_room", form.errors)


class TermRecordTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="term_admin", email="term_admin@school.edu",
            password="pw-Admin-2026", role=User.Role.ADMIN)

    def test_name_is_built_from_year_and_semester(self):
        term = Term.objects.create(academic_year="2025-2026", semester=Term.Semester.FIRST,
                                   start_date=date(2025, 8, 1), end_date=date(2025, 12, 15))
        self.assertEqual(term.name, "2025-2026 1st Semester")
        self.assertEqual(str(term), "2025-2026 1st Semester")

    def test_form_creates_a_term_and_rejects_bad_or_duplicate_years(self):
        from scheduling.forms import TermForm
        data = {"academic_year": "2025-2026", "semester": "2ND",
                "start_date": "2026-01-05", "end_date": "2026-05-30", "is_active": "on"}
        form = TermForm(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().name, "2025-2026 2nd Semester")
        self.assertFalse(TermForm(data).is_valid())          # same year + semester
        self.assertFalse(TermForm({**data, "academic_year": "2025-2027"}).is_valid())
        self.assertFalse(TermForm({**data, "academic_year": "2025"}).is_valid())

    def test_term_pages_create_and_archive_for_an_admin_only(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("scheduling:term_list")).status_code, 200)
        response = self.client.post(reverse("scheduling:term_create"), {
            "academic_year": "2026-2027", "semester": "1ST", "start_date": "2026-08-03",
            "end_date": "2026-12-12", "is_active": "on"})
        self.assertIn(response.status_code, (201, 302))
        term = Term.objects.get(academic_year="2026-2027")
        self.client.post(reverse("scheduling:term_toggle", args=[term.pk]))
        term.refresh_from_db()
        self.assertFalse(term.is_active)

        guard = User.objects.create_user(
            username="term_guard", email="term_guard@school.edu",
            password="pw-Guard-2026", role=User.Role.GUARD)
        self.client.force_login(guard)
        self.assertEqual(self.client.get(reverse("scheduling:term_list")).status_code, 403)


class LectureLabOfferingTests(TestCase):
    """A lecture + lab subject: 2 meetings a week, 1 in a classroom, 1 in a lab."""

    def setUp(self):
        self.term = mk_term("AY LL")
        b = Building.objects.create(code="LL", name="LL Hall")
        self.classroom = Room.objects.create(building=b, room_code="C1", capacity=40,
                                             room_type=Room.RoomType.CLASSROOM)
        self.lab = Room.objects.create(building=b, room_code="L1", capacity=40,
                                       room_type=Room.RoomType.LABORATORY)
        self.section = Section.objects.create(program="BSIT", year_level=1, block="A")
        self.subject = Subject.objects.create(code="LL101", title="Lecture Lab")

    def _offering(self, **kw):
        return SubjectOffering.objects.create(
            term=self.term, subject=self.subject, section=self.section,
            meeting_format=SubjectOffering.MeetingFormat.LECTURE_LAB,
            minutes_per_meeting=60, lab_minutes_per_meeting=180, **kw)

    def test_places_one_lecture_in_classroom_and_one_lab_in_laboratory(self):
        o = self._offering()
        self.assertEqual(o.meetings_per_week, 2)
        run = planner.auto_schedule(term=self.term)
        self.assertTrue(run.ok, run.unplaced)
        blocks = {b.meeting_kind: b for b in o.scheduled_blocks()}
        self.assertEqual(set(blocks), {"LECTURE", "LAB"})
        self.assertEqual(blocks["LECTURE"].room, self.classroom)
        self.assertEqual(blocks["LAB"].room, self.lab)
        self.assertNotEqual(blocks["LECTURE"].day_of_week, blocks["LAB"].day_of_week)
        lecture, lab = blocks["LECTURE"], blocks["LAB"]
        self.assertEqual((lecture.end_time.hour - lecture.start_time.hour), 1)
        self.assertEqual((lab.end_time.hour - lab.start_time.hour), 3)

    def test_rerun_only_places_the_missing_kind(self):
        o = self._offering()
        planner.auto_schedule(term=self.term)
        o.scheduled_blocks().filter(meeting_kind="LAB").update(is_active=False)
        run = planner.auto_schedule(term=self.term)
        self.assertEqual(run.placed_count, 1)
        self.assertEqual(run.placements[0].room, self.lab)

    def test_no_laboratory_reports_unplaced_but_keeps_the_lecture(self):
        self.lab.is_active = False
        self.lab.save()
        o = self._offering()
        run = planner.auto_schedule(term=self.term)
        self.assertEqual(run.unplaced_count, 1)
        self.assertIn("LABORATORY", run.unplaced[0].reason)
        self.assertEqual([b.meeting_kind for b in o.scheduled_blocks()], ["LECTURE"])

    def test_preferred_room_of_the_wrong_type_is_ignored_per_meeting(self):
        o = self._offering(preferred_room=self.classroom)
        planner.auto_schedule(term=self.term)
        lab_block = o.scheduled_blocks().get(meeting_kind="LAB")
        self.assertEqual(lab_block.room, self.lab)

    def test_separate_preferred_lecture_and_lab_rooms(self):
        c2 = Room.objects.create(building=self.classroom.building, room_code="C2",
                                 capacity=40, room_type=Room.RoomType.CLASSROOM)
        l2 = Room.objects.create(building=self.classroom.building, room_code="L2",
                                 capacity=40, room_type=Room.RoomType.LABORATORY)
        o = self._offering(preferred_room=c2, preferred_lab_room=l2)
        planner.auto_schedule(term=self.term)
        self.assertEqual(o.scheduled_blocks().get(meeting_kind="LECTURE").room, c2)
        self.assertEqual(o.scheduled_blocks().get(meeting_kind="LAB").room, l2)
