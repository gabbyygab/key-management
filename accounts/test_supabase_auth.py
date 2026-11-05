"""Supabase Auth login and admin account sync (accounts/supabase_auth.py).

No network: a fake client stands in for Supabase and records every call.
"""
import io
import json
import uuid
from unittest import mock
from urllib.error import HTTPError

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .models import User
from .supabase_auth import (InvalidCredentials, SupabaseAuthClient, SupabaseAuthError,
                            SupabaseAuthUnavailable)

PW = "pw-Strong-12345"
MODAL = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}
SUPABASE_ON = override_settings(
    ALLOWED_HOSTS=["testserver"],
    SUPABASE_AUTH_ENABLED=True,
    SUPABASE_AUTH_OFFLINE_FALLBACK=True,
    AUTHENTICATION_BACKENDS=["accounts.backends.SupabaseAuthBackend"],
)


class FakeSupabase:
    """In-memory stand-in for the Supabase Auth API."""

    def __init__(self):
        self.users = {}  # uid -> {"id", "email", "password", "banned"}
        self.down = False
        self.calls = []

    def add(self, email, password):
        uid = str(uuid.uuid4())
        self.users[uid] = {"id": uid, "email": email, "password": password, "banned": False}
        return uid

    def _check(self):
        if self.down:
            raise SupabaseAuthUnavailable("Could not reach Supabase Auth: timed out")

    def sign_in(self, email, password):
        self._check()
        self.calls.append(("sign_in", email))
        for u in self.users.values():
            if u["email"].lower() == email.lower() and u["password"] == password and not u["banned"]:
                return {"id": u["id"], "email": u["email"]}
        raise InvalidCredentials("Invalid login credentials", status=400)

    def create_user(self, *, email, password, metadata=None):
        self._check()
        self.calls.append(("create_user", email))
        if any(u["email"].lower() == email.lower() for u in self.users.values()):
            raise SupabaseAuthError("A user with this email address has already been registered",
                                    status=422, code="email_exists")
        return self.users[self.add(email, password)]

    def update_user(self, uid, **attrs):
        self._check()
        self.calls.append(("update_user", str(uid), sorted(attrs)))
        u = self.users[str(uid)]
        if "password" in attrs:
            u["password"] = attrs["password"]
        if "email" in attrs:
            u["email"] = attrs["email"]
        if "ban_duration" in attrs:
            u["banned"] = attrs["ban_duration"] != "none"
        return u

    def set_banned(self, uid, banned):
        return self.update_user(uid, ban_duration="876000h" if banned else "none")

    def find_user_by_email(self, email):
        self._check()
        return next((u for u in self.users.values() if u["email"].lower() == email.lower()), None)


class FakeSupabaseMixin:
    def setUp(self):
        super().setUp()
        cache.clear()
        self.sb = FakeSupabase()
        for target in ("accounts.backends.get_client", "accounts.supabase_auth.get_client"):
            patcher = mock.patch(target, return_value=self.sb)
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_user(self, username, role=User.Role.GUARD, **extra):
        return User.objects.create_user(
            username=username, email=f"{username}@example.com", password=PW,
            role=role, **extra)

    def login(self, ident, password=PW):
        return self.client.post(reverse("accounts:login"), {"username": ident, "password": password})


