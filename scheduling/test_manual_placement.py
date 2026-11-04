"""Manual class placement from the section / room timetables, and the removal
of the old schedule list page."""
from datetime import time

from django.test import TestCase
from django.urls import reverse

from accounts.models import Borrower, User
from facilities.models import Building, Room
from scheduling import planner
from scheduling.models import (CurriculumSubject, ScheduleBlock, Section, Subject,
                               SubjectOffering, Term)
from scheduling.test_planner import mk_term


class ManualPlacementTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="pl_admin", email="pl_admin@school.edu",
            password="pw-Admin-2026", role=User.Role.ADMIN)
        self.term = mk_term()
        self.term.semester = Term.Semester.FIRST
        self.term.save()
        building = Building.objects.create(code="SCI", name="Science")
        self.room = Room.objects.create(building=building, room_code="101", capacity=40)
        self.room2 = Room.objects.create(building=building, room_code="102", capacity=40)
        self.section = Section.objects.create(program="BSIT", year_level=4, block="D")
        self.group = Section.objects.create(program="BSIT", year_level=4, block="D", group="G1")
        self.in_cur = Subject.objects.create(code="IT401", title="Capstone 1")
        self.also = Subject.objects.create(code="IT402", title="Networks")
        self.outside = Subject.objects.create(code="HIS1", title="History")
        for s in (self.in_cur, self.also):
            CurriculumSubject.objects.create(term=self.term, program="BSIT", year_level=4,
                                             semester=Term.Semester.FIRST, subject=s)
        self.prof = Borrower.objects.create(
            id_number="P-9", first_name="Eve", last_name="Cruz",
            borrower_type=Borrower.BorrowerType.FACULTY)

    def place(self, **kw):
        args = dict(term=self.term, section=self.section, subject=self.in_cur,
                    room=self.room, day_of_week=0, start_time=time(9, 0),
                    end_time=time(10, 30), instructor=self.prof, user=self.admin)
        args.update(kw)
        return planner.place_class(**args)

    def test_place_files_an_offering_and_links_the_block(self):
        block, offering, created = self.place()
        self.assertTrue(created)
        self.assertEqual(block.offering_id, offering.pk)
        self.assertEqual(offering.instructor, self.prof)
        self.assertEqual(offering.scheduled_count, 1)
        self.assertEqual(offering.minutes_per_meeting, 90)

    def test_second_meeting_reuses_the_offering_and_grows_the_plan(self):
        _, first, _ = self.place()
        _, second, created = self.place(day_of_week=2)
        self.assertFalse(created)
        self.assertEqual(first.pk, second.pk)
        second.refresh_from_db()
        self.assertEqual(second.meetings_per_week, 2)
        self.assertEqual(second.scheduled_count, 2)

    def test_subject_outside_the_curriculum_is_refused(self):
        with self.assertRaisesMessage(planner.PlaceError, "not in the"):
            self.place(subject=self.outside)

    def test_room_professor_and_section_clashes_are_refused(self):
        self.place()
        with self.assertRaisesMessage(planner.PlaceError, "Room busy"):
            self.place(subject=self.also, section=self.group, instructor=None)
        with self.assertRaisesMessage(planner.PlaceError, "Professor busy"):
            self.place(subject=self.also, room=self.room2,
                       section=Section.objects.create(program="BSIT", year_level=4, block="E"))
        with self.assertRaisesMessage(planner.PlaceError, "Section busy"):
            self.place(subject=self.also, room=self.room2, instructor=None)

    def test_off_grid_times_are_refused(self):
        with self.assertRaises(planner.PlaceError):
            self.place(start_time=time(6, 0), end_time=time(7, 30))
        with self.assertRaises(planner.PlaceError):
            self.place(start_time=time(9, 10), end_time=time(10, 30))
        with self.assertRaises(planner.PlaceError):
            self.place(day_of_week=6)
        self.assertFalse(ScheduleBlock.objects.exists())

    def test_group_cannot_double_a_subject_its_block_already_takes(self):
        self.place()
        rows, _ = planner.placeable_subjects(section=self.group, term=self.term)
        self.assertNotIn("IT401", [r["code"] for r in rows])

    def test_placeable_subjects_are_the_curriculum_with_progress(self):
        self.place()
        rows, restricted = planner.placeable_subjects(section=self.section, term=self.term)
        self.assertTrue(restricted)
        by_code = {r["code"]: r for r in rows}
        self.assertEqual(set(by_code), {"IT401", "IT402"})
        self.assertEqual(by_code["IT401"]["scheduled"], 1)
        self.assertEqual(by_code["IT401"]["instructor_id"], self.prof.pk)

    def test_removing_a_block_lowers_the_offering_count(self):
        block, offering, _ = self.place()
        self.client.force_login(self.admin)
        res = self.client.post(reverse("scheduling:schedule_block_delete", args=[block.pk]))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(SubjectOffering.objects.get(pk=offering.pk).scheduled_count, 0)

    def test_moving_a_block_is_validated_for_the_grid(self):
        block, _, _ = self.place()
        with self.assertRaises(planner.SwapError):
            planner.move_block(block, start_time=time(20, 0), end_time=time(22, 0))


class ManualPlacementApiTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="api_admin", email="api_admin@school.edu",
            password="pw-Admin-2026", role=User.Role.ADMIN)
        self.guard = User.objects.create_user(
            username="api_guard", email="api_guard@school.edu",
            password="pw-Guard-2026", role=User.Role.GUARD)
        self.term = mk_term()
        building = Building.objects.create(code="SCI", name="Science")
        self.room = Room.objects.create(building=building, room_code="101")
        self.section = Section.objects.create(program="BSIT", year_level=4, block="D")
        self.subject = Subject.objects.create(code="IT401", title="Capstone 1")

    def body(self, **kw):
        data = {"term": self.term.pk, "section": self.section.pk,
                "subject": self.subject.pk, "room": self.room.pk, "day_of_week": 0,
                "start_time": "09:00", "end_time": "10:30"}
        data.update(kw)
        return data

    def test_place_api_creates_blocks_for_repeat_days(self):
        self.client.force_login(self.admin)
        res = self.client.post(reverse("scheduling:class_place"),
                               self.body(repeat_days=[2, 4]), content_type="application/json")
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["created"], 3)
        offering = SubjectOffering.objects.get()
        self.assertEqual((offering.scheduled_count, offering.meetings_per_week), (3, 3))

    def test_a_clash_on_a_repeat_day_rolls_everything_back(self):
        self.client.force_login(self.admin)
        ScheduleBlock.objects.create(term=self.term, room=self.room, title="Busy",
                                     day_of_week=2, start_time=time(9, 0), end_time=time(10, 0))
        res = self.client.post(reverse("scheduling:class_place"),
                               self.body(repeat_days=[2]), content_type="application/json")
        self.assertEqual(res.status_code, 422)
        self.assertEqual(ScheduleBlock.objects.count(), 1)
        self.assertFalse(SubjectOffering.objects.exists())

    def test_place_api_is_admin_only(self):
        self.client.force_login(self.guard)
        res = self.client.post(reverse("scheduling:class_place"), self.body(),
                               content_type="application/json")
        self.assertEqual(res.status_code, 403)

    def test_options_api_lists_subjects(self):
        self.client.force_login(self.admin)
        res = self.client.get(reverse("scheduling:class_options"),
                              {"section": self.section.pk, "term": self.term.pk})
        self.assertEqual(res.status_code, 200)
        self.assertEqual([s["code"] for s in res.json()["subjects"]], ["IT401"])

    def test_timetable_pages_render_the_modal_and_old_list_is_gone(self):
        self.client.force_login(self.admin)
        for name in ("room_timetable", "section_timetable", "free_rooms"):
            res = self.client.get(reverse(f"scheduling:{name}"))
            self.assertContains(res, 'id="class-modal"')
        self.assertContains(self.client.get(reverse("scheduling:room_timetable")),
                            'data-slot-time="07:00"')
        res = self.client.get("/scheduling/")
        self.assertRedirects(res, reverse("scheduling:room_timetable"))
        sidebar = self.client.get(reverse("scheduling:room_timetable")).content.decode()
        self.assertNotIn("Schedule List", sidebar)
