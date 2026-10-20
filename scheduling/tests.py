from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from facilities.models import Building, Room
from .models import ScheduleBlock, Term
from .services import find_conflicts, find_free_rooms

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
