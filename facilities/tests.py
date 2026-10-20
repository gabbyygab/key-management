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
