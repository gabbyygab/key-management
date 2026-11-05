"""Room finder for a subject (scheduling/room_finder.py, the subject tab)."""
from datetime import time

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Borrower, Department, User
from facilities.models import Building, Room
from scheduling import planner
from scheduling.models import ScheduleBlock, Section, Subject, SubjectOffering
from scheduling.room_finder import find_rooms_for_offering
from scheduling.test_planner import mk_term


def windows(result, room, day=0):
    """``[("09:00", "12:00"), ...]`` for one room on one day."""
    for r in result.rooms:
        if r.room == room:
            return [(w["start"], w["end"]) for w in r.windows if w["day"] == day]
    return None


@override_settings(ALLOWED_HOSTS=["testserver"])
class RoomFinderTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("rf_admin", "rf_admin@x.edu", "x",
                                              role=User.Role.ADMIN)
        self.guard = User.objects.create_user("rf_guard", "rf_guard@x.edu", "x",
                                              role=User.Role.GUARD)
        self.term = mk_term()
        self.ccs = Department.objects.create(code="CCS", name="Computing")
        self.coe = Department.objects.create(code="COE", name="Engineering")
        b = Building.objects.create(code="SCI", name="Science")
        mk = lambda code, **kw: Room.objects.create(building=b, room_code=code, **kw)
        self.big = mk("101", capacity=60)
        self.fit = mk("102", capacity=40)
        self.small = mk("103", capacity=20)
        self.nocap = mk("104")
        self.lab = mk("LAB1", room_type=Room.RoomType.LABORATORY, capacity=40)
        self.ccs_room = mk("105", capacity=45, department=self.ccs)
        self.coe_room = mk("106", capacity=45, department=self.coe)
        self.office = mk("OFF", room_type=Room.RoomType.OFFICE, capacity=50)
        self.broken = mk("107", capacity=50, status=Room.Status.MAINTENANCE)

        self.block = Section.objects.create(program="BSIT", year_level=4, block="D",
                                            student_count=35, department=self.ccs)
        self.g1 = Section.objects.create(program="BSIT", year_level=4, block="D", group="G1",
                                         student_count=18, department=self.ccs)
        self.g2 = Section.objects.create(program="BSIT", year_level=4, block="D", group="G2",
                                         student_count=17, department=self.ccs)
        self.prof = Borrower.objects.create(id_number="P-1", first_name="Eve", last_name="Cruz",
                                            borrower_type=Borrower.BorrowerType.FACULTY)
        self.subject = Subject.objects.create(code="IT401", title="Capstone")
        self.offering = SubjectOffering.objects.create(
            term=self.term, subject=self.subject, section=self.block, instructor=self.prof,
            meetings_per_week=2, minutes_per_meeting=90)
        self.api = reverse("scheduling:room_finder_api")
        self.page = reverse("scheduling:free_rooms")

    def block_at(self, room, start, end, *, day=0, section=None, instructor=None,
                 term=None, is_active=True):
        return ScheduleBlock.objects.create(
            term=term or self.term, room=room, title="Other class", section=section,
            instructor=instructor, day_of_week=day, start_time=start, end_time=end,
            is_active=is_active)

    def find(self, offering=None, **kw):
        return find_rooms_for_offering(offering=offering or self.offering, **kw)

    # --- which rooms -------------------------------------------------------
    def test_rooms_follow_type_seats_and_department_rules(self):
        rooms = [r.room for r in self.find().rooms]
        # Own department first, then shared, smallest fit first, unknown size last.
        # No room type on the offering = any teaching room, labs included
        # (planner.TEACHING_ROOM_TYPES), exactly as the auto-scheduler reads it.
        self.assertEqual(rooms, [self.ccs_room, self.fit, self.lab, self.big, self.nocap])
        for excluded in (self.small, self.coe_room, self.office, self.broken):
            self.assertNotIn(excluded, rooms)

    def test_hidden_counts_explain_every_other_room(self):
        hidden = {h["key"]: h["count"] for h in self.find().hidden}
        self.assertEqual(hidden, {"unavailable": 1, "wrong_type": 1, "too_small": 1,
                                  "other_department": 1})

    def test_whole_block_without_count_adds_up_groups(self):
        self.block.student_count = None
        self.block.save()
        self.assertEqual(self.find().requirement.seats, 35)

    def test_lecture_lab_asks_for_a_classroom_then_a_laboratory(self):
        self.offering.meeting_format = SubjectOffering.MeetingFormat.LECTURE_LAB
        self.offering.lab_minutes_per_meeting = 180
        self.offering.save()
        lecture = self.find(kind="LECTURE")
        self.assertNotIn(self.lab, [r.room for r in lecture.rooms])
        self.assertEqual(lecture.requirement.minutes, 90)
        lab = self.find(kind="LAB")
        self.assertEqual([r.room for r in lab.rooms], [self.lab])
        self.assertEqual(lab.requirement.minutes, 180)

    def test_offering_room_type_limits_the_rooms(self):
        self.offering.room_type = Room.RoomType.CLASSROOM
        self.offering.save()
        self.assertNotIn(self.lab, [r.room for r in self.find().rooms])

    def test_preferred_room_is_listed_first(self):
        self.offering.preferred_room = self.big
        self.offering.save()
        result = self.find()
        self.assertEqual(result.rooms[0].room, self.big)
        self.assertEqual(result.rooms[0].fit, "preferred")

    def test_building_filter(self):
        other = Building.objects.create(code="ENG", name="Eng")
        far = Room.objects.create(building=other, room_code="201", capacity=40)
        result = self.find(building=other)
        self.assertEqual([r.room for r in result.rooms], [far])

    # --- when ---------------------------------------------------------------
    def test_free_room_has_the_whole_day(self):
        self.assertEqual(windows(self.find(), self.fit), [("07:00", "21:00")])

    def test_room_booking_splits_the_day_and_touching_is_fine(self):
        self.block_at(self.fit, time(9, 0), time(10, 30))
        self.assertEqual(windows(self.find(), self.fit),
                         [("07:00", "09:00"), ("10:30", "21:00")])
        # Another room is untouched by it.
        self.assertEqual(windows(self.find(), self.big), [("07:00", "21:00")])

    def test_gap_shorter_than_the_meeting_is_not_a_window(self):
        self.block_at(self.fit, time(7, 0), time(9, 0))
        self.block_at(self.fit, time(10, 0), time(21, 0))
        self.assertEqual(windows(self.find(), self.fit, day=0), [])
        result = self.find(days=[0])
        self.assertIn(self.fit, [r.room for r in result.full_rooms])

    def test_off_grid_booking_snaps_to_half_hours(self):
        self.block_at(self.fit, time(7, 0), time(9, 15))
        self.assertEqual(windows(self.find(), self.fit), [("09:30", "21:00")])

    def test_section_busy_elsewhere_blocks_every_room(self):
        self.block_at(self.big, time(13, 0), time(14, 30), section=self.block)
        for room in (self.fit, self.ccs_room):
            self.assertEqual(windows(self.find(), room),
                             [("07:00", "13:00"), ("14:30", "21:00")])

    def test_group_class_blocks_the_whole_block_but_not_a_sibling(self):
        self.block_at(self.big, time(8, 30), time(10, 0), section=self.g2)
        self.assertEqual(windows(self.find(), self.fit),
                         [("07:00", "08:30"), ("10:00", "21:00")])
        g1_offering = SubjectOffering.objects.create(
            term=self.term, subject=Subject.objects.create(code="IT402", title="Net"),
            section=self.g1, meetings_per_week=1, minutes_per_meeting=90)
        # G2's class does not touch G1's week.
        self.assertEqual(windows(self.find(g1_offering), self.fit), [("07:00", "21:00")])

    def test_professor_busy_elsewhere_blocks(self):
        self.block_at(self.big, time(15, 0), time(16, 0), instructor=self.prof)
        self.assertEqual(windows(self.find(), self.fit),
                         [("07:00", "15:00"), ("16:00", "21:00")])

    def test_other_term_and_inactive_blocks_do_not_count(self):
        self.block_at(self.fit, time(9, 0), time(12, 0), term=mk_term("Other term"))
        self.block_at(self.fit, time(13, 0), time(15, 0), is_active=False)
        self.assertEqual(windows(self.find(), self.fit), [("07:00", "21:00")])

    def test_days_and_hours_narrow_the_search(self):
        result = self.find(days=[2, 4], window_start=time(13, 0), window_end=time(17, 0))
        self.assertEqual([d["day"] for d in result.days], [2, 4])
        self.assertEqual(windows(result, self.fit, day=2), [("13:00", "17:00")])
        self.assertEqual(windows(result, self.fit, day=0), [])

    def test_every_window_is_accepted_by_place_class(self):
        """The finder and the placement rules must agree."""
        self.block_at(self.fit, time(7, 0), time(8, 0))
        self.block_at(self.big, time(10, 0), time(11, 0), section=self.block)
        self.block_at(self.big, time(13, 0), time(14, 0), instructor=self.prof)
        result = self.find(days=[0])
        for r in result.rooms:
            for w in r.windows:
                with self.subTest(room=str(r.room), start=w["start"]):
                    start = time.fromisoformat(w["start"])
                    end = time.fromisoformat(w["book_end"])
                    blockers = planner.slot_blockers(
                        term=self.term, section=self.block, instructor=self.prof, room=r.room,
                        day_of_week=0, start_time=start, end_time=end)
                    self.assertEqual(blockers, {})
        earliest = next(r for r in result.rooms if r.room == self.fit).earliest
        block, _, _ = planner.place_class(
            term=self.term, section=self.block, subject=self.subject, room=self.fit,
            day_of_week=0, start_time=time.fromisoformat(earliest["start"]),
            end_time=time.fromisoformat(earliest["book_end"]))
        self.assertEqual(block.start_time, time(8, 0))

    def test_placed_lab_shows_a_notice(self):
        self.offering.meeting_format = SubjectOffering.MeetingFormat.LECTURE_LAB
        self.offering.save()
        ScheduleBlock.objects.create(
            term=self.term, room=self.lab, title="lab", section=self.block,
            offering=self.offering, meeting_kind="LAB", day_of_week=1,
            start_time=time(9, 0), end_time=time(10, 30))
        self.assertIn("already on the timetable", self.find(kind="LAB").requirement.notice)
        self.assertEqual(self.find(kind="LECTURE").requirement.notice, "")

    # --- HTTP ---------------------------------------------------------------
    def test_api_returns_rooms_and_rendered_html(self):
        self.client.force_login(self.admin)
        res = self.client.get(self.api, {"offering": self.offering.pk, "term": self.term.pk,
                                         "days": ["0"]})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["count"], 5)
        self.assertEqual(body["requirement"]["seats"], 35)
        self.assertEqual(body["rooms"][0]["windows"], [{"day": 0, "start": "07:00", "end": "21:00"}])
        self.assertIn("5 rooms can take it", body["html"])
        self.assertIn("data-finder-slot", body["html"])

    def test_api_rejects_a_window_shorter_than_the_meeting(self):
        self.client.force_login(self.admin)
        res = self.client.get(self.api, {"offering": self.offering.pk, "term": self.term.pk,
                                         "window_start": "09:00", "window_end": "10:00"})
        self.assertEqual(res.status_code, 422)
        self.assertIn("window_end", res.json())

    def test_api_rejects_an_offering_from_another_term(self):
        self.client.force_login(self.admin)
        other = mk_term("Next term")
        res = self.client.get(self.api, {"offering": self.offering.pk, "term": other.pk})
        self.assertEqual(res.status_code, 422)
        self.assertIn("offering", res.json())

    def test_api_guard_gets_403(self):
        self.client.force_login(self.guard)
        res = self.client.get(self.api, {"offering": self.offering.pk})
        self.assertEqual(res.status_code, 403)

    def test_page_defaults_to_the_subject_tab(self):
        self.client.force_login(self.admin)
        res = self.client.get(self.page, {"term": self.term.pk})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'id="room-finder-form"')
        self.assertContains(res, "IT401 · BSIT-4D (G1 + G2 together)")
        self.assertContains(res, "Pick a subject to see the rooms that fit it")

    def test_page_server_renders_subject_results(self):
        self.client.force_login(self.admin)
        res = self.client.get(self.page, {"term": self.term.pk, "offering": self.offering.pk})
        self.assertContains(res, "5 rooms can take it")
        self.assertContains(res, "SCI-105")

    # --- pagination: 4 rooms a page ----------------------------------------
    def cards(self, html):
        return html.count("Schedule earliest")

    def test_rooms_are_paged_four_at_a_time(self):
        self.client.force_login(self.admin)
        q = {"term": self.term.pk, "offering": self.offering.pk}
        first = self.client.get(self.page, q)
        self.assertEqual(self.cards(first.content.decode()), 4)
        self.assertContains(first, "5 rooms can take it")     # headline covers every page
        self.assertContains(first, "of <span class=\"font-semibold text-stone-800\">5</span> rooms")
        self.assertNotContains(first, "Rows per page")        # fixed size, no picker
        second = self.client.get(self.page, {**q, "page": 2})
        self.assertEqual(self.cards(second.content.decode()), 1)
        self.assertContains(second, "SCI-104")                 # unknown capacity sorts last

    def test_page_links_keep_the_search(self):
        self.client.force_login(self.admin)
        res = self.client.get(self.api, {"term": self.term.pk, "offering": self.offering.pk,
                                         "days": ["2"], "kind": ""})
        html = res.json()["html"]
        self.assertIn("page=2", html)
        self.assertIn(f"offering={self.offering.pk}", html)
        self.assertIn("days=2", html)

    def test_api_pages_and_ignores_per_page(self):
        self.client.force_login(self.admin)
        q = {"term": self.term.pk, "offering": self.offering.pk, "per_page": "50"}
        body = self.client.get(self.api, q).json()
        self.assertEqual((body["count"], body["page"], body["num_pages"]), (5, 1, 2))
        self.assertEqual(len(body["rooms"]), 4)
        body = self.client.get(self.api, {**q, "page": "99"}).json()   # clamps
        self.assertEqual((body["page"], len(body["rooms"])), (2, 1))

    def test_page_time_tab(self):
        self.client.force_login(self.admin)
        res = self.client.get(self.page, {"mode": "time"})
        self.assertContains(res, 'id="free-room-form"')
        # The day chips come from the finder form, not the schedule-block form.
        self.assertContains(res, 'name="day" value="0"')
