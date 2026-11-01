"""Tests for the building / room / guard-post admin screens (B2.5, B3.1-B3.3, F4.1, F4.2)."""
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Borrower, User
from keys.models import Key, KeyCategory
from transactions.models import GuardShift
from transactions.services import pos_borrow

from .models import Building, GuardPost, Room

MODAL = "?modal=1"


@override_settings(ALLOWED_HOSTS=["testserver"])
class FacilitiesAdminTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin", email="a@example.com", password="pw-Strong-12345",
            role=User.Role.ADMIN, approval_status=User.Approval.APPROVED)
        self.client.force_login(self.admin)
        self.ccs = Building.objects.create(code="CCS", name="Computing")
        self.eng = Building.objects.create(code="ENG", name="Engineering")
        self.room = Room.objects.create(building=self.ccs, room_code="101")

    def room_data(self, **kw):
        data = {"building": self.ccs.pk, "room_code": "101", "name": "", "room_type": "CLASSROOM",
                "floor": "", "capacity": "", "status": "AVAILABLE", "is_active": "on"}
        data.update(kw)
        return data

    def test_duplicate_room_code_is_a_field_error_not_a_500(self):
        res = self.client.post(reverse("facilities:room_create") + MODAL, self.room_data())
        self.assertEqual(res.status_code, 422)
        self.assertContains(res, "already exists in CCS", status_code=422)

    def test_same_code_in_another_building_is_fine(self):
        res = self.client.post(reverse("facilities:room_create") + MODAL,
                               self.room_data(building=self.eng.pk))
        self.assertEqual(res.status_code, 201)
        self.assertTrue(Room.objects.filter(building=self.eng, room_code="101").exists())

    def test_bulk_create_skips_existing_codes(self):
        res = self.client.post(reverse("facilities:room_bulk_create") + MODAL, {
            "building": self.ccs.pk, "prefix": "", "start_number": 100, "end_number": 103,
            "room_type": "CLASSROOM"})
        self.assertEqual(res.status_code, 201)
        self.assertIn("Created 3", res.content.decode())
        self.assertIn("Skipped 1", res.content.decode())
        self.assertEqual(Room.objects.filter(building=self.ccs).count(), 4)

    def test_bulk_create_rejects_reversed_range(self):
        res = self.client.post(reverse("facilities:room_bulk_create") + MODAL, {
            "building": self.ccs.pk, "start_number": 110, "end_number": 101,
            "room_type": "CLASSROOM"})
        self.assertEqual(res.status_code, 422)

    def test_toggles_are_post_only_and_refuse_while_keys_out(self):
        guard = User.objects.create_user(username="g", email="g@example.com",
                                         password="pw-Strong-12345", role=User.Role.GUARD,
                                         approval_status=User.Approval.APPROVED)
        GuardShift.objects.create(guard=guard, post="Main Desk")
        key = Key.objects.create(key_code="CCS-101-1", room=self.room,
                                 category=KeyCategory.objects.create(name="Room"))
        borrower = Borrower.objects.create(id_number="F-1", first_name="A", last_name="B",
                                           borrower_type="FACULTY")
        pos_borrow(key_id=key.pk, borrower=borrower, guard=guard)

        self.assertEqual(self.client.get(reverse("facilities:room_toggle", args=[self.room.pk])).status_code, 405)
        self.client.post(reverse("facilities:room_toggle", args=[self.room.pk]))
        self.client.post(reverse("facilities:building_toggle", args=[self.ccs.pk]))
        self.room.refresh_from_db()
        self.ccs.refresh_from_db()
        self.assertTrue(self.room.is_active)
        self.assertTrue(self.ccs.is_active)

        # An empty building deactivates (soft) and stays in the database.
        self.client.post(reverse("facilities:building_toggle", args=[self.eng.pk]))
        self.eng.refresh_from_db()
        self.assertFalse(self.eng.is_active)

    def test_guard_post_crud(self):
        res = self.client.post(reverse("facilities:post_create") + MODAL,
                               {"name": "Main Gate", "building": self.ccs.pk, "is_active": "on"})
        self.assertEqual(res.status_code, 201)
        post = GuardPost.objects.get(name="Main Gate")
        self.assertContains(self.client.get(reverse("facilities:post_list")), "Main Gate")
        self.client.post(reverse("facilities:post_toggle", args=[post.pk]))
        post.refresh_from_db()
        self.assertFalse(post.is_active)

    def test_list_pages_render(self):
        for name in ("building_list", "room_list", "post_list"):
            self.assertEqual(self.client.get(reverse(f"facilities:{name}")).status_code, 200)
        res = self.client.get(reverse("facilities:room_list"), {"building": self.ccs.pk})
        self.assertContains(res, "CCS-101")
