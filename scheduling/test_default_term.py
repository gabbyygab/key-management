from datetime import date

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from scheduling.models import Term


class DefaultTermTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("adm", password="x", role="ADMIN")
        self.t1 = Term.objects.create(academic_year="2025-2026", semester="1ST",
                                      start_date=date(2025, 8, 1), end_date=date(2025, 12, 1))
        self.t2 = Term.objects.create(academic_year="2025-2026", semester="2ND",
                                      start_date=date(2026, 1, 1), end_date=date(2026, 5, 1))
        self.client.force_login(self.admin)

    def test_picker_sets_default_and_strips_term_param(self):
        r = self.client.post(reverse("scheduling:set_default_term"),
                             {"term": self.t1.pk, "next": f"/scheduling/offerings/?term={self.t2.pk}&q=a"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/scheduling/offerings/?q=a")
        r = self.client.get(reverse("scheduling:room_timetable"))
        self.assertEqual(r.context["selected_term"], self.t1)

    def test_explicit_param_wins_and_clear_resets(self):
        self.client.post(reverse("scheduling:set_default_term"), {"term": self.t1.pk})
        r = self.client.get(reverse("scheduling:room_timetable"), {"term": self.t2.pk})
        self.assertEqual(r.context["selected_term"], self.t2)
        self.client.post(reverse("scheduling:set_default_term"), {"term": ""})
        self.assertNotIn("kt_default_term", self.client.session)

    def test_unsafe_next_is_ignored(self):
        r = self.client.post(reverse("scheduling:set_default_term"),
                             {"term": self.t1.pk, "next": "https://evil.example/x"})
        self.assertEqual(r["Location"], reverse("scheduling:room_timetable"))
