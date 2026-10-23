import json

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Borrower, User
from facilities.models import Building, Room
from .models import ScheduleBlock, Subject, Term
from .services import find_conflicts, find_free_rooms, get_room_timetable

SLOT = {"day": "0", "start": "09:00", "end": "10:30"}


@override_settings(ALLOWED_HOSTS=["testserver"])
class FreeRoomsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("admin1", "admin1@example.com", "x",
                                             role=User.Role.ADMIN, approval_status="APPROVED")
        cls.guard = User.objects.create_user("guard1", "guard1@example.com", "x",
                                             role=User.Role.GUARD, approval_status="APPROVED")
        cls.ccs = Building.objects.create(code="CCS", name="Computing")
        cls.eng = Building.objects.create(code="ENG", name="Engineering")
        cls.closed = Building.objects.create(code="OLD", name="Old Wing", is_active=False)
        cls.lab = Room.objects.create(building=cls.ccs, room_code="LAB1",
                                      room_type=Room.RoomType.LABORATORY, capacity=40)
        cls.small = Room.objects.create(building=cls.ccs, room_code="101", capacity=20)
        cls.eng_room = Room.objects.create(building=cls.eng, room_code="201", capacity=60)
        cls.no_cap = Room.objects.create(building=cls.eng, room_code="202")
        Room.objects.create(building=cls.ccs, room_code="FIX", status=Room.Status.MAINTENANCE)
        Room.objects.create(building=cls.ccs, room_code="OFF", is_active=False)
        Room.objects.create(building=cls.closed, room_code="X1")
        cls.api = reverse("scheduling:free_rooms_api")
        cls.page = reverse("scheduling:free_rooms")

    def codes(self, response):
        return [r["room_code"] for r in response.json()["results"]]

    # --- service stub ---
    def test_service_excludes_unbookable_rooms(self):
        rooms = find_free_rooms(day_of_week=0, start_time="09:00", end_time="10:00")
        self.assertEqual(set(rooms), {self.lab, self.small, self.eng_room, self.no_cap})

    # --- API ---
    def test_api_returns_contract_shape(self):
        self.client.force_login(self.admin)
        res = self.client.get(self.api, {**SLOT, "room_type": "LABORATORY"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"count": 1, "stub": True, "results": [{
            "id": self.lab.pk, "room_code": "LAB1", "name": "",
            "building": {"id": self.ccs.pk, "code": "CCS", "name": "Computing"},
            "room_type": "LABORATORY", "room_type_display": "Laboratory",
            "capacity": 40, "floor": None,
        }]})

    def test_api_filters_building_and_capacity(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.codes(self.client.get(self.api, {**SLOT, "building": self.ccs.pk})),
                         ["101", "LAB1"])
        # min_capacity drops rooms below it and rooms with no recorded capacity
        self.assertEqual(self.codes(self.client.get(self.api, {**SLOT, "min_capacity": 40})),
                         ["LAB1", "201"])

    def test_api_end_before_start_is_422(self):
        self.client.force_login(self.admin)
        res = self.client.get(self.api, {**SLOT, "end": "09:00"})
        self.assertEqual(res.status_code, 422)
        self.assertIn("end", res.json())

    def test_api_missing_fields_is_422(self):
        self.client.force_login(self.admin)
        res = self.client.get(self.api)
        self.assertEqual(res.status_code, 422)
        self.assertEqual(set(res.json()), {"day", "start", "end"})

    def test_api_guard_gets_403_json(self):
        self.client.force_login(self.guard)
        res = self.client.get(self.api, SLOT)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json(), {"error": "You do not have permission."})

    def test_api_anonymous_gets_403_not_redirect(self):
        res = self.client.get(self.api, SLOT)
        self.assertEqual(res.status_code, 403)

    # --- page ---
    def test_page_renders_for_admin(self):
        self.client.force_login(self.admin)
        res = self.client.get(self.page)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'id="free-room-form"')
        self.assertContains(res, self.api)

    def test_page_server_renders_results_without_js(self):
        self.client.force_login(self.admin)
        res = self.client.get(self.page, {**SLOT, "building": self.eng.pk})
        self.assertEqual(len(res.context["rooms"]), 2)
        self.assertContains(res, "2 rooms free")

    def test_schedule_list_page_renders_for_admin(self):
        self.client.force_login(self.admin)
        res = self.client.get(reverse("scheduling:schedule_list"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "id=\"schedule-list-form\"")
        self.assertContains(res, 'data-modal-open="schedule-edit-modal"')

    def test_page_forbidden_for_guard(self):
        self.client.force_login(self.guard)
        self.assertEqual(self.client.get(self.page).status_code, 403)


@override_settings(ALLOWED_HOSTS=["testserver"])
class ScheduleConflictTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            "sched_admin", "sched_admin@example.com", "x",
            role=User.Role.ADMIN, approval_status=User.Approval.APPROVED,
        )
        cls.building = Building.objects.create(code="CCS", name="Computing")
        cls.room = Room.objects.create(building=cls.building, room_code="204", capacity=30)
        cls.other_room = Room.objects.create(building=cls.building, room_code="205", capacity=30)
        cls.term = Term.objects.create(name="2026 Fall", start_date="2026-08-01", end_date="2026-12-20", is_active=True)

    def add_block(self, *, start, end, room=None, day_of_week=0, is_active=True, title="Class"):
        return ScheduleBlock.objects.create(
            term=self.term,
            room=room or self.room,
            title=title,
            day_of_week=day_of_week,
            start_time=start,
            end_time=end,
            is_active=is_active,
            created_by=self.admin,
        )

    def test_exact_same_slot_conflicts(self):
        existing_a = self.add_block(start="09:00", end="10:00", title="A")
        existing_b = self.add_block(start="09:00", end="10:00", title="B")
        self.assertEqual(
            list(find_conflicts(self.room, 0, "09:00", "10:00", self.term)),
            [existing_a, existing_b],
        )

    def test_partial_overlap_at_start_conflicts(self):
        self.add_block(start="09:00", end="10:30", title="A")
        conflicts = find_conflicts(self.room, 0, "09:30", "11:00", self.term)
        self.assertEqual(len(conflicts), 1)

    def test_partial_overlap_at_end_conflicts(self):
        self.add_block(start="09:30", end="11:00", title="A")
        conflicts = find_conflicts(self.room, 0, "09:00", "10:30", self.term)
        self.assertEqual(len(conflicts), 1)

    def test_new_block_fully_inside_conflicts(self):
        self.add_block(start="09:00", end="11:00", title="A")
        conflicts = find_conflicts(self.room, 0, "09:30", "10:30", self.term)
        self.assertEqual(len(conflicts), 1)

    def test_new_block_fully_contains_conflicts(self):
        self.add_block(start="09:30", end="10:30", title="A")
        conflicts = find_conflicts(self.room, 0, "09:00", "11:00", self.term)
        self.assertEqual(len(conflicts), 1)

    def test_touching_endpoints_do_not_conflict(self):
        existing = self.add_block(start="09:00", end="10:00", title="A")
        conflicts = find_conflicts(self.room, 0, "10:00", "11:00", self.term)
        self.assertNotIn(existing, conflicts)

    def test_different_room_does_not_conflict(self):
        existing = self.add_block(start="09:00", end="10:00", title="A")
        conflicts = find_conflicts(self.other_room, 0, "09:00", "10:00", self.term)
        self.assertNotIn(existing, conflicts)

    def test_different_day_does_not_conflict(self):
        existing = self.add_block(start="09:00", end="10:00", title="A", day_of_week=0)
        conflicts = find_conflicts(self.room, 1, "09:00", "10:00", self.term)
        self.assertNotIn(existing, conflicts)

    def test_inactive_block_does_not_conflict(self):
        existing = self.add_block(start="09:00", end="10:00", title="A", is_active=False)
        conflicts = find_conflicts(self.room, 0, "09:00", "10:00", self.term)
        self.assertNotIn(existing, conflicts)

    def test_editing_block_ignores_itself(self):
        existing = self.add_block(start="09:00", end="10:00", title="A")
        conflicts = find_conflicts(self.room, 0, "09:00", "10:00", self.term, exclude_block_id=existing.pk)
        self.assertEqual(conflicts, [])


