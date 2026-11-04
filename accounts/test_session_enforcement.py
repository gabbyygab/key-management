from django.test import TestCase
from django.urls import reverse

from .models import User

PASSWORD = "Str0ng-pass-2026"


def make_user(username, role):
    return User.objects.create_user(username=username, password=PASSWORD, role=role,
                                    email=f"{username}@example.com",
                                    must_change_password=False)


class AnonymousAccessTests(TestCase):
    def test_every_page_redirects_to_login_with_next(self):
        login_url = reverse("accounts:login")
        for url in ("/", "/accounts/users/", "/keys/", "/reports/", "/audit/logs/",
                    "/facilities/buildings/", "/accounts/password/"):
            with self.subTest(url=url):
                r = self.client.get(url)
                self.assertEqual(r.status_code, 302)
                self.assertEqual(r["Location"], f"{login_url}?next={url}")

    def test_ajax_and_api_get_403_json_not_a_redirect(self):
        r = self.client.get("/accounts/users/", headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(r.status_code, 403)
        self.assertIn("error", r.json())
        r = self.client.get("/api/keys/search/")
        self.assertEqual(r.status_code, 403)
        self.assertIn("error", r.json())

    def test_anonymous_post_is_refused(self):
        r = self.client.post(reverse("accounts:logout"))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("accounts:login"), r["Location"])

    def test_login_page_is_public(self):
        self.assertEqual(self.client.get(reverse("accounts:login")).status_code, 200)


class SignedInAccessTests(TestCase):
    def setUp(self):
        self.admin = make_user("boss", "ADMIN")
        self.client.force_login(self.admin)

    def test_login_page_bounces_signed_in_user(self):
        r = self.client.get(reverse("accounts:login"))
        self.assertRedirects(r, reverse("dashboard"), fetch_redirect_response=False)

    def test_login_post_while_signed_in_does_not_reauthenticate(self):
        r = self.client.post(reverse("accounts:login"),
                             {"username": "x", "password": "y"})
        self.assertRedirects(r, reverse("dashboard"), fetch_redirect_response=False)

    def test_guard_login_page_goes_to_pos(self):
        make_user("g1", "GUARD")
        self.client.logout()
        self.client.login(username="g1", password=PASSWORD)
        r = self.client.get(reverse("accounts:login"))
        self.assertRedirects(r, reverse("transactions:borrow_dashboard"),
                             fetch_redirect_response=False)

    def test_pages_and_login_are_never_cached(self):
        for url in ("/", reverse("accounts:login")):
            with self.subTest(url=url):
                cc = self.client.get(url)["Cache-Control"]
                self.assertIn("no-store", cc)
                self.assertIn("private", cc)

    def test_back_after_logout_requires_login_again(self):
        self.client.post(reverse("accounts:logout"))
        r = self.client.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("accounts:login"), r["Location"])

    def test_session_cookie_flags(self):
        self.client.get("/")
        cookie = self.client.cookies["sessionid"]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")


class LoginFlowTests(TestCase):
    def test_login_honours_safe_next_and_rotates_session(self):
        make_user("boss", "ADMIN")
        self.client.get(reverse("accounts:login"))
        before = self.client.cookies.get("sessionid")
        before = before.value if before else None
        r = self.client.post(reverse("accounts:login"),
                             {"username": "boss", "password": PASSWORD, "next": "/keys/"})
        self.assertRedirects(r, "/keys/", fetch_redirect_response=False)
        self.assertNotEqual(self.client.cookies["sessionid"].value, before)

    def test_login_rejects_offsite_next(self):
        make_user("boss", "ADMIN")
        r = self.client.post(reverse("accounts:login"),
                             {"username": "boss", "password": PASSWORD,
                              "next": "https://evil.example/"})
        self.assertRedirects(r, reverse("dashboard"), fetch_redirect_response=False)


class AccountLockoutTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        make_user("g1", "GUARD")

    def test_three_wrong_passwords_lock_the_account_even_for_right_one(self):
        url = reverse("accounts:login")
        for _ in range(3):
            r = self.client.post(url, {"username": "g1", "password": "wrong"})
            self.assertEqual(r.status_code, 200)
        self.assertContains(r, "locked")
        r = self.client.post(url, {"username": "g1", "password": PASSWORD})
        self.assertEqual(r.status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_lock_follows_the_account_across_ips(self):
        url = reverse("accounts:login")
        for _ in range(3):
            self.client.post(url, {"username": "g1", "password": "wrong"},
                             REMOTE_ADDR="10.0.0.1")
        r = self.client.post(url, {"username": "g1", "password": PASSWORD},
                             REMOTE_ADDR="10.0.0.2")
        self.assertEqual(r.status_code, 429)

    def test_other_accounts_unaffected_and_success_resets(self):
        make_user("g2", "GUARD")
        url = reverse("accounts:login")
        for _ in range(2):
            self.client.post(url, {"username": "g1", "password": "wrong"})
        self.assertEqual(self.client.post(url, {"username": "g2", "password": PASSWORD}).status_code, 302)
        self.client.logout()
        self.assertEqual(self.client.post(url, {"username": "g1", "password": PASSWORD}).status_code, 302)
        self.client.logout()
        for _ in range(2):  # counter was reset, so 2 more misses do not lock
            self.client.post(url, {"username": "g1", "password": "wrong"})
        self.assertEqual(self.client.post(url, {"username": "g1", "password": PASSWORD}).status_code, 302)


class AdminLockVisibilityTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.admin = make_user("boss", "ADMIN")
        make_user("g1", "GUARD")
        make_user("g2", "GUARD")

    def lock(self, username):
        for _ in range(3):
            self.client.post(reverse("accounts:login"), {"username": username, "password": "bad"})

    def test_admin_sees_locked_badge_with_countdown_only_for_locked_account(self):
        self.lock("g1")
        self.client.force_login(self.admin)
        res = self.client.get(reverse("accounts:user_list"))
        rows = {u.username: u.lock_seconds for u in res.context["users"]}
        self.assertTrue(0 < rows["g1"] <= 900)
        self.assertEqual(rows["g2"], 0)
        self.assertContains(res, "data-lock-seconds", count=1)
        self.assertContains(res, "lock-countdown.js")

    def test_email_cannot_be_used_for_a_second_set_of_tries(self):
        for who in ("g1", "g1@example.com", "G1@EXAMPLE.COM"):
            self.client.post(reverse("accounts:login"), {"username": who, "password": "bad"})
        r = self.client.post(reverse("accounts:login"), {"username": "g1", "password": PASSWORD})
        self.assertEqual(r.status_code, 429)

    def test_lock_expires(self):
        from unittest import mock
        import time
        from . import lockout
        self.lock("g1")
        self.assertTrue(lockout.is_locked("g1"))
        with mock.patch("accounts.lockout.time.time", return_value=time.time() + 901):
            self.assertFalse(lockout.is_locked("g1"))
