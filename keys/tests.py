from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.forms import KeyForm
from facilities.models import Building, Room
from .models import Key, KeyCategory
from .services import CategoryInUseError, toggle_category_active

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["testserver"])
class CategoryModelAndServiceTests(TestCase):
    def setUp(self):
        self.building = Building.objects.create(code="BLDG1", name="Building 1")
        self.room = Room.objects.create(building=self.building, room_code="101")
        self.category = KeyCategory.objects.create(name="Master", description="Master keys")

    def test_category_defaults_to_active(self):
        self.assertTrue(self.category.is_active)
        self.assertEqual(self.category.key_count, 0)
        self.assertFalse(self.category.is_in_use)

    def test_category_key_counts_reflect_assigned_keys(self):
        Key.objects.create(
            key_code="K-101-1",
            room=self.room,
            category=self.category,
            copy_number=1,
            status=Key.Status.AVAILABLE,
        )
        Key.objects.create(
            key_code="K-101-2",
            room=self.room,
            category=self.category,
            copy_number=2,
            status=Key.Status.BORROWED,
        )

        self.assertEqual(self.category.key_count, 2)
        self.assertEqual(self.category.active_keys_count, 1)
        self.assertEqual(self.category.borrowed_keys_count, 1)
        self.assertEqual(self.category.borrowed_count, 1)
        self.assertTrue(self.category.is_in_use)

    def test_toggle_empty_category_deactivates_and_activates(self):
        # Deactivate
        result = toggle_category_active(category=self.category)
        self.assertFalse(result)
        self.category.refresh_from_db()
        self.assertFalse(self.category.is_active)

        # Reactivate
        result = toggle_category_active(category=self.category)
        self.assertTrue(result)
        self.category.refresh_from_db()
        self.assertTrue(self.category.is_active)

    def test_toggle_category_in_use_requires_force(self):
        Key.objects.create(
            key_code="K-101-1",
            room=self.room,
            category=self.category,
            status=Key.Status.AVAILABLE,
        )

        # Unforced attempt must raise CategoryInUseError
        with self.assertRaises(CategoryInUseError) as ctx:
            toggle_category_active(category=self.category, force=False)
        self.assertIn("in use by 1 key", str(ctx.exception))
        self.category.refresh_from_db()
        self.assertTrue(self.category.is_active)

        # Forced attempt succeeds
        result = toggle_category_active(category=self.category, force=True)
        self.assertFalse(result)
        self.category.refresh_from_db()
        self.assertFalse(self.category.is_active)

    def test_toggle_category_with_borrowed_keys_is_hard_blocked(self):
        Key.objects.create(
            key_code="K-101-1",
            room=self.room,
            category=self.category,
            status=Key.Status.BORROWED,
        )

        # Even with force=True, must be blocked
        with self.assertRaises(CategoryInUseError) as ctx:
            toggle_category_active(category=self.category, force=True)
        self.assertIn("currently borrowed or overdue", str(ctx.exception))
        self.category.refresh_from_db()
        self.assertTrue(self.category.is_active)