@override_settings(ALLOWED_HOSTS=["testserver"])
class RoomTimetableTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            "tt_admin", "tt_admin@example.com", "x",
            role=User.Role.ADMIN, approval_status=User.Approval.APPROVED,
        )
        cls.guard = User.objects.create_user(
            "tt_guard", "tt_guard@example.com", "x",
            role=User.Role.GUARD, approval_status=User.Approval.APPROVED,
        )
        cls.building = Building.objects.create(code="CCS", name="Computing")
        cls.room = Room.objects.create(
            building=cls.building,
            room_code="204",
            name="Computer Lab 1",
            room_type=Room.RoomType.LABORATORY,
            capacity=30,
        )
        cls.other_room = Room.objects.create(
            building=cls.building,
            room_code="205",
            name="Classroom 2",
            room_type=Room.RoomType.CLASSROOM,
            capacity=40,
        )
        cls.term = Term.objects.create(
            name="2026 Fall",
            start_date="2026-08-01",
            end_date="2026-12-20",
            is_active=True,
        )
        cls.subject = Subject.objects.create(code="CS 101", title="Intro to CS")
        cls.borrower = Borrower.objects.create(
            id_number="FAC-001",
            first_name="Alan",
            last_name="Turing",
            borrower_type=Borrower.BorrowerType.FACULTY,
        )
        cls.block = ScheduleBlock.objects.create(
            term=cls.term,
            room=cls.room,
            subject=cls.subject,
            title="Intro to CS Lab",
            instructor=cls.borrower,
            day_of_week=0,  # Monday
            start_time="09:00",
            end_time="10:30",
            is_active=True,
            created_by=cls.admin,
        )
        cls.url = reverse("scheduling:room_timetable")
        cls.conflict_api = reverse("scheduling:conflict_check")
        cls.save_api = reverse("scheduling:schedule_block_save")

    def test_timetable_service_geometry_and_data(self):
        result = get_room_timetable(self.room, self.term)
        self.assertEqual(result["total_blocks"], 1)
        self.assertEqual(result["total_hours"], 1.5)
        block_data = result["blocks"][0]
        self.assertEqual(block_data["id"], self.block.pk)
        self.assertEqual(block_data["day_of_week"], 0)
        self.assertEqual(block_data["col"], 2)  # Monday is column 2
        self.assertEqual(block_data["row_start"], 5)  # 09:00 is slot 4 -> row 5
        self.assertEqual(block_data["row_end"], 8)  # 10:30 is slot 7 -> row 8
        self.assertEqual(block_data["span"], 3)
        self.assertEqual(block_data["subject_code"], "CS 101")
        self.assertEqual(block_data["instructor_name"], "Alan Turing")
        self.assertIn("bg", block_data["palette"])

    def test_timetable_service_none_handling(self):
        result = get_room_timetable(None, None)
        self.assertEqual(result["blocks"], [])
        self.assertEqual(result["total_blocks"], 0)
        self.assertEqual(result["total_hours"], 0.0)

    def test_timetable_page_renders_for_admin(self):
        self.client.force_login(self.admin)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Room Timetable")
        self.assertContains(res, "data-schedule-grid")
        self.assertContains(res, "Mon")
        self.assertContains(res, "Sat")
        self.assertContains(res, "07:00")
        self.assertContains(res, "20:30")
        self.assertContains(res, "Intro to CS Lab")
        self.assertContains(res, "CS 101")
        self.assertContains(res, "09:00 – 10:30")
        self.assertContains(res, "@media print")
        self.assertContains(res, "Print timetable")
        self.assertContains(res, 'id="schedule-form-modal"')

    def test_timetable_page_filters_by_room_and_term(self):
        self.client.force_login(self.admin)
        res = self.client.get(self.url, {"room": self.other_room.pk, "term": self.term.pk})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["selected_room"], self.other_room)
        self.assertEqual(res.context["selected_term"], self.term)
        self.assertEqual(len(res.context["timetable"]["blocks"]), 0)

    def test_timetable_page_forbidden_for_guard(self):
        self.client.force_login(self.guard)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 403)

    def test_timetable_page_redirect_for_anonymous(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 302)

    def test_conflict_api_free_slot(self):
        self.client.force_login(self.admin)
        res = self.client.post(
            self.conflict_api,
            json.dumps({
                "room": self.room.pk,
                "term": self.term.pk,
                "day_of_week": 0,
                "start_time": "11:00",
                "end_time": "12:30",
            }),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["available"])
        self.assertEqual(data["conflicts"], [])

    def test_conflict_api_overlapping_slot(self):
        self.client.force_login(self.admin)
        res = self.client.post(
            self.conflict_api,
            json.dumps({
                "room": self.room.pk,
                "term": self.term.pk,
                "day_of_week": 0,
                "start_time": "09:30",
                "end_time": "11:00",
            }),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertFalse(data["available"])
        self.assertEqual(len(data["conflicts"]), 1)
        self.assertEqual(data["conflicts"][0]["title"], "Intro to CS Lab")

    def test_conflict_api_end_before_start(self):
        self.client.force_login(self.admin)
        res = self.client.post(
            self.conflict_api,
            json.dumps({
                "room": self.room.pk,
                "term": self.term.pk,
                "day_of_week": 0,
                "start_time": "11:00",
                "end_time": "10:00",
            }),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertFalse(data["available"])
        self.assertIn("clientError", data)

    def test_conflict_api_guard_gets_403(self):
        self.client.force_login(self.guard)
        res = self.client.post(
            self.conflict_api,
            json.dumps({
                "room": self.room.pk,
                "term": self.term.pk,
                "day_of_week": 0,
                "start_time": "11:00",
                "end_time": "12:00",
            }),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 403)

    def test_save_block_api_create_and_delete(self):
        self.client.force_login(self.admin)
        # Create
        res = self.client.post(
            self.save_api,
            json.dumps({
                "room": self.room.pk,
                "term": self.term.pk,
                "title": "Math 101",
                "day_of_week": 1,  # Tuesday
                "start_time": "13:00",
                "end_time": "14:30",
                "is_active": True,
            }),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 201)
        block_id = res.json()["block"]["id"]
        self.assertTrue(ScheduleBlock.objects.filter(pk=block_id).exists())

        # Update
        res_update = self.client.post(
            self.save_api,
            json.dumps({
                "block_id": block_id,
                "room": self.room.pk,
                "term": self.term.pk,
                "title": "Math 101 Advanced",
                "day_of_week": 1,
                "start_time": "13:00",
                "end_time": "14:30",
                "is_active": True,
            }),
            content_type="application/json",
        )
        self.assertEqual(res_update.status_code, 200)
        self.assertEqual(ScheduleBlock.objects.get(pk=block_id).title, "Math 101 Advanced")

        # Delete
        delete_url = reverse("scheduling:schedule_block_delete", kwargs={"pk": block_id})
        res_del = self.client.post(delete_url)
        self.assertEqual(res_del.status_code, 200)
        self.assertFalse(ScheduleBlock.objects.filter(pk=block_id).exists())

    def test_save_block_api_conflict_returns_422(self):
        self.client.force_login(self.admin)
        res = self.client.post(
            self.save_api,
            json.dumps({
                "room": self.room.pk,
                "term": self.term.pk,
                "title": "Conflicting Class",
                "day_of_week": 0,  # Monday
                "start_time": "09:30",
                "end_time": "10:00",
                "is_active": True,
            }),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 422)

    def test_empty_state_without_rooms(self):
        self.client.force_login(self.admin)
        Room.objects.update(is_active=False)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "No rooms available")


from datetime import datetime, time
from django.utils import timezone
from .models import ScheduleException
from .services import get_room_today_schedule, get_current_occupant


@override_settings(ALLOWED_HOSTS=["testserver"])
class TodayInRoomServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            "admin_test", "admin_test@example.com", "x",
            role=User.Role.ADMIN, approval_status=User.Approval.APPROVED,
        )
        cls.building = Building.objects.create(code="SCI", name="Science")
        cls.room = Room.objects.create(building=cls.building, room_code="101", capacity=30)
        cls.other_room = Room.objects.create(building=cls.building, room_code="102", capacity=30)
        cls.term = Term.objects.create(
            name="2026 Fall Test",
            start_date="2026-08-01",
            end_date="2026-12-20",
            is_active=True,
        )
        cls.instructor = Borrower.objects.create(
            first_name="Marie",
            last_name="Curie",
            id_number="FAC-001",
            borrower_type=Borrower.BorrowerType.FACULTY,
        )
        cls.subject = Subject.objects.create(code="PHY 101", title="Physics I")

    def test_empty_schedule_returns_free_all_day(self):
        when = timezone.make_aware(datetime(2026, 8, 3, 10, 0))  # Monday
        schedule = get_room_today_schedule(self.room, when=when)
        self.assertFalse(schedule["has_schedule_today"])
        self.assertFalse(schedule["is_occupied"])
        self.assertTrue(schedule["all_day_free"])
        self.assertIsNone(schedule["current_block"])
        self.assertIsNone(schedule["next_block"])

    def test_current_block_in_session(self):
        block = ScheduleBlock.objects.create(
            term=self.term,
            room=self.room,
            subject=self.subject,
            title="Physics Lecture",
            instructor=self.instructor,
            day_of_week=0,  # Monday
            start_time=time(9, 0),
            end_time=time(11, 0),
            is_active=True,
        )
        when = timezone.make_aware(datetime(2026, 8, 3, 10, 0))  # Monday 10:00
        schedule = get_room_today_schedule(self.room, when=when)

        self.assertTrue(schedule["has_schedule_today"])
        self.assertTrue(schedule["is_occupied"])
        self.assertIsNotNone(schedule["current_block"])
        self.assertEqual(schedule["current_block"]["id"], block.pk)
        self.assertEqual(schedule["current_block"]["instructor_name"], "Marie Curie")
        self.assertEqual(schedule["current_block"]["time_remaining_min"], 60)
        self.assertIsNone(schedule["next_block"])

    def test_next_block_upcoming(self):
        block = ScheduleBlock.objects.create(
            term=self.term,
            room=self.room,
            subject=self.subject,
            title="Physics Afternoon Lab",
            instructor=self.instructor,
            day_of_week=0,  # Monday
            start_time=time(14, 0),
            end_time=time(16, 0),
            is_active=True,
        )
        when = timezone.make_aware(datetime(2026, 8, 3, 10, 0))  # Monday 10:00
        schedule = get_room_today_schedule(self.room, when=when)

        self.assertTrue(schedule["has_schedule_today"])
        self.assertFalse(schedule["is_occupied"])
        self.assertIsNone(schedule["current_block"])
        self.assertIsNotNone(schedule["next_block"])
        self.assertEqual(schedule["next_block"]["id"], block.pk)
        self.assertEqual(schedule["next_block"]["time_until_start_min"], 240)

    def test_both_current_and_next_block(self):
        b1 = ScheduleBlock.objects.create(
            term=self.term,
            room=self.room,
            title="Morning Class",
            day_of_week=0,
            start_time=time(9, 0),
            end_time=time(11, 0),
            is_active=True,
        )
        b2 = ScheduleBlock.objects.create(
            term=self.term,
            room=self.room,
            title="Afternoon Class",
            day_of_week=0,
            start_time=time(13, 0),
            end_time=time(15, 0),
            is_active=True,
        )
        when = timezone.make_aware(datetime(2026, 8, 3, 10, 0))  # Monday 10:00
        schedule = get_room_today_schedule(self.room, when=when)

        self.assertTrue(schedule["is_occupied"])
        self.assertEqual(schedule["current_block"]["id"], b1.pk)
        self.assertEqual(schedule["next_block"]["id"], b2.pk)

    def test_past_blocks_done_for_day(self):
        ScheduleBlock.objects.create(
            term=self.term,
            room=self.room,
            title="Early Class",
            day_of_week=0,
            start_time=time(8, 0),
            end_time=time(9, 30),
            is_active=True,
        )
        when = timezone.make_aware(datetime(2026, 8, 3, 10, 0))  # Monday 10:00
        schedule = get_room_today_schedule(self.room, when=when)

        self.assertTrue(schedule["has_schedule_today"])
        self.assertFalse(schedule["is_occupied"])
        self.assertIsNone(schedule["current_block"])
        self.assertIsNone(schedule["next_block"])
        self.assertTrue(schedule["done_for_day"])

    def test_schedule_exception_cancelled_excluded(self):
        block = ScheduleBlock.objects.create(
            term=self.term,
            room=self.room,
            title="Cancelled Class",
            day_of_week=0,
            start_time=time(9, 0),
            end_time=time(11, 0),
            is_active=True,
        )
        ScheduleException.objects.create(
            schedule_block=block,
            date=datetime(2026, 8, 3).date(),
            exception_type=ScheduleException.ExceptionType.CANCELLED,
            reason="Holiday",
        )
        when = timezone.make_aware(datetime(2026, 8, 3, 10, 0))
        schedule = get_room_today_schedule(self.room, when=when)

        self.assertFalse(schedule["has_schedule_today"])
        self.assertFalse(schedule["is_occupied"])
        self.assertIsNone(schedule["current_block"])

    def test_schedule_exception_room_change_to_this_room(self):
        block = ScheduleBlock.objects.create(
            term=self.term,
            room=self.other_room,  # originally in other_room
            title="Moved In Class",
            day_of_week=0,
            start_time=time(9, 0),
            end_time=time(11, 0),
            is_active=True,
        )
        ScheduleException.objects.create(
            schedule_block=block,
            date=datetime(2026, 8, 3).date(),
            exception_type=ScheduleException.ExceptionType.ROOM_CHANGE,
            replacement_room=self.room,  # moved to self.room
            reason="Lab renovation",
        )
        when = timezone.make_aware(datetime(2026, 8, 3, 10, 0))
        schedule = get_room_today_schedule(self.room, when=when)

        self.assertTrue(schedule["has_schedule_today"])
        self.assertTrue(schedule["is_occupied"])
        self.assertEqual(schedule["current_block"]["id"], block.pk)

    def test_schedule_exception_room_change_away_from_this_room(self):
        block = ScheduleBlock.objects.create(
            term=self.term,
            room=self.room,
            title="Moved Away Class",
            day_of_week=0,
            start_time=time(9, 0),
            end_time=time(11, 0),
            is_active=True,
        )
        ScheduleException.objects.create(
            schedule_block=block,
            date=datetime(2026, 8, 3).date(),
            exception_type=ScheduleException.ExceptionType.ROOM_CHANGE,
            replacement_room=self.other_room,
            reason="Relocated",
        )
        when = timezone.make_aware(datetime(2026, 8, 3, 10, 0))
        schedule = get_room_today_schedule(self.room, when=when)

        self.assertFalse(schedule["has_schedule_today"])
        self.assertFalse(schedule["is_occupied"])

    def test_get_current_occupant(self):
        block = ScheduleBlock.objects.create(
            term=self.term,
            room=self.room,
            title="Class",
            instructor=self.instructor,
            day_of_week=0,
            start_time=time(9, 0),
            end_time=time(11, 0),
            is_active=True,
        )
        when = timezone.make_aware(datetime(2026, 8, 3, 10, 0))
        occupant = get_current_occupant(self.room, when=when)
        self.assertEqual(occupant, self.instructor)

        when_free = timezone.make_aware(datetime(2026, 8, 3, 12, 0))
        self.assertIsNone(get_current_occupant(self.room, when=when_free))


