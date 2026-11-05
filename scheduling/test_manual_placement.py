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

        # Classes can only be placed for subjects already offered to the section.
        for sec, subj in ((self.section, self.in_cur), (self.section, self.also),
                          (self.group, self.also)):
            SubjectOffering.objects.create(
                term=self.term, section=sec, subject=subj, instructor=None,
                meetings_per_week=1, minutes_per_meeting=90)
        SubjectOffering.objects.filter(section=self.section, subject=self.in_cur
                                       ).update(instructor=self.prof)
        self.other = Section.objects.create(program="BSIT", year_level=4, block="E")
        SubjectOffering.objects.create(
            term=self.term, section=self.other, subject=self.also, instructor=self.prof,
            meetings_per_week=1, minutes_per_meeting=90)

    def place(self, **kw):
        args = dict(term=self.term, section=self.section, subject=self.in_cur,
                    room=self.room, day_of_week=0, start_time=time(9, 0),
                    end_time=time(10, 30), instructor=self.prof, user=self.admin)
        args.update(kw)
        return planner.place_class(**args)

    def test_place_links_the_block_to_the_existing_offering(self):
        before = SubjectOffering.objects.count()
        block, offering, created = self.place()
        self.assertFalse(created)
        self.assertEqual(SubjectOffering.objects.count(), before)
        self.assertEqual(block.offering_id, offering.pk)
        self.assertEqual(offering.instructor, self.prof)
        self.assertEqual(offering.scheduled_count, 1)

    def test_placement_never_assigns_a_professor(self):
        block, offering, _ = self.place(subject=self.also, instructor=self.prof)
        offering.refresh_from_db()
        self.assertIsNone(offering.instructor)
        self.assertIsNone(block.instructor)

    def test_extra_meeting_grows_only_that_offering(self):
        _, first, _ = self.place()
        _, second, created = self.place(day_of_week=2)
        self.assertFalse(created)
        self.assertEqual(first.pk, second.pk)
        second.refresh_from_db()
        self.assertEqual(second.meetings_per_week, 2)
        self.assertEqual(second.scheduled_count, 2)
        untouched = SubjectOffering.objects.get(section=self.section, subject=self.also)
        self.assertEqual((untouched.meetings_per_week, untouched.scheduled_count), (1, 0))

    def test_subject_without_an_offering_is_refused(self):
        before = SubjectOffering.objects.count()
        # In the curriculum but never offered to the section, and not in it at all.
        with self.assertRaisesMessage(planner.PlaceError, "Subject Offerings"):
            self.place(subject=self.outside)
        with self.assertRaisesMessage(planner.PlaceError, "Subject Offerings"):
            self.place(subject=self.in_cur, section=self.group)
        self.assertEqual(SubjectOffering.objects.count(), before)
        self.assertFalse(ScheduleBlock.objects.exists())

    def test_archived_offering_is_not_placeable(self):
        SubjectOffering.objects.filter(section=self.section, subject=self.in_cur
                                       ).update(is_active=False)
        with self.assertRaisesMessage(planner.PlaceError, "Subject Offerings"):
            self.place()

    def test_room_professor_and_section_clashes_are_refused(self):
        self.place()
        with self.assertRaisesMessage(planner.PlaceError, "Room busy"):
            self.place(subject=self.also, section=self.group, instructor=None)
        with self.assertRaisesMessage(planner.PlaceError, "Professor busy"):
            self.place(subject=self.also, room=self.room2,
                       section=self.other)
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

    def test_placeable_subjects_are_the_offerings_with_progress(self):
        self.place()
        rows, _ = planner.placeable_subjects(section=self.group, term=self.term)
        self.assertEqual([r["code"] for r in rows], ["IT402"])
        rows, _ = planner.placeable_subjects(section=self.section, term=self.term)
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
        self.offering = SubjectOffering.objects.create(
            term=self.term, section=self.section, subject=self.subject,
            meetings_per_week=1, minutes_per_meeting=90)

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
        self.offering.refresh_from_db()
        self.assertEqual(self.offering.meetings_per_week, 1)

    def test_place_api_refuses_a_subject_without_an_offering(self):
        self.client.force_login(self.admin)
        other = Subject.objects.create(code="IT499", title="Elective")
        res = self.client.post(reverse("scheduling:class_place"),
                               self.body(subject=other.pk), content_type="application/json")
        self.assertEqual(res.status_code, 422)
        self.assertFalse(ScheduleBlock.objects.exists())
        self.assertEqual(SubjectOffering.objects.count(), 1)

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


