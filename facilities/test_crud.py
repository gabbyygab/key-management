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
            role=User.Role.ADMIN)
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

    def test_adding_a_room_mints_its_original_key(self):
        """Adding a room creates exactly one key (no copies) so the Keys page
        has something to hand over right away."""
        res = self.client.post(reverse("facilities:room_create") + MODAL,
                               self.room_data(building=self.eng.pk, room_code="201"))
        self.assertEqual(res.status_code, 201)
        room = Room.objects.get(building=self.eng, room_code="201")
        keys = Key.objects.filter(room=room)
        self.assertEqual(keys.count(), 1)
        key = keys.get()
        self.assertEqual(key.copy_number, 1)
        self.assertEqual(key.key_code, "ENG-201-1")
        self.assertEqual(key.status, Key.Status.AVAILABLE)
        self.assertEqual(key.created_by, self.admin)
        self.assertIn("ENG-201-1", res.content.decode())
        # It is visible on the Keys page, not just in the database.
        page = self.client.get(reverse("keys:key_list"))
        self.assertContains(page, "ENG-201-1")

    def test_room_key_uses_office_category_for_an_office(self):
        KeyCategory.objects.create(name="Room")
        office = KeyCategory.objects.create(name="Office")
        res = self.client.post(reverse("facilities:room_create") + MODAL,
                               self.room_data(building=self.eng.pk, room_code="202",
                                              room_type="OFFICE"))
        self.assertEqual(res.status_code, 201)
        key = Key.objects.get(room__room_code="202")
        self.assertEqual(key.category, office)

    def test_archived_room_does_not_get_a_key(self):
        data = self.room_data(building=self.eng.pk, room_code="203")
        data.pop("is_active")
        res = self.client.post(reverse("facilities:room_create") + MODAL, data)
        self.assertEqual(res.status_code, 201)
        self.assertFalse(Key.objects.filter(room__room_code="203").exists())

    def test_bulk_create_skips_existing_codes(self):
        res = self.client.post(reverse("facilities:room_bulk_create") + MODAL, {
            "building": self.ccs.pk, "prefix": "", "start_number": 100, "end_number": 103,
            "room_type": "CLASSROOM"})
        self.assertEqual(res.status_code, 201)
        self.assertIn("Created 3", res.content.decode())
        self.assertIn("Skipped 1", res.content.decode())
        self.assertEqual(Room.objects.filter(building=self.ccs).count(), 4)
        for code in ["100", "102", "103"]:
            r = Room.objects.get(building=self.ccs, room_code=code)
            self.assertTrue(r.keys.exists(), f"Room {code} should have minted a key")
            self.assertEqual(r.keys.first().key_code, f"CCS-{code}-1")

    def test_room_generate_key(self):
        room_without_key = Room.objects.create(building=self.ccs, room_code="999", room_type="OFFICE")
        self.assertFalse(room_without_key.keys.exists())
        res = self.client.post(reverse("facilities:room_generate_key", args=[room_without_key.pk]))
        self.assertEqual(res.status_code, 302)
        room_without_key.refresh_from_db()
        self.assertTrue(room_without_key.keys.exists())
        self.assertEqual(room_without_key.keys.first().key_code, "CCS-999-1")

    def test_bulk_create_rejects_reversed_range(self):
        res = self.client.post(reverse("facilities:room_bulk_create") + MODAL, {
            "building": self.ccs.pk, "start_number": 110, "end_number": 101,
            "room_type": "CLASSROOM"})
        self.assertEqual(res.status_code, 422)

    def test_toggles_are_post_only_and_refuse_while_keys_out(self):
        guard = User.objects.create_user(username="g", email="g@example.com",
                                         password="pw-Strong-12345", role=User.Role.GUARD)
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
        self.assertIsNotNone(post.archived_at)

    def test_guard_post_bulk_archive_restore_delete(self):
        posts = [GuardPost.objects.create(name=f"Gate {i}") for i in range(3)]
        ids = [p.pk for p in posts]
        url = reverse("facilities:post_bulk")
        # Delete is refused while the posts are live.
        res = self.client.post(url, {"action": "delete", "ids": ids})
        self.assertEqual(GuardPost.objects.filter(pk__in=ids).count(), 3)
        res = self.client.post(url, {"action": "archive", "ids": ids[:2]})
        self.assertEqual(GuardPost.objects.filter(pk__in=ids, is_active=False).count(), 2)
        self.assertContains(self.client.get(reverse("facilities:post_archived")), "Gate 0")
        self.client.post(url, {"action": "restore", "ids": ids[:1]})
        self.assertTrue(GuardPost.objects.get(pk=ids[0]).is_active)
        self.client.post(url, {"action": "delete", "ids": ids})
        self.assertEqual(sorted(GuardPost.objects.filter(pk__in=ids).values_list("pk", flat=True)),
                         sorted([ids[0], ids[2]]))

    def test_list_pages_render(self):
        for name in ("building_list", "room_list", "post_list"):
            self.assertEqual(self.client.get(reverse(f"facilities:{name}")).status_code, 200)
        res = self.client.get(reverse("facilities:room_list"), {"building": self.ccs.pk})
        self.assertContains(res, "CCS-101")


