from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Building, Room

User = get_user_model()


class RoomCrudTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="admin",
            email="admin@example.com",
            password="secret123",
            role=User.Role.ADMIN,
            approval_status=User.Approval.APPROVED,
        )
        self.client.force_login(self.user)
        self.building = Building.objects.create(code="ENG", name="Engineering")

    def test_room_create_saves_room(self):
        response = self.client.post(
            reverse("facilities:room_create"),
            {
                "building": self.building.pk,
                "room_code": "B101",
                "name": "Room 101",
                "room_type": Room.RoomType.CLASSROOM,
                "capacity": 30,
                "status": Room.Status.AVAILABLE,
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Room.objects.filter(building=self.building, room_code="B101").exists())

    def test_room_update_changes_values(self):
        room = Room.objects.create(
            building=self.building,
            room_code="B202",
            name="Room 202",
            room_type=Room.RoomType.LABORATORY,
            capacity=20,
            status=Room.Status.AVAILABLE,
        )

        response = self.client.post(
            reverse("facilities:room_update", args=[room.pk]),
            {
                "building": self.building.pk,
                "room_code": "B202",
                "name": "Updated Lab 202",
                "room_type": Room.RoomType.LABORATORY,
                "capacity": 25,
                "status": Room.Status.MAINTENANCE,
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        room.refresh_from_db()
        self.assertEqual(room.name, "Updated Lab 202")
        self.assertEqual(room.capacity, 25)
        self.assertEqual(room.status, Room.Status.MAINTENANCE)



class RoomDetailViewTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin_fac",
            email="admin_fac@example.com",
            password="secret123",
            role=User.Role.ADMIN,
            approval_status=User.Approval.APPROVED,
        )
        self.guard = User.objects.create_user(
            username="guard_fac",
            email="guard_fac@example.com",
            password="secret123",
            role=User.Role.GUARD,
            approval_status=User.Approval.APPROVED,
        )
        self.building = Building.objects.create(code="ENG", name="Engineering")
        self.room = Room.objects.create(
            building=self.building,
            room_code="105",
            name="Robotics Lab",
            room_type=Room.RoomType.LABORATORY,
            capacity=35,
            floor=1,
            status=Room.Status.AVAILABLE,
        )
        self.detail_url = reverse("facilities:room_detail", args=[self.room.pk])

    def test_admin_can_view_room_detail_with_embedded_widget(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ENG-105")
        self.assertContains(response, "Robotics Lab")
        self.assertContains(response, "Schedule Activity")
        self.assertContains(response, "today-in-room-widget")
        self.assertIn("today_schedule", response.context)
        self.assertEqual(response.context["room"], self.room)

    def test_guard_cannot_view_room_detail(self):
        self.client.force_login(self.guard)
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 403)

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 302)

    def test_room_list_links_to_room_detail(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("facilities:room_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.detail_url)

    def test_nonexistent_room_returns_404(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("facilities:room_detail", args=[99999]))
        self.assertEqual(response.status_code, 404)
