"""Client-side rate limit for calls to Supabase (REST mirror and Auth).

A token bucket per API name, per process: it refills at SUPABASE_RATE_LIMIT
requests/second up to SUPABASE_RATE_BURST. A call that finds the bucket empty
waits for its turn, but never longer than SUPABASE_RATE_MAX_WAIT seconds; past
that it raises RateLimited so a page fails fast instead of hanging. Both
Supabase clients turn RateLimited into their own "unavailable" error, which
the existing retry and offline-fallback paths already handle.

RATE = 0 disables the limit. The bucket is in memory: it protects the
project's API quota from one runaway process (a bulk import, a sync loop),
not from several workers added together.
"""
import threading
import time

from django.conf import settings


class RateLimited(Exception):
    """The wait for a free slot would exceed SUPABASE_RATE_MAX_WAIT."""

    def __init__(self, wait):
        super().__init__(f"Supabase rate limit reached; next free slot in {wait:.1f}s.")
        self.wait = wait


class TokenBucket:
    def __init__(self, rate, burst, *, clock=time.monotonic, sleep=time.sleep):
        self.rate = float(rate)
        self.burst = max(1, int(burst))
        self._clock = clock
        self._sleep = sleep
        self._tokens = float(self.burst)
        self._stamp = clock()
        self._lock = threading.Lock()

    def acquire(self, max_wait):
        """Take one token, sleeping if needed. Raises RateLimited when the
        wait would pass max_wait (and takes nothing in that case)."""
        if self.rate <= 0:
            return
        with self._lock:
            now = self._clock()
            self._tokens = min(self.burst, self._tokens + (now - self._stamp) * self.rate)
            self._stamp = now
            if self._tokens >= 1:
                self._tokens -= 1
                return
            wait = (1 - self._tokens) / self.rate
            if wait > max_wait:
                raise RateLimited(wait)
            # Reserve the slot now so concurrent callers queue behind us.
            self._tokens -= 1
        self._sleep(wait)

    def retry_after(self, seconds):
        """The server said 429: drain the bucket so everyone backs off."""
        with self._lock:
            self._tokens = min(self._tokens, 0) - max(0.0, seconds) * self.rate


_buckets = {}
_buckets_lock = threading.Lock()


def get_bucket(name):
    with _buckets_lock:
        bucket = _buckets.get(name)
        rate = float(getattr(settings, "SUPABASE_RATE_LIMIT", 0) or 0)
        burst = int(getattr(settings, "SUPABASE_RATE_BURST", 10) or 10)
        if bucket is None or (bucket.rate, bucket.burst) != (rate, max(1, burst)):
            bucket = _buckets[name] = TokenBucket(rate, burst)
        return bucket


def acquire(name):
    get_bucket(name).acquire(float(getattr(settings, "SUPABASE_RATE_MAX_WAIT", 5.0)))


def reset():
    """Forget every bucket (tests)."""
    with _buckets_lock:
        _buckets.clear()
