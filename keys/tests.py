from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import KeyCategory

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
