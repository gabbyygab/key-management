"""Everything the POS needs to pick a borrower and a key without a round trip.

The database is remote (every query costs ~0.1s), so the POS page embeds this
snapshot at render time and pos.js filters it in the browser: picking a
borrower shows the key list at once, and picking a key shows the room's
schedule verdict at once. pos.js refreshes it in the background from
GET /api/transactions/pos-snapshot/ and after every release or return.

Nothing here is authoritative. pos_borrow() re-checks availability, the
one-key rule and the timetable when the guard presses Release.

Shape:
    {
      "today": "2026-11-05",
      "now_minutes": 615.5,          # server wall clock, minutes past midnight
      "enforcement": "warn",
      "term": "2026 Fall", "no_timetable": false,
      "keys": [serialize_key(...) + room_code + room_holder, ...],
      "rooms": {"<room_id>": [block, ...]},   # today's blocks, by start time
      "borrowers": [serialize_borrower(...), ...] | null,   # null: too many
    }
"""
from django.utils import timezone

from accounts.api import serialize_borrower
from accounts.cache_utils import remember
from accounts.models import Borrower
from keys.api import room_holders, serialize_key
from keys.models import Key
from scheduling.services import (
    effective_term, get_schedule_settings, rooms_today_blocks, term_has_schedule,
)
from .models import BorrowTransaction

# Above this many active borrowers the browser falls back to the search API.
BORROWER_LIMIT = 2000

OPEN = [BorrowTransaction.Status.ACTIVE, BorrowTransaction.Status.OVERDUE]


def _keys():
    keys = list(Key.objects.select_related("room__building", "category")
                .filter(status=Key.Status.AVAILABLE, room__is_active=True)
                .order_by("room__building__code", "room__room_code", "copy_number", "pk"))
    holders = room_holders(k.room_id for k in keys)
    rows = []
    for k in keys:
        row = serialize_key(k)
        row["room_code"] = k.room.room_code
        row["room_holder"] = holders.get(k.room_id)
        rows.append(row)
    return rows


def _borrowers():
    qs = (Borrower.objects.select_related("department").filter(is_active=True)
          .order_by("last_name", "first_name", "pk"))
    borrowers = list(qs[:BORROWER_LIMIT + 1])
    if len(borrowers) > BORROWER_LIMIT:
        return None
    open_txns = {
        t.borrower_id: t for t in BorrowTransaction.objects
        .filter(borrower__is_active=True, status__in=OPEN)
        .select_related("key__room__building").order_by("-borrowed_at")
    }
    return [serialize_borrower(b, active=open_txns.get(b.pk)) for b in borrowers]


def _block(summary, today, tz):
    end = timezone.make_aware(
        timezone.datetime.combine(today, summary["end_time"]), tz)
    return {
        "id": summary["id"],
        "title": summary["display_title"],
        "instructor": summary["instructor_name"],
        "instructor_id": summary["instructor_id"],
        "start": summary["start_time"].hour * 60 + summary["start_time"].minute,
        "end": summary["end_time"].hour * 60 + summary["end_time"].minute,
        "start_time": summary["start_time_str"],
        "end_time": summary["end_time_str"],
        "time_display": summary["time_display"],
        "end_iso": end.isoformat(),
    }


def _schedule(when):
    today, tz = when.date(), timezone.get_current_timezone()
    cfg = get_schedule_settings()
    term = effective_term(today, cfg)
    rooms = {
        str(room_id): [_block(s, today, tz) for s in blocks]
        for room_id, blocks in rooms_today_blocks(when=when).items()
    }
    return {
        "enforcement": cfg.enforcement,
        "term": term.name if term else "",
        "no_timetable": not term_has_schedule(term),
        "rooms": rooms,
    }


def build_snapshot(when=None):
    when = when or timezone.localtime()
    day = when.date().isoformat()
    # Each part is cached until a save in its namespace (or DATA_CACHE_TTL),
    # so a warm page load costs no queries. The day is part of the key:
    # authorization expiry and the weekday's blocks both depend on it.
    snapshot = {
        "today": day,
        "now_minutes": when.hour * 60 + when.minute + when.second / 60,
        "keys": remember("pos-keys", ["inventory", "txn"], _keys),
        "borrowers": remember("pos-borrowers", ["txn"], _borrowers, parts=[day]),
    }
    snapshot.update(remember("pos-schedule", ["schedule"], lambda: _schedule(when), parts=[day]))
    return snapshot