@SUPABASE_ON
class SupabaseLoginTests(FakeSupabaseMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.guard = self.make_user("guard")
        self.guard.supabase_uid = self.sb.add("guard@example.com", PW)
        self.guard.save()

    def test_linked_user_logs_in_by_username_or_email(self):
        self.assertEqual(self.login("guard").status_code, 302)
        self.client.post(reverse("accounts:logout"))
        self.assertEqual(self.login("GUARD@example.com").status_code, 302)
        self.assertIn(("sign_in", "guard@example.com"), self.sb.calls)

    def test_wrong_password_is_refused(self):
        res = self.login("guard", "nope")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Invalid credentials")

    def test_supabase_password_change_wins_and_updates_local_hash(self):
        self.sb.users[str(self.guard.supabase_uid)]["password"] = "new-Pass-98765"
        self.assertEqual(self.login("guard", PW).status_code, 200)  # old one refused
        self.assertEqual(self.login("guard", "new-Pass-98765").status_code, 302)
        self.guard.refresh_from_db()
        self.assertTrue(self.guard.check_password("new-Pass-98765"))

    def test_inactive_never_reaches_supabase(self):
        self.guard.is_active = False
        self.guard.save()
        res = self.login("guard")
        self.assertContains(res, "deactivated by an administrator", status_code=403)
        self.assertEqual(self.sb.calls, [])

    def test_offline_fallback_uses_local_hash(self):
        self.sb.down = True
        self.assertEqual(self.login("guard").status_code, 302)

    @override_settings(LOGIN_RATE_LIMIT=2)
    def test_offline_fallback_rejects_and_throttles_wrong_password(self):
        self.sb.down = True
        res = self.login("guard", "nope")
        self.assertContains(res, "Invalid credentials")
        self.login("guard", "nope")
        self.assertEqual(self.login("guard").status_code, 429)

    @override_settings(SUPABASE_AUTH_OFFLINE_FALLBACK=False)
    def test_without_fallback_outage_blocks_login(self):
        self.sb.down = True
        res = self.login("guard")
        self.assertContains(res, "cannot be reached", status_code=503)

    def test_uid_mismatch_is_refused(self):
        self.guard.supabase_uid = uuid.uuid4()
        self.guard.save()
        self.assertEqual(self.login("guard").status_code, 200)


@SUPABASE_ON
class SupabaseFirstLoginMigrationTests(FakeSupabaseMixin, TestCase):
    def test_existing_user_is_moved_to_supabase_on_first_login(self):
        admin = self.make_user("boss", role=User.Role.ADMIN)
        self.assertEqual(self.login("boss").status_code, 302)
        admin.refresh_from_db()
        self.assertIsNotNone(admin.supabase_uid)
        self.assertEqual(self.sb.users[str(admin.supabase_uid)]["password"], PW)

    def test_wrong_password_does_not_migrate(self):
        self.make_user("boss", role=User.Role.ADMIN)
        self.login("boss", "nope")
        self.assertEqual(self.sb.users, {})

    def test_squatted_supabase_email_is_not_linked_by_its_own_password(self):
        """Someone signs up in Supabase with a guard's email and their own
        password. That password must not open the KEYTRACK account."""
        guard = self.make_user("guard")
        self.sb.add("guard@example.com", "attacker-Pass-1")
        self.assertEqual(self.login("guard", "attacker-Pass-1").status_code, 200)
        guard.refresh_from_db()
        self.assertIsNone(guard.supabase_uid)

    def test_squatted_email_is_taken_over_by_real_password(self):
        guard = self.make_user("guard")
        squat_uid = self.sb.add("guard@example.com", "attacker-Pass-1")
        self.assertEqual(self.login("guard").status_code, 302)
        guard.refresh_from_db()
        self.assertEqual(str(guard.supabase_uid), squat_uid)
        self.assertEqual(self.sb.users[squat_uid]["password"], PW)  # attacker locked out


@SUPABASE_ON
class SupabaseAdminSyncTests(FakeSupabaseMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.make_user("boss", role=User.Role.ADMIN)
        self.client.force_login(self.admin)

    def create_payload(self, **over):
        data = {"username": "newguard", "first_name": "New", "last_name": "Guard",
                "email": "newguard@example.com", "role": User.Role.GUARD}
        data.update(over)
        return data

    def test_create_user_provisions_supabase_account(self):
        res = self.client.post(reverse("accounts:user_create"), self.create_payload(), **MODAL)
        self.assertEqual(res.status_code, 201)
        user = User.objects.get(username="newguard")
        self.assertEqual(self.sb.users[str(user.supabase_uid)]["email"], "newguard@example.com")
        self.assertEqual(self.sb.users[str(user.supabase_uid)]["password"], "Admin2026")
        self.assertTrue(user.check_password("Admin2026"))

    def own_change(self, current=PW, new="My-New-Pass-77"):
        return self.client.post(reverse("accounts:password_change"),
                                {"current_password": current, "new_password1": new,
                                 "new_password2": new})

    def test_own_password_change_updates_supabase(self):
        guard = self.make_user("guard")
        guard.supabase_uid = self.sb.add("guard@example.com", PW)
        guard.save()
        self.client.force_login(guard)
        self.assertEqual(self.own_change().status_code, 302)
        self.assertEqual(self.sb.users[str(guard.supabase_uid)]["password"], "My-New-Pass-77")
        guard.refresh_from_db()
        self.assertTrue(guard.check_password("My-New-Pass-77"))

    def test_own_password_change_checks_current_password_with_supabase(self):
        guard = self.make_user("guard")
        # Changed in the Supabase dashboard: the stale local hash must not pass.
        guard.supabase_uid = self.sb.add("guard@example.com", "dashboard-Pass-9")
        guard.save()
        self.client.force_login(guard)
        self.assertEqual(self.own_change(current=PW).status_code, 422)
        self.assertEqual(self.sb.users[str(guard.supabase_uid)]["password"], "dashboard-Pass-9")

    def test_own_password_change_rolls_back_when_supabase_is_down(self):
        guard = self.make_user("guard")
        guard.supabase_uid = self.sb.add("guard@example.com", PW)
        guard.save()
        self.client.force_login(guard)
        self.sb.down = True
        self.assertEqual(self.own_change().status_code, 422)
        guard.refresh_from_db()
        self.assertTrue(guard.check_password(PW))

    def test_create_rolls_back_when_supabase_is_down(self):
        self.sb.down = True
        res = self.client.post(reverse("accounts:user_create"), self.create_payload(), **MODAL)
        self.assertEqual(res.status_code, 422)
        self.assertContains(res, "Supabase Auth refused", status_code=422)
        self.assertFalse(User.objects.filter(username="newguard").exists())

    def test_reset_password_updates_supabase(self):
        guard = self.make_user("guard")
        guard.supabase_uid = self.sb.add("guard@example.com", PW)
        guard.save()
        res = self.client.post(reverse("accounts:user_reset_password", args=[guard.pk]),
                               {"new_password1": "fresh-Pass-4321", "new_password2": "fresh-Pass-4321"},
                               **MODAL)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.sb.users[str(guard.supabase_uid)]["password"], "fresh-Pass-4321")
        guard.refresh_from_db()
        self.assertTrue(guard.check_password("fresh-Pass-4321"))

    def test_reset_password_rolls_back_when_supabase_is_down(self):
        guard = self.make_user("guard")
        guard.supabase_uid = self.sb.add("guard@example.com", PW)
        guard.save()
        self.sb.down = True
        res = self.client.post(reverse("accounts:user_reset_password", args=[guard.pk]),
                               {"new_password1": "fresh-Pass-4321", "new_password2": "fresh-Pass-4321"},
                               **MODAL)
        self.assertEqual(res.status_code, 422)
        guard.refresh_from_db()
        self.assertTrue(guard.check_password(PW))

    def test_deactivate_bans_and_reactivate_unbans(self):
        guard = self.make_user("guard")
        guard.supabase_uid = self.sb.add("guard@example.com", PW)
        guard.save()
        url = reverse("accounts:user_toggle", args=[guard.pk])
        self.client.post(url)
        self.assertTrue(self.sb.users[str(guard.supabase_uid)]["banned"])
        self.client.post(url)
        self.assertFalse(self.sb.users[str(guard.supabase_uid)]["banned"])

    def test_email_change_is_pushed(self):
        guard = self.make_user("guard")
        guard.supabase_uid = self.sb.add("guard@example.com", PW)
        guard.save()
        res = self.client.post(reverse("accounts:user_update", args=[guard.pk]),
                               {"username": "guard", "first_name": "G", "last_name": "One",
                                "email": "moved@example.com", "role": User.Role.GUARD}, **MODAL)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.sb.users[str(guard.supabase_uid)]["email"], "moved@example.com")


