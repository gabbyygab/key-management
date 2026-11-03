"""Every create/edit/delete gives feedback: edits ask "Save changes?" first
(confirm.js via partials/edit_confirm_attrs.html), creates do not, and the
messages from full-page actions reach toast.js (partials/messages.html)."""
from django.test import TestCase, override_settings
from django.urls import reverse

from facilities.models import Building, GuardPost, Room
from keys.models import Key, KeyCategory

from .models import Borrower, User

MODAL = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


@override_settings(ALLOWED_HOSTS=["testserver"])
class EditConfirmationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(username="boss", email="boss@example.com",
                                             password="pw-Strong-12345", role=User.Role.ADMIN)
        cls.guard = User.objects.create_user(username="desk", email="desk@example.com",
                                             password="pw-Strong-12345", role=User.Role.GUARD)
        cls.building = Building.objects.create(code="CCS", name="Computer Studies")
        cls.room = Room.objects.create(building=cls.building, room_code="101")
        cls.post = GuardPost.objects.create(name="Gate 1")
        cls.category = KeyCategory.objects.create(name="Room")
        cls.key = Key.objects.create(room=cls.room, category=cls.category, key_code="CCS-101-1")
        cls.borrower = Borrower.objects.create(id_number="2024-0001", first_name="Ada",
                                               last_name="Lovelace")

    def setUp(self):
        self.client.force_login(self.admin)

    def get(self, url):
        res = self.client.get(url + "?modal=1", **MODAL)
        self.assertEqual(res.status_code, 200, url)
        return res.content.decode()

    def test_edit_forms_ask_before_saving(self):
        urls = [
            reverse("accounts:user_update", args=[self.guard.pk]),
            reverse("accounts:borrower_update", args=[self.borrower.pk]),
            reverse("facilities:building_update", args=[self.building.pk]),
            reverse("facilities:room_update", args=[self.room.pk]),
            reverse("facilities:post_update", args=[self.post.pk]),
            reverse("keys:key_update", args=[self.key.pk]),
            reverse("keys:category_update", args=[self.category.pk]),
        ]
        for url in urls:
            with self.subTest(url=url):
                body = self.get(url)
                self.assertIn('data-confirm="', body)
                self.assertIn('data-confirm-title="Save changes?"', body)

    def test_create_forms_do_not_ask(self):
        for name in ("accounts:user_create", "accounts:borrower_create",
                     "facilities:building_create", "facilities:room_create",
                     "facilities:post_create", "keys:key_create", "keys:category_create"):
            with self.subTest(url=name):
                self.assertNotIn("data-confirm=", self.get(reverse(name)))

    def test_password_reset_asks_with_danger_styling(self):
        body = self.get(reverse("accounts:user_reset_password", args=[self.guard.pk]))
        self.assertIn('data-confirm-title="Reset password?"', body)
        self.assertIn('data-confirm-variant="danger"', body)
        self.assertIn('data-confirm-subject="desk"', body)

    def test_full_page_edit_fallback_also_asks(self):
        res = self.client.get(reverse("facilities:building_update", args=[self.building.pk]))
        self.assertContains(res, 'data-confirm-title="Save changes?"')


@override_settings(ALLOWED_HOSTS=["testserver"])
class FlashToastTests(TestCase):
    def test_toggle_message_is_handed_to_toast_js(self):
        admin = User.objects.create_user(username="boss", email="boss@example.com",
                                         password="pw-Strong-12345", role=User.Role.ADMIN)
        building = Building.objects.create(code="CCS", name="Computer Studies")
        self.client.force_login(admin)
        res = self.client.post(reverse("facilities:building_toggle", args=[building.pk]), follow=True)
        self.assertContains(res, 'id="flash-messages"')
        self.assertContains(res, 'data-flash-level="success"')
        # B3.3 vocabulary: the UI verb is archive, never delete/deactivate.
        self.assertContains(res, "Building CCS archived.")