@override_settings(ALLOWED_HOSTS=["testserver"])
class RoomTodayScheduleApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            "admin_api", "admin_api@example.com", "x",
            role=User.Role.ADMIN, approval_status=User.Approval.APPROVED,
        )
        cls.guard = User.objects.create_user(
            "guard_api", "guard_api@example.com", "x",
            role=User.Role.GUARD, approval_status=User.Approval.APPROVED,
        )
        cls.building = Building.objects.create(code="CCS", name="Computing")
        cls.room = Room.objects.create(building=cls.building, room_code="301", capacity=40)
        cls.url = reverse("scheduling:room_today_schedule_api", kwargs={"pk": cls.room.pk})

    def test_guard_can_access_api(self):
        self.client.force_login(self.guard)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["room_id"], self.room.pk)
        self.assertEqual(data["room_code"], "301")
        self.assertEqual(data["building_code"], "CCS")
        self.assertIn("html", data)
        self.assertIn("today-in-room-widget", data["html"])

    def test_admin_can_access_api(self):
        self.client.force_login(self.admin)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("is_occupied", data)
        self.assertIn("current_block", data)
        self.assertIn("next_block", data)

    def test_anonymous_gets_403(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 403)

    def test_nonexistent_room_returns_404(self):
        self.client.force_login(self.guard)
        missing_url = reverse("scheduling:room_today_schedule_api", kwargs={"pk": 99999})
        res = self.client.get(missing_url)
        self.assertEqual(res.status_code, 404)