@override_settings(ALLOWED_HOSTS=["testserver"], SUPABASE_AUTH_ENABLED=False)
class SupabaseDisabledTests(FakeSupabaseMixin, TestCase):
    def test_nothing_is_sent_when_disabled(self):
        admin = self.make_user("boss", role=User.Role.ADMIN)
        self.client.force_login(admin)
        self.client.post(reverse("accounts:user_create"), {
            "username": "g2", "first_name": "G", "last_name": "Two", "email": "g2@example.com",
            "role": User.Role.GUARD}, **MODAL)
        self.assertTrue(User.objects.filter(username="g2").exists())
        self.assertEqual(self.sb.calls, [])


class SupabaseAuthClientHttpTests(SimpleTestCase):
    """The real client's mapping of HTTP answers to exceptions."""

    def client_(self):
        return SupabaseAuthClient(url="https://x.supabase.co", anon_key="sb_publishable_x",
                                  service_role_key="sb_secret_x", timeout=1)

    @staticmethod
    def http_error(code, payload):
        return HTTPError("https://x.supabase.co", code, "err", {}, io.BytesIO(json.dumps(payload).encode()))

    def test_bad_password_maps_to_invalid_credentials(self):
        err = self.http_error(400, {"error_code": "invalid_credentials", "msg": "Invalid login credentials"})
        with mock.patch("accounts.supabase_auth.urlopen", side_effect=err):
            with self.assertRaises(InvalidCredentials):
                self.client_().sign_in("a@b.c", "x")

    def test_server_error_maps_to_unavailable(self):
        with mock.patch("accounts.supabase_auth.urlopen", side_effect=self.http_error(503, {})):
            with self.assertRaises(SupabaseAuthUnavailable):
                self.client_().sign_in("a@b.c", "x")

    def test_duplicate_email_keeps_status_and_code(self):
        err = self.http_error(422, {"error_code": "email_exists", "msg": "already registered"})
        with mock.patch("accounts.supabase_auth.urlopen", side_effect=err):
            with self.assertRaises(SupabaseAuthError) as ctx:
                self.client_().create_user(email="a@b.c", password="x")
        self.assertEqual((ctx.exception.status, ctx.exception.code), (422, "email_exists"))

    def test_publishable_key_is_not_sent_as_bearer(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = b'{"user": {"id": "u1"}}'
        with mock.patch("accounts.supabase_auth.urlopen", return_value=resp) as m:
            self.client_().sign_in("a@b.c", "x")
        req = m.call_args.args[0]
        self.assertEqual(req.get_header("Apikey"), "sb_publishable_x")
        self.assertIsNone(req.get_header("Authorization"))


class OnlyAdminsCreateAccountsTests(TestCase):
    """Only admins add accounts, through any screen."""

    def setUp(self):
        self.staff_guard = User.objects.create_user(
            username="sguard", email="sguard@example.com", password=PW, role=User.Role.GUARD,
            is_staff=True)
        self.superadmin = User.objects.create_superuser(
            username="root", email="root@example.com", password=PW, role=User.Role.ADMIN)

    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_staff_guard_cannot_add_users_in_django_admin(self):
        self.client.force_login(self.staff_guard)
        self.assertEqual(self.client.get("/admin/accounts/user/add/").status_code, 403)

    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_admin_can_add_users_in_django_admin(self):
        self.client.force_login(self.superadmin)
        self.assertEqual(self.client.get("/admin/accounts/user/add/").status_code, 200)

    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_guard_cannot_use_add_user_screen(self):
        self.client.force_login(self.staff_guard)
        self.assertEqual(self.client.get(reverse("accounts:user_create")).status_code, 403)

    def test_supabase_create_marks_user_as_provisioned(self):
        """The database trigger lets in only rows carrying this flag."""
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = b'{"id": "u1"}'
        client = SupabaseAuthClient(url="https://x.supabase.co", anon_key="a",
                                    service_role_key="sb_secret_x", timeout=1)
        with mock.patch("accounts.supabase_auth.urlopen", return_value=resp) as m:
            client.create_user(email="a@b.c", password="x")
        body = json.loads(m.call_args.args[0].data)
        self.assertEqual(body["app_metadata"], {"keytrack_provisioned": True})
