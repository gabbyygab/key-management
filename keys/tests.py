from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.forms import BulkKeyForm, KeyForm
from facilities.models import Building, Room
from .models import Key, KeyCategory
from .services import KeyServiceError, bulk_create_key_copies

User = get_user_model()


class CategoryCrudTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="admin",
            email="admin@example.com",
            password="secret123",
            role=User.Role.ADMIN,
            approval_status=User.Approval.APPROVED,
        )
        self.client.force_login(self.user)

    def test_category_create_saves_category(self):
        response = self.client.post(
            reverse("keys:category_create"),
            {"name": "Master", "description": "Main key set"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(KeyCategory.objects.filter(name="Master").exists())

    def test_category_update_changes_values(self):
        category = KeyCategory.objects.create(name="Office", description="Old")
        response = self.client.post(
            reverse("keys:category_update", args=[category.pk]),
            {"name": "Cabinet", "description": "Updated description"},
        )
        self.assertEqual(response.status_code, 302)
        category.refresh_from_db()
        self.assertEqual(category.name, "Cabinet")
        self.assertEqual(category.description, "Updated description")


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class KeyFormTests(TestCase):
    def setUp(self):
        self.building = Building.objects.create(code="CCS", name="College of Computer Studies")
        self.room = Room.objects.create(
            building=self.building,
            room_code="204",
            name="Lab 1",
            room_type=Room.RoomType.LABORATORY,
        )
        self.category = KeyCategory.objects.create(name="Classroom", description="Regular key")

    def test_room_choices_grouped_by_building(self):
        form = KeyForm()
        rendered_html = form["room"].as_widget()
        self.assertIn("<optgroup", rendered_html)
        self.assertIn('label="CCS — College of Computer Studies"', rendered_html)
        self.assertIn("204", rendered_html)

    def test_valid_key_form_creates_key(self):
        form = KeyForm(
            data={
                "key_code": "CCS-204-1",
                "room": self.room.pk,
                "category": self.category.pk,
                "copy_number": 1,
                "status": Key.Status.AVAILABLE,
                "description": "Original door key",
                "remarks": "Newly cut",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        key = form.save()
        self.assertEqual(key.key_code, "CCS-204-1")
        self.assertEqual(key.copy_number, 1)
        self.assertTrue(key.is_original)

    def test_duplicate_copy_number_per_room_and_category_explains_constraint(self):
        Key.objects.create(
            key_code="CCS-204-1",
            room=self.room,
            category=self.category,
            copy_number=1,
            status=Key.Status.AVAILABLE,
        )

        form = KeyForm(
            data={
                "key_code": "CCS-204-DUP",
                "room": self.room.pk,
                "category": self.category.pk,
                "copy_number": 1,
                "status": Key.Status.AVAILABLE,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("copy_number", form.errors)
        error_msg = str(form.errors["copy_number"])
        self.assertIn("Copy #1", error_msg)
        self.assertIn("already exists", error_msg)
        self.assertIn("Constraint rule", error_msg)

    def test_different_copy_number_for_same_room_and_category_succeeds(self):
        Key.objects.create(
            key_code="CCS-204-1",
            room=self.room,
            category=self.category,
            copy_number=1,
        )
        form = KeyForm(
            data={
                "key_code": "CCS-204-2",
                "room": self.room.pk,
                "category": self.category.pk,
                "copy_number": 2,
                "status": Key.Status.AVAILABLE,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        key = form.save()
        self.assertEqual(key.copy_number, 2)
        self.assertFalse(key.is_original)

    def test_updating_existing_key_does_not_conflict_with_itself(self):
        key = Key.objects.create(
            key_code="CCS-204-1",
            room=self.room,
            category=self.category,
            copy_number=1,
            description="Initial description",
        )
        form = KeyForm(
            data={
                "key_code": "CCS-204-1",
                "room": self.room.pk,
                "category": self.category.pk,
                "copy_number": 1,
                "status": Key.Status.AVAILABLE,
                "description": "Updated description",
                "remarks": "Inspected",
            },
            instance=key,
        )
        self.assertTrue(form.is_valid(), form.errors)
        updated = form.save()
        self.assertEqual(updated.description, "Updated description")


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class KeyServiceTests(TestCase):
    def setUp(self):
        self.building = Building.objects.create(code="ENG", name="Engineering")
        self.room = Room.objects.create(
            building=self.building,
            room_code="301",
            name="Electronics Lab",
        )
        self.category = KeyCategory.objects.create(name="Sub-Master")

    def test_bulk_create_starting_from_empty(self):
        created = bulk_create_key_copies(
            room=self.room,
            category=self.category,
            count=3,
        )
        self.assertEqual(len(created), 3)
        self.assertEqual(created[0].copy_number, 1)
        self.assertEqual(created[1].copy_number, 2)
        self.assertEqual(created[2].copy_number, 3)
        self.assertEqual(created[0].key_code, "ENG-301-1")
        self.assertEqual(created[1].key_code, "ENG-301-2")
        self.assertEqual(created[2].key_code, "ENG-301-3")

    def test_bulk_create_increments_after_existing_copies(self):
        Key.objects.create(
            key_code="ENG-301-1",
            room=self.room,
            category=self.category,
            copy_number=1,
        )
        Key.objects.create(
            key_code="ENG-301-2",
            room=self.room,
            category=self.category,
            copy_number=2,
        )

        created = bulk_create_key_copies(
            room=self.room,
            category=self.category,
            count=2,
        )
        self.assertEqual(len(created), 2)
        self.assertEqual(created[0].copy_number, 3)
        self.assertEqual(created[1].copy_number, 4)
        self.assertEqual(created[0].key_code, "ENG-301-3")
        self.assertEqual(created[1].key_code, "ENG-301-4")

    def test_bulk_create_invalid_count_raises_error(self):
        with self.assertRaises(KeyServiceError):
            bulk_create_key_copies(
                room=self.room,
                category=self.category,
                count=0,
            )
        with self.assertRaises(KeyServiceError):
            bulk_create_key_copies(
                room=self.room,
                category=self.category,
                count=51,
            )


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class KeyViewsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin_user",
            email="admin@example.com",
            password="secret123",
            role=User.Role.ADMIN,
            approval_status=User.Approval.APPROVED,
        )
        self.guard = User.objects.create_user(
            username="guard_user",
            email="guard@example.com",
            password="secret123",
            role=User.Role.GUARD,
            approval_status=User.Approval.APPROVED,
        )
        self.b1 = Building.objects.create(code="CCS", name="Computer Studies")
        self.b2 = Building.objects.create(code="ENG", name="Engineering")

        self.r1 = Room.objects.create(building=self.b1, room_code="101", name="Intro Lab")
        self.r2 = Room.objects.create(building=self.b2, room_code="202", name="Lecture Hall")

        self.cat1 = KeyCategory.objects.create(name="Standard")
        self.cat2 = KeyCategory.objects.create(name="Master")

        self.key1 = Key.objects.create(
            key_code="CCS-101-1",
            room=self.r1,
            category=self.cat1,
            copy_number=1,
            status=Key.Status.AVAILABLE,
            description="Lab key",
        )
        self.key2 = Key.objects.create(
            key_code="CCS-101-2",
            room=self.r1,
            category=self.cat1,
            copy_number=2,
            status=Key.Status.BORROWED,
            description="Lab duplicate",
        )
        self.key3 = Key.objects.create(
            key_code="ENG-202-1",
            room=self.r2,
            category=self.cat2,
            copy_number=1,
            status=Key.Status.LOST,
            description="Hall master",
        )

    def test_guard_cannot_access_key_list(self):
        self.client.force_login(self.guard)
        response = self.client.get(reverse("keys:key_list"))
        self.assertEqual(response.status_code, 403)

    def test_admin_can_access_key_list_and_sees_original_badge(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("keys:key_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Key Inventory")
        self.assertContains(response, "Original")
        self.assertContains(response, "CCS-101-1")
        self.assertContains(response, "ENG-202-1")

    def test_filter_by_search_query(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("keys:key_list"), {"q": "ENG"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ENG-202-1")
        self.assertNotContains(response, "CCS-101-1")

    def test_filter_by_status(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("keys:key_list"), {"status": "BORROWED"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CCS-101-2")
        self.assertNotContains(response, "CCS-101-1")
        self.assertNotContains(response, "ENG-202-1")

    def test_filter_by_building(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("keys:key_list"), {"building": self.b2.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ENG-202-1")
        self.assertNotContains(response, "CCS-101-1")

    def test_filter_by_category(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("keys:key_list"), {"category": self.cat2.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ENG-202-1")
        self.assertNotContains(response, "CCS-101-1")

    def test_modal_create_get_partial(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("keys:key_create"), {"modal": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "keys/_key_form_modal.html")
        self.assertContains(response, 'data-room-search')
        self.assertContains(response, 'name="copy_number"')

    def test_modal_create_post_valid(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("keys:key_create") + "?modal=1",
            {
                "key_code": "CCS-101-3",
                "room": self.r1.pk,
                "category": self.cat1.pk,
                "copy_number": 3,
                "status": Key.Status.AVAILABLE,
                "description": "Third copy",
                "remarks": "",
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(Key.objects.filter(key_code="CCS-101-3").exists())

    def test_modal_create_post_duplicate_returns_422_with_explanatory_error(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("keys:key_create") + "?modal=1",
            {
                "key_code": "CCS-101-DUP",
                "room": self.r1.pk,
                "category": self.cat1.pk,
                "copy_number": 1,
                "status": Key.Status.AVAILABLE,
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertTemplateUsed(response, "keys/_key_form_modal.html")
        self.assertContains(response, "already exists", status_code=422)
        self.assertContains(response, "Constraint rule", status_code=422)

    def test_modal_update_get_and_post(self):
        self.client.force_login(self.admin)
        get_res = self.client.get(reverse("keys:key_update", args=[self.key1.pk]), {"modal": "1"})
        self.assertEqual(get_res.status_code, 200)
        self.assertContains(get_res, self.key1.key_code)

        post_res = self.client.post(
            reverse("keys:key_update", args=[self.key1.pk]) + "?modal=1",
            {
                "key_code": self.key1.key_code,
                "room": self.r1.pk,
                "category": self.cat1.pk,
                "copy_number": 1,
                "status": Key.Status.DAMAGED,
                "description": "Bent key",
                "remarks": "Needs replacement",
            },
        )
        self.assertEqual(post_res.status_code, 200)
        self.key1.refresh_from_db()
        self.assertEqual(self.key1.status, Key.Status.DAMAGED)

    def test_bulk_create_modal_get_and_post(self):
        self.client.force_login(self.admin)
        get_res = self.client.get(reverse("keys:key_bulk_create"), {"modal": "1", "room": self.r1.pk})
        self.assertEqual(get_res.status_code, 200)
        self.assertTemplateUsed(get_res, "keys/_key_bulk_modal.html")

        post_res = self.client.post(
            reverse("keys:key_bulk_create") + "?modal=1",
            {
                "room": self.r1.pk,
                "category": self.cat1.pk,
                "count": 2,
                "key_code_prefix": "CCS-101",
                "status": Key.Status.AVAILABLE,
                "description": "Bulk batch",
                "remarks": "",
            },
        )
        self.assertEqual(post_res.status_code, 201)
        self.assertTrue(Key.objects.filter(key_code="CCS-101-3").exists())
        self.assertTrue(Key.objects.filter(key_code="CCS-101-4").exists())