class BusySlotsTests(TestCase):
    """GET /api/scheduling/busy/: the slots the timetables grey out while a
    class is dragged or edited. It must list exactly what move/place refuse."""

    place = ManualPlacementTests.place

    def setUp(self):  # the placement fixtures, then a booked week
        ManualPlacementTests.setUp(self)
        self.client.force_login(self.admin)
        # The class being dragged: IT401 for 4D, taught by Eve, Monday 9:00 in 101.
        self.mine, _, _ = self.place()
        # Room 101 is booked Tuesday 9:00 by another block (4E, no professor).
        self.room_taken = ScheduleBlock.objects.create(
            term=self.term, room=self.room, section=self.other, title="Room user",
            day_of_week=1, start_time=time(9, 0), end_time=time(10, 30), is_active=True)
        # Eve teaches 4E on Wednesday 13:00 in another room.
        self.prof_taken = ScheduleBlock.objects.create(
            term=self.term, room=self.room2, section=self.other, instructor=self.prof,
            title="Eve elsewhere", day_of_week=2, start_time=time(13, 0),
            end_time=time(14, 30), is_active=True)
        # G1 (part of 4D) has a lab Thursday 8:00: 4D students are busy then.
        self.group_taken = ScheduleBlock.objects.create(
            term=self.term, room=self.room2, section=self.group, title="G1 lab",
            day_of_week=3, start_time=time(8, 0), end_time=time(9, 0), is_active=True)
        # Unrelated: another room, another section, no professor.
        ScheduleBlock.objects.create(
            term=self.term, room=self.room2, section=self.other, title="Unrelated",
            day_of_week=4, start_time=time(9, 0), end_time=time(10, 0), is_active=True)

    def busy(self, **params):
        res = self.client.get(reverse("scheduling:busy_slots"), params)
        self.assertEqual(res.status_code, 200, res.content)
        return {row["title"]: row for row in res.json()["busy"]}

    def test_lists_room_professor_and_section_clashes_but_not_itself(self):
        rows = self.busy(block=self.mine.pk)
        self.assertEqual(set(rows), {"Room user", "Eve elsewhere", "G1 lab"})
        self.assertEqual(rows["Room user"]["kinds"], ["room"])
        self.assertEqual(rows["Eve elsewhere"]["kinds"], ["instructor"])
        self.assertEqual(rows["G1 lab"]["kinds"], ["section"])
        self.assertEqual((rows["G1 lab"]["day"], rows["G1 lab"]["start"],
                          rows["G1 lab"]["end"]), (3, "08:00", "09:00"))

    def test_room_override_checks_the_room_picked_in_the_dialog(self):
        rows = self.busy(block=self.mine.pk, room=self.room2.pk)
        self.assertNotIn("Room user", rows)
        self.assertIn("Unrelated", rows)
        self.assertEqual(rows["Unrelated"]["kinds"], ["room"])
        # Eve's other class is in room 102 too: both reasons are named.
        self.assertEqual(rows["Eve elsewhere"]["kinds"], ["room", "instructor"])

    def test_new_class_takes_the_professor_from_the_offering(self):
        rows = self.busy(term=self.term.pk, section=self.section.pk,
                         subject=self.in_cur.pk, room=self.room.pk)
        self.assertIn("Eve elsewhere", rows)
        self.assertIn(self.mine.title, rows)   # not excluded: it is not being moved
        # IT402 for 4D has no professor yet, so only room and section count.
        rows = self.busy(term=self.term.pk, section=self.section.pk,
                         subject=self.also.pk, room=self.room.pk)
        self.assertNotIn("Eve elsewhere", rows)

    def test_grey_slots_match_what_a_move_refuses(self):
        for row in self.busy(block=self.mine.pk).values():
            start = time(*map(int, row["start"].split(":")))
            with self.assertRaises(planner.SwapError, msg=row["title"]):
                planner.move_block(self.mine, day_of_week=row["day"], start_time=start,
                                   end_time=time(start.hour + 1, start.minute))
        moved = planner.move_block(self.mine, day_of_week=5, start_time="09:00",
                                   end_time="10:30")
        self.assertEqual(moved.day_of_week, 5)

    def test_admin_only_and_needs_a_term(self):
        res = self.client.get(reverse("scheduling:busy_slots"))
        self.assertEqual(res.status_code, 422)
        guard = User.objects.create_user(username="pl_guard", email="g@school.edu",
                                         password="pw-Guard-2026", role=User.Role.GUARD)
        self.client.force_login(guard)
        res = self.client.get(reverse("scheduling:busy_slots"), {"block": self.mine.pk})
        self.assertEqual(res.status_code, 403)

    def test_both_grids_carry_the_busy_url(self):
        url = reverse("scheduling:busy_slots")
        for page in (reverse("scheduling:room_timetable") + f"?room={self.room.pk}&term={self.term.pk}",
                     reverse("scheduling:section_timetable")
                     + f"?section={self.section.pk}&term={self.term.pk}"):
            html = self.client.get(page).content.decode()
            self.assertIn(f'data-busy-url="{url}"', html)
            self.assertIn('id="cm-availability"', html)