@override_settings(ALLOWED_HOSTS=["testserver"])
class CategoryViewTests(TestCase):
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
        self.building = Building.objects.create(code="BLDG1", name="Building 1")
        self.room = Room.objects.create(building=self.building, room_code="101")
        self.category = KeyCategory.objects.create(name="Master", description="Master keys")
        self.client.force_login(self.admin)

    def test_guard_cannot_access_category_list(self):
        self.client.force_login(self.guard)
        response = self.client.get(reverse("keys:category_list"))
        self.assertEqual(response.status_code, 403)

    def test_category_list_renders_table_with_key_count(self):
        Key.objects.create(
            key_code="K-101-1",
            room=self.room,
            category=self.category,
            status=Key.Status.AVAILABLE,
        )
        response = self.client.get(reverse("keys:category_list"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "keys/category_list.html")
        self.assertContains(response, "Master")
        self.assertContains(response, "1 key")
        self.assertContains(response, "category-modal")
        self.assertContains(response, "category-deactivate-modal")

    def test_category_list_filter_by_status_and_search(self):
        inactive_cat = KeyCategory.objects.create(
            name="Archived", description="Old tags", is_active=False
        )

        # Search
        res = self.client.get(reverse("keys:category_list") + "?q=Archived")
        self.assertIn(inactive_cat, res.context["categories"])
        self.assertNotIn(self.category, res.context["categories"])

        # Filter active
        res_active = self.client.get(reverse("keys:category_list") + "?status=active")
        self.assertIn(self.category, res_active.context["categories"])
        self.assertNotIn(inactive_cat, res_active.context["categories"])

        # Filter inactive
        res_inactive = self.client.get(reverse("keys:category_list") + "?status=inactive")
        self.assertIn(inactive_cat, res_inactive.context["categories"])
        self.assertNotIn(self.category, res_inactive.context["categories"])

    def test_category_create_full_page_and_modal_get(self):
        # Full-page GET
        res_full = self.client.get(reverse("keys:category_create"))
        self.assertEqual(res_full.status_code, 200)
        self.assertTemplateUsed(res_full, "keys/category_form.html")

        # Modal GET (?modal=1)
        res_modal = self.client.get(reverse("keys:category_create") + "?modal=1")
        self.assertEqual(res_modal.status_code, 200)
        self.assertTemplateUsed(res_modal, "keys/_category_form_modal.html")

    def test_category_create_post_success(self):
        # Full-page POST
        response = self.client.post(
            reverse("keys:category_create"),
            {"name": "Submaster", "description": "Submaster keys"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(KeyCategory.objects.filter(name="Submaster").exists())

        # Modal POST (?modal=1 returns 201)
        response_modal = self.client.post(
            reverse("keys:category_create") + "?modal=1",
            {"name": "Emergency", "description": "Emergency access"},
        )
        self.assertEqual(response_modal.status_code, 201)
        self.assertTrue(KeyCategory.objects.filter(name="Emergency").exists())

    def test_category_create_post_validation_error(self):
        # Duplicate name via modal -> 422 status
        response = self.client.post(
            reverse("keys:category_create") + "?modal=1",
            {"name": "Master", "description": "Duplicate name"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertTemplateUsed(response, "keys/_category_form_modal.html")
        self.assertContains(response, "already exists", status_code=422)

    def test_category_update_full_page_and_modal(self):
        # Modal GET
        res = self.client.get(reverse("keys:category_update", args=[self.category.pk]) + "?modal=1")
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, "keys/_category_form_modal.html")
        self.assertContains(res, "Master")

        # Modal POST success
        res_post = self.client.post(
            reverse("keys:category_update", args=[self.category.pk]) + "?modal=1",
            {"name": "Grand Master", "description": "Top tier keys"},
        )
        self.assertEqual(res_post.status_code, 200)
        self.category.refresh_from_db()
        self.assertEqual(self.category.name, "Grand Master")

        # Modal POST invalid
        other = KeyCategory.objects.create(name="Cabinet")
        res_invalid = self.client.post(
            reverse("keys:category_update", args=[other.pk]) + "?modal=1",
            {"name": "Grand Master", "description": "Collision"},
        )
        self.assertEqual(res_invalid.status_code, 422)

    def test_category_toggle_requires_post(self):
        res = self.client.get(reverse("keys:category_toggle", args=[self.category.pk]))
        self.assertEqual(res.status_code, 405)

    def test_category_toggle_unused_category_succeeds(self):
        # Deactivate
        res = self.client.post(reverse("keys:category_toggle", args=[self.category.pk]))
        self.assertEqual(res.status_code, 302)
        self.category.refresh_from_db()
        self.assertFalse(self.category.is_active)

        # Reactivate
        res2 = self.client.post(reverse("keys:category_toggle", args=[self.category.pk]))
        self.assertEqual(res2.status_code, 302)
        self.category.refresh_from_db()
        self.assertTrue(self.category.is_active)

    def test_category_toggle_in_use_warns_and_blocks_unconfirmed(self):
        Key.objects.create(
            key_code="K-101-1",
            room=self.room,
            category=self.category,
            status=Key.Status.AVAILABLE,
        )

        # Unconfirmed POST
        res = self.client.post(reverse("keys:category_toggle", args=[self.category.pk]))
        self.assertEqual(res.status_code, 302)
        self.category.refresh_from_db()
        self.assertTrue(self.category.is_active)

        # Unconfirmed AJAX POST -> 422 JSON
        res_ajax = self.client.post(
            reverse("keys:category_toggle", args=[self.category.pk]),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res_ajax.status_code, 422)
        data = res_ajax.json()
        self.assertTrue(data.get("requires_confirm"))
        self.assertEqual(data.get("key_count"), 1)

        # Confirmed POST (confirm=true)
        res_confirmed = self.client.post(
            reverse("keys:category_toggle", args=[self.category.pk]),
            {"confirm": "true"},
        )
        self.assertEqual(res_confirmed.status_code, 302)
        self.category.refresh_from_db()
        self.assertFalse(self.category.is_active)

    def test_category_toggle_borrowed_keys_blocked(self):
        Key.objects.create(
            key_code="K-101-1",
            room=self.room,
            category=self.category,
            status=Key.Status.BORROWED,
        )

        # Even with confirm=true, blocked
        res = self.client.post(
            reverse("keys:category_toggle", args=[self.category.pk]),
            {"confirm": "true"},
        )
        self.assertEqual(res.status_code, 302)
        self.category.refresh_from_db()
        self.assertTrue(self.category.is_active)

        # AJAX request returns 422 with requires_confirm=False
        res_ajax = self.client.post(
            reverse("keys:category_toggle", args=[self.category.pk]),
            {"confirm": "true"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res_ajax.status_code, 422)
        data = res_ajax.json()
        self.assertFalse(data.get("requires_confirm"))
        self.assertEqual(data.get("borrowed_count"), 1)


@override_settings(ALLOWED_HOSTS=["testserver"])
class KeyFormActiveCategoryFilterTests(TestCase):
    def setUp(self):
        self.building = Building.objects.create(code="BLDG1", name="Building 1")
        self.room = Room.objects.create(building=self.building, room_code="101")
        self.active_cat = KeyCategory.objects.create(name="Active Cat", is_active=True)
        self.inactive_cat = KeyCategory.objects.create(name="Inactive Cat", is_active=False)

    def test_key_form_excludes_inactive_categories_for_new_key(self):
        form = KeyForm()
        category_choices = list(form.fields["category"].queryset)
        self.assertIn(self.active_cat, category_choices)
        self.assertNotIn(self.inactive_cat, category_choices)

    def test_key_form_includes_existing_category_when_editing(self):
        key = Key.objects.create(
            key_code="K-OLD",
            room=self.room,
            category=self.inactive_cat,
        )
        form = KeyForm(instance=key)
        category_choices = list(form.fields["category"].queryset)
        self.assertIn(self.active_cat, category_choices)
        self.assertIn(self.inactive_cat, category_choices)
