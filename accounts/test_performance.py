"""Page caching, throttled sessions and the Supabase rate limiter."""
import time
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts import cache_utils
from accounts.models import User
from supabase_sync import throttle


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


class TokenBucketTests(TestCase):
    def bucket(self, rate=2, burst=2):
        c = FakeClock()
        return throttle.TokenBucket(rate, burst, clock=c.now, sleep=c.sleep), c

    def test_burst_then_waits_for_refill(self):
        b, c = self.bucket()
        b.acquire(5); b.acquire(5)
        self.assertEqual(c.slept, [])
        b.acquire(5)
        self.assertEqual(c.slept, [0.5])

    def test_fails_fast_when_wait_exceeds_max(self):
        b, c = self.bucket(rate=1, burst=1)
        b.acquire(1)
        with self.assertRaises(throttle.RateLimited):
            b.acquire(0.5)
        self.assertEqual(c.slept, [])

    def test_rate_zero_disables(self):
        b, c = self.bucket(rate=0)
        for _ in range(100):
            b.acquire(0)

    def test_retry_after_drains_bucket(self):
        b, c = self.bucket(rate=2, burst=2)
        b.retry_after(2)
        b.acquire(10)
        self.assertGreater(c.slept[0], 1.0)

    @override_settings(SUPABASE_RATE_LIMIT=1, SUPABASE_RATE_BURST=1, SUPABASE_RATE_MAX_WAIT=0)
    def test_client_turns_limit_into_supabase_error(self):
        from supabase_sync.client import SupabaseClient, SupabaseError
        from supabase_sync.conf import SupabaseConfig
        throttle.reset()
        cfg = SupabaseConfig("https://x.supabase.co", "", "k", "public", 1, True)
        client = SupabaseClient(cfg, retries=0)
        throttle.acquire("rest")  # use the only token
        with self.assertRaises(SupabaseError) as cm:
            client.select("keys_key")
        self.assertEqual(cm.exception.status, 429)
        throttle.reset()


class DataCacheTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_remember_caches_and_bump_invalidates(self):
        calls = []
        build = lambda: calls.append(1) or len(calls)
        self.assertEqual(cache_utils.remember("t", ["txn"], build), 1)
        self.assertEqual(cache_utils.remember("t", ["txn"], build), 1)
        cache_utils.bump("txn")
        self.assertEqual(cache_utils.remember("t", ["txn"], build), 2)

    def test_parts_separate_entries(self):
        self.assertEqual(cache_utils.remember("p", [], lambda: "a", parts=[1]), "a")
        self.assertEqual(cache_utils.remember("p", [], lambda: "b", parts=[2]), "b")

    @override_settings(DATA_CACHE_TTL=0)
    def test_ttl_zero_disables(self):
        calls = []
        build = lambda: calls.append(1) or len(calls)
        cache_utils.remember("z", [], build); cache_utils.remember("z", [], build)
        self.assertEqual(len(calls), 2)

    def test_saving_watched_model_bumps_version(self):
        before = cache_utils._version("inventory")
        from facilities.models import Building
        Building.objects.create(code="ZZ", name="Cache Test")
        self.assertGreater(cache_utils._version("inventory"), before)

    def test_admin_dashboard_second_hit_uses_cache(self):
        admin = User.objects.create_user("cacheadmin", password="x", role="ADMIN")
        self.client.force_login(admin)
        self.client.get(reverse("dashboard"))
        with self.assertNumQueries(1):  # only the signed-in user; the page data is cached
            r = self.client.get(reverse("dashboard"))
        self.assertEqual(r.status_code, 200)


class SessionTouchTests(TestCase):
    def test_session_not_rewritten_every_request(self):
        u = User.objects.create_user("touch", password="x", role="ADMIN")
        self.client.force_login(u)
        self.client.get(reverse("dashboard"))
        first = self.client.session["_touched"]
        with mock.patch("accounts.middleware.time.time", return_value=first + 10):
            self.client.get(reverse("dashboard"))
        self.assertEqual(self.client.session["_touched"], first)
        with mock.patch("accounts.middleware.time.time", return_value=first + 10_000):
            self.client.get(reverse("dashboard"))
        self.assertEqual(self.client.session["_touched"], first + 10_000)