@override_settings(ALLOWED_HOSTS=["testserver"])
class BulkArchiveTests(TestCase):
    """Multi-select Archive / Restore (static/js/bulk-actions.js posts here).

    Archive is a soft delete throughout: PROTECTed foreign keys mean a real
    DELETE would either be refused or take borrow history with it.
    """
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin", email="a@example.com", password="pw-Strong-12345",
            role=User.Role.ADMIN)
        self.client.force_login(self.admin)
        self.ccs = Building.objects.create(code="CCS", name="Computing")
        self.eng = Building.objects.create(code="ENG", name="Engineering")
        self.r1 = Room.objects.create(building=self.ccs, room_code="101")
        self.r2 = Room.objects.create(building=self.ccs, room_code="102")

    def post(self, url, data):
        return self.client.post(url, data, headers={"x-requested-with": "XMLHttpRequest"})

    def test_bulk_archive_and_restore_buildings(self):
        url = reverse("facilities:building_bulk")
        res = self.post(url, {"action": "archive", "ids": [self.ccs.pk, self.eng.pk]})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])
        self.assertIn("2 building(s) archived", res.json()["message"])
        self.ccs.refresh_from_db(); self.eng.refresh_from_db()
        self.assertFalse(self.ccs.is_active)
        self.assertFalse(self.eng.is_active)
        # Nothing was deleted.
        self.assertEqual(Building.objects.count(), 2)

        res = self.post(url, {"action": "restore", "ids": [self.ccs.pk, self.eng.pk]})
        self.assertEqual(res.status_code, 200)
        self.ccs.refresh_from_db()
        self.assertTrue(self.ccs.is_active)

    def test_bulk_archive_skips_a_building_with_a_key_out(self):
        guard = User.objects.create_user(username="g", email="g@example.com",
                                         password="pw-Strong-12345", role=User.Role.GUARD)
        GuardShift.objects.create(guard=guard, post="Main Desk")
        key = Key.objects.create(key_code="CCS-101-1", room=self.r1,
                                 category=KeyCategory.objects.create(name="Room"))
        borrower = Borrower.objects.create(id_number="F-1", first_name="A", last_name="B",
                                           borrower_type="FACULTY")
        pos_borrow(key_id=key.pk, borrower=borrower, guard=guard)

        res = self.post(reverse("facilities:building_bulk"),
                        {"action": "archive", "ids": [self.ccs.pk, self.eng.pk]})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertIn("1 building(s) archived", body["message"])
        self.assertEqual(len(body["blocked"]), 1)
        self.ccs.refresh_from_db(); self.eng.refresh_from_db()
        self.assertTrue(self.ccs.is_active)     # skipped: a key is still out
        self.assertFalse(self.eng.is_active)

    def test_bulk_archive_rooms(self):
        res = self.post(reverse("facilities:room_bulk"),
                        {"action": "archive", "ids": [self.r1.pk, self.r2.pk]})
        self.assertEqual(res.status_code, 200)
        self.r1.refresh_from_db(); self.r2.refresh_from_db()
        self.assertFalse(self.r1.is_active)
        self.assertFalse(self.r2.is_active)
        self.assertEqual(Room.objects.count(), 2)

    def test_unknown_action_is_422_and_changes_nothing(self):
        res = self.post(reverse("facilities:room_bulk"),
                        {"action": "delete", "ids": [self.r1.pk]})
        self.assertEqual(res.status_code, 422)
        self.r1.refresh_from_db()
        self.assertTrue(self.r1.is_active)

    def test_bulk_endpoints_require_post_and_admin(self):
        for name in ("facilities:building_bulk", "facilities:room_bulk"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 405)

        guard = User.objects.create_user(username="guard2", email="g2@example.com",
                                         password="pw-Strong-12345", role=User.Role.GUARD)
        self.client.force_login(guard)
        for name in ("facilities:building_bulk", "facilities:room_bulk"):
            res = self.client.post(reverse(name), {"action": "archive", "ids": [self.r1.pk]})
            self.assertEqual(res.status_code, 403)


