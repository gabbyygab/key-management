from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from facilities.models import Building, Room

from .services import find_free_rooms

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

    def test_page_forbidden_for_guard(self):
        self.client.force_login(self.guard)
        self.assertEqual(self.client.get(self.page).status_code, 403)
