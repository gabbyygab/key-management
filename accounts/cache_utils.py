"""Versioned data cache for slow, read-mostly admin pages.

Every Supabase query costs ~0.1s, so the dashboard (20+ queries) and reports
are cached for DATA_CACHE_TTL seconds. Each entry is keyed by the version of
the namespaces it depends on; saving or deleting a model in a namespace bumps
its version (signals below), so a cached page is never older than the last
write. The TTL is only the backstop.

    data = remember("dashboard", ["inventory", "txn"], lambda: build())
"""
import hashlib

from django.conf import settings
from django.core.cache import cache
from django.db.models.signals import post_delete, post_save

_VERSION_TTL = 60 * 60 * 24 * 7


def _version(ns):
    key = f"dcv:{ns}"
    v = cache.get(key)
    if v is None:
        v = 1
        cache.add(key, v, _VERSION_TTL)
        v = cache.get(key, 1)
    return v


def bump(*namespaces):
    for ns in namespaces:
        key = f"dcv:{ns}"
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 2, _VERSION_TTL)


def remember(name, namespaces, builder, *, parts=(), ttl=None):
    """Return builder()'s result, cached per (name, parts, namespace versions)."""
    ttl = settings.DATA_CACHE_TTL if ttl is None else ttl
    if ttl <= 0:
        return builder()
    raw = "|".join([name, *map(str, parts), *(f"{n}={_version(n)}" for n in namespaces)])
    key = "dc:" + hashlib.md5(raw.encode()).hexdigest()
    hit = cache.get(key)
    if hit is not None:
        return hit
    value = builder()
    cache.set(key, value, ttl)
    return value


# model label -> namespaces to invalidate
WATCHED = {
    "keys.Key": ("inventory",),
    "keys.KeyCategory": ("inventory",),
    "facilities.Room": ("inventory",),
    "facilities.Building": ("inventory",),
    "transactions.BorrowTransaction": ("txn",),
    "transactions.GuardShift": ("shift",),
    "audit.ActivityLog": ("audit",),
    "accounts.Borrower": ("txn",),
    "accounts.User": ("shift", "txn"),
    "scheduling.ScheduleBlock": ("schedule",),
    "scheduling.ScheduleException": ("schedule",),
    "scheduling.Term": ("schedule",),
}


def _invalidate(sender, **kwargs):
    namespaces = WATCHED.get(sender._meta.label)
    if namespaces:
        bump(*namespaces)


def connect():
    post_save.connect(_invalidate, dispatch_uid="datacache-save")
    post_delete.connect(_invalidate, dispatch_uid="datacache-delete")