@override_settings(ALLOWED_HOSTS=["testserver"])
class ArchivedPageTests(TestCase):
    """Archived rows live on their own page, never mixed into the live list."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin", email="a@example.com", password="pw-Strong-12345",
            role=User.Role.ADMIN)
        self.client.force_login(self.admin)
        self.live = Building.objects.create(code="CCS", name="Computing")
        self.gone = Building.objects.create(code="OLD", name="Old Hall",
                                           is_active=False)
        self.live_room = Room.objects.create(building=self.live, room_code="101")
        self.gone_room = Room.objects.create(building=self.live, room_code="102",
                                             is_active=False)

    def test_building_pages_split_live_from_archived(self):
        res = self.client.get(reverse("facilities:building_list"))
        codes = [b.code for b in res.context["buildings"]]
        self.assertEqual(codes, ["CCS"])
        self.assertFalse(res.context["archived"])
        self.assertEqual(res.context["archived_count"], 1)

        res = self.client.get(reverse("facilities:building_archived"))
        codes = [b.code for b in res.context["buildings"]]
        self.assertEqual(codes, ["OLD"])
        self.assertTrue(res.context["archived"])

    def test_room_pages_split_live_from_archived(self):
        res = self.client.get(reverse("facilities:room_list"))
        self.assertIn(self.live_room, res.context["rooms"])
        self.assertNotIn(self.gone_room, res.context["rooms"])

        res = self.client.get(reverse("facilities:room_archived"))
        self.assertIn(self.gone_room, res.context["rooms"])
        self.assertNotIn(self.live_room, res.context["rooms"])

    def test_archived_pages_are_admin_only(self):
        guard = User.objects.create_user(username="g", email="g@example.com",
                                         password="pw-Strong-12345",
                                         role=User.Role.GUARD)
        self.client.force_login(guard)
        for name in ("facilities:building_archived", "facilities:room_archived"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_restore_from_the_archived_page_comes_back_to_it(self):
        back = reverse("facilities:room_archived")
        res = self.client.post(reverse("facilities:room_toggle", args=[self.gone_room.pk]),
                               {"next": back})
        self.assertRedirects(res, back)
        self.gone_room.refresh_from_db()
        self.assertTrue(self.gone_room.is_active)

    def test_next_is_ignored_when_it_points_off_site(self):
        res = self.client.post(reverse("facilities:room_toggle", args=[self.live_room.pk]),
                               {"next": "https://evil.example.com/"})
        self.assertRedirects(res, reverse("facilities:room_list"))
