from django.db import transaction as db_transaction
from django.db.models import Q
from django.utils import timezone

from keys.models import Key
from .models import BorrowTransaction, GuardShift


class BorrowError(Exception):
    pass


def generate_transaction_no():
    today = timezone.localdate()
    prefix = f"TXN-{today:%Y%m%d}-"
    last = (BorrowTransaction.objects.filter(transaction_no__startswith=prefix)
            .order_by("-transaction_no").first())
    seq = int(last.transaction_no.split("-")[-1]) + 1 if last else 1
    return f"{prefix}{seq:04d}"


def get_active_shift(guard):
    return GuardShift.objects.filter(guard=guard, is_on_duty=True).first()


def borrower_has_active_key(borrower):
    return BorrowTransaction.objects.filter(
        borrower=borrower, status=BorrowTransaction.Status.ACTIVE).exists()


@db_transaction.atomic
def borrow_key(*, key_id, borrower, guard, purpose="", request=None):
    shift = get_active_shift(guard)
    if not shift:
        raise BorrowError("You must start a shift before releasing keys.")
    if not borrower.can_borrow:
        raise BorrowError("This borrower is not authorized to borrow keys.")
    # MVP rule: one person can only borrow one key at a time.
    if borrower_has_active_key(borrower):
        active = borrower.active_transaction()
        raise BorrowError(
            f"{borrower.full_name} already has key {active.key.key_code}. Return it first.")

    key = Key.objects.select_for_update().get(pk=key_id)
    if not key.is_available:
        raise BorrowError(f"Key {key.key_code} is not available (status: {key.status}).")

    txn = BorrowTransaction.objects.create(
        transaction_no=generate_transaction_no(),
        key=key, borrower=borrower, room=key.room, purpose=purpose or "Room use",
        released_by=guard, guard_shift=shift,
    )
    key.status = Key.Status.BORROWED
    key.save(update_fields=["status", "updated_at"])
    return txn


@db_transaction.atomic
def return_key(*, transaction_id, guard, remarks=""):
    shift = get_active_shift(guard)
    if not shift:
        raise BorrowError("You must have an active shift to receive keys.")

    txn = BorrowTransaction.objects.select_for_update().get(pk=transaction_id)
    if txn.status != BorrowTransaction.Status.ACTIVE:
        raise BorrowError("This transaction is already closed.")

    txn.returned_at = timezone.now()
    txn.received_by = guard
    txn.return_confirmed = True
    txn.status = BorrowTransaction.Status.RETURNED
    if remarks:
        txn.remarks = remarks
    txn.save()

    txn.key.status = txn.key.Status.AVAILABLE
    txn.key.save(update_fields=["status", "updated_at"])
    return txn


# How long before expected_return_at a row counts as "approaching due" (F3.7 amber).
DUE_SOON_MINUTES = 30

RETURN_CONDITIONS = {
    "OK": BorrowTransaction.Status.RETURNED,
    "DAMAGED": BorrowTransaction.Status.DAMAGED,
    "LOST": BorrowTransaction.Status.LOST,
}

_CONDITION_KEY_STATUS = {
    "DAMAGED": Key.Status.DAMAGED,
    "LOST": Key.Status.LOST,
}


def active_transactions(*, q=""):
    """Currently-out keys, newest first, for the POS "Currently Out" panel."""
    qs = (BorrowTransaction.objects
          .select_related("key__room__building", "key__category",
                          "borrower__department", "released_by")
          .filter(status__in=[BorrowTransaction.Status.ACTIVE,
                              BorrowTransaction.Status.OVERDUE]))
    if q:
        qs = qs.filter(
            Q(transaction_no__icontains=q)
            | Q(key__key_code__icontains=q)
            | Q(key__room__room_code__icontains=q)
            | Q(key__room__building__code__icontains=q)
            | Q(borrower__first_name__icontains=q)
            | Q(borrower__last_name__icontains=q)
            | Q(borrower__id_number__icontains=q))
    return qs


def due_state(txn, *, now=None):
    """"overdue" | "due_soon" | "ok" for one out-key row.

    Honors Status.OVERDUE (set by the mark_overdue command, backend B4.4) and
    falls back to comparing expected_return_at, which is null for most rows
    today because nothing populates it yet (backend B4.3).
    """
    now = now or timezone.now()
    if txn.status == BorrowTransaction.Status.OVERDUE:
        return "overdue"
    due = txn.expected_return_at
    if not due:
        return "ok"
    if now >= due:
        return "overdue"
    if (due - now).total_seconds() <= DUE_SOON_MINUTES * 60:
        return "due_soon"
    return "ok"


@db_transaction.atomic
def return_key_with_condition(*, transaction_id, guard, remarks="", condition="OK"):
    """Wrap return_key() with the F3.7 condition flag (OK / DAMAGED / LOST).

    return_key() owns the rules and the row locking; this only re-files the
    closed transaction and its key when the key came back damaged or not at all.
    """
    condition = (condition or "OK").upper()
    if condition not in RETURN_CONDITIONS:
        raise BorrowError(f"Unknown key condition: {condition}.")

    # return_key() accepts only an ACTIVE row, but an OVERDUE key is still
    # physically out and the guard has to be able to receive it (those are the
    # red rows in the Currently Out panel). Clear the flag with one conditional
    # UPDATE inside this transaction, then let return_key() apply its own rules
    # — a row that is RETURNED / LOST / DAMAGED is untouched and still rejected.
    BorrowTransaction.objects.filter(
        pk=transaction_id, status=BorrowTransaction.Status.OVERDUE
    ).update(status=BorrowTransaction.Status.ACTIVE)

    txn = return_key(transaction_id=transaction_id, guard=guard, remarks=remarks)

    if condition != "OK":
        txn.status = RETURN_CONDITIONS[condition]
        txn.save(update_fields=["status", "updated_at"])
        txn.key.status = _CONDITION_KEY_STATUS[condition]
        txn.key.save(update_fields=["status", "updated_at"])
    return txn


# ---------------------------------------------------------------------------
# POS wrapper (B4.1 / B4.3 / B5.5 / B6.2)
#
# borrow_key() above is the verified rule engine and is NOT modified. This
# wraps it in one outer atomic block so that:
#   1. borrow_key's own rules (shift, authorization, one active key, key
#      availability) are checked first and win;
#   2. the schedule check (SCHEDULE_ENFORCEMENT) runs afterwards — raising
#      here rolls the borrow back, so a warning never leaves a half-issued key;
#   3. expected_return_at is filled from the scheduled block (B4.3) or the
#      guard's pick, and an override reason lands in remarks + the audit log.
# ---------------------------------------------------------------------------

class ScheduleWarning(BorrowError):
    """The room is scheduled to someone else right now and no override reason
    was given. Carries the schedule payload so the POS can show who/when."""

    def __init__(self, message, *, schedule):
        super().__init__(message)
        self.schedule = schedule


def _open_statuses():
    return [BorrowTransaction.Status.ACTIVE, BorrowTransaction.Status.OVERDUE]


@db_transaction.atomic
def pos_borrow(*, key_id, borrower, guard, purpose="", expected_return_at=None,
               override_reason="", request=None, now=None):
    from audit.services import Action, log_activity
    from scheduling.services import get_schedule_settings, schedule_check_for_room

    now = now or timezone.now()
    override_reason = (override_reason or "").strip()

    # borrow_key's one-key rule only looks at ACTIVE rows; once mark_overdue
    # has flipped a row to OVERDUE the borrower would slip through. Close that
    # gap here rather than editing borrow_key.
    overdue = (BorrowTransaction.objects.filter(
        borrower=borrower, status=BorrowTransaction.Status.OVERDUE)
        .select_related("key").first())
    if overdue:
        raise BorrowError(
            f"{borrower.full_name} has an overdue key ({overdue.key.key_code}). Return it first.")

    if expected_return_at is not None and expected_return_at <= now:
        raise BorrowError("Expected return time must be in the future.")

    if not Key.objects.filter(pk=key_id).exists():
        raise BorrowError("Key not found.")

    txn = borrow_key(key_id=key_id, borrower=borrower, guard=guard,
                     purpose=purpose, request=request)

    cfg = get_schedule_settings()
    mode = cfg.enforcement
    check = {"state": "free", "current": None}
    if mode != "off":
        check = schedule_check_for_room(txn.room, borrower_id=borrower.pk,
                                        when=timezone.localtime(now))
    overridden = False
    off_schedule = False
    public = {k: v for k, v in check.items() if not k.startswith("_")}
    if check["state"] == "other":
        cur = check["current"] or {}
        who = cur.get("instructor") or "another class"
        msg = (f"Room {txn.room} is scheduled to {who} ({cur.get('title', '')}, "
               f"{cur.get('time_display', '')}) right now.")
        if mode == "block":
            raise ScheduleWarning(msg + " Borrowing is blocked by schedule enforcement.",
                                  schedule=public)
        if not override_reason:
            raise ScheduleWarning(msg + " Enter a reason to override.", schedule=public)
        overridden = off_schedule = True
    elif mode != "off" and check.get("no_timetable") and cfg.no_schedule_policy == "reason":
        if not override_reason:
            term = check.get("term") or "this semester"
            raise ScheduleWarning(
                f"No timetable has been generated for {term}. Enter a reason to continue.",
                schedule=public)
        overridden = True

    fields = []
    due = expected_return_at or check.get("_expected_return_dt")
    if due:
        txn.expected_return_at = due
        fields.append("expected_return_at")
    if overridden:
        txn.remarks = f"Override: {override_reason}"
        fields.append("remarks")
    if off_schedule:
        txn.off_schedule = True
        fields.append("off_schedule")
    if fields:
        txn.save(update_fields=fields + ["updated_at"])

    log_activity(guard, Action.BORROW, obj=txn, request=request,
                 description=f"Released {txn.key.key_code} to {borrower.full_name} "
                             f"({borrower.id_number}).")
    if overridden:
        cur = check["current"] or {}
        log_activity(guard, Action.OVERRIDE, obj=txn, request=request,
                     description=override_reason,
                     changes={"scheduled_block": cur.get("id"),
                              "scheduled_to": cur.get("instructor"),
                              "block_time": cur.get("time_display")})
    return txn


# ---------------------------------------------------------------------------
# Admin status correction (B4.6)
#
# borrow_key / return_key / pos_borrow are the guard-facing rule engine and are
# not touched. This is the admin repair path for a record that no longer matches
# reality (a key received off-system, a row closed by mistake, a key found after
# being written off). Every status carries a key status with it, so the two can
# never drift apart; the caller writes the audit row.
# ---------------------------------------------------------------------------

#: The Key.status that each BorrowTransaction.status implies.
_STATUS_KEY_STATUS = {
    BorrowTransaction.Status.ACTIVE: Key.Status.BORROWED,
    BorrowTransaction.Status.OVERDUE: Key.Status.OVERDUE,
    BorrowTransaction.Status.RETURNED: Key.Status.AVAILABLE,
    BorrowTransaction.Status.LOST: Key.Status.LOST,
    BorrowTransaction.Status.DAMAGED: Key.Status.DAMAGED,
}

#: Statuses where the key is still physically out.
OPEN_STATUSES = frozenset(_open_statuses())


@db_transaction.atomic
def admin_set_status(*, transaction_id, new_status, admin, now=None):
    """Move one transaction to `new_status` and re-file its key to match.

    Returns (txn, changes) where `changes` is the {field: [old, new]} diff for
    the audit log, empty when nothing moved. Raises BorrowError when the move
    would leave two open rows on the same key or the same borrower.
    """
    now = now or timezone.now()
    new_status = (new_status or "").strip().upper()
    if not new_status:
        return (BorrowTransaction.objects.get(pk=transaction_id), {})
    if new_status not in _STATUS_KEY_STATUS:
        raise BorrowError(f"Unknown transaction status: {new_status}.")

    txn = (BorrowTransaction.objects.select_for_update()
           .select_related("borrower").get(pk=transaction_id))
    old_status = txn.status
    if new_status == old_status:
        return txn, {}

    key = Key.objects.select_for_update().get(pk=txn.key_id)
    old_key_status = key.status
    was_open = old_status in OPEN_STATUSES
    now_open = new_status in OPEN_STATUSES

    if now_open and not was_open:
        # Re-opening a closed record puts the key back out, so the one-open-row
        # rules that borrow_key enforces have to hold here too.
        clash = (BorrowTransaction.objects.filter(key_id=key.pk, status__in=OPEN_STATUSES)
                 .exclude(pk=txn.pk).select_related("borrower").first())
        if clash:
            raise BorrowError(
                f"Key {key.key_code} is already out on {clash.transaction_no} "
                f"({clash.borrower.full_name}). Close that record first.")
        clash = (BorrowTransaction.objects.filter(borrower_id=txn.borrower_id,
                                                  status__in=OPEN_STATUSES)
                 .exclude(pk=txn.pk).select_related("key").first())
        if clash:
            raise BorrowError(
                f"{txn.borrower.full_name} already has an open key "
                f"({clash.key.key_code}). Close that record first.")
        if key.status == Key.Status.INACTIVE:
            raise BorrowError(f"Key {key.key_code} is archived and cannot be re-issued.")
        txn.returned_at = None
        txn.received_by = None
        txn.return_confirmed = False
    else:
        # Closing, or moving between two closed states. RETURNED and DAMAGED
        # mean the key came back, so they get a receipt; LOST never does.
        if new_status in (BorrowTransaction.Status.RETURNED,
                          BorrowTransaction.Status.DAMAGED):
            if not txn.returned_at:
                txn.returned_at = now
            if not txn.received_by_id:
                txn.received_by = admin
        txn.return_confirmed = new_status == BorrowTransaction.Status.RETURNED

    txn.status = new_status
    txn.save()

    changes = {"status": [old_status, new_status]}
    wanted = _STATUS_KEY_STATUS[new_status]
    # An archived key stays archived: un-archiving is the key screen's job.
    if key.status != wanted and key.status != Key.Status.INACTIVE:
        key.status = wanted
        key.save(update_fields=["status", "updated_at"])
        changes["key_status"] = [old_key_status, wanted]
    return txn, changes


def log_return(txn, *, guard, condition="OK", request=None):
    from audit.services import Action, log_activity
    log_activity(guard, Action.RETURN, obj=txn, request=request,
                 description=f"Received {txn.key.key_code} from {txn.borrower.full_name} "
                             f"(condition: {condition}).")


def mark_overdue(*, now=None):
    """B4.4: flip ACTIVE transactions past expected_return_at to OVERDUE, and
    their keys BORROWED -> OVERDUE. Idempotent: a second run finds nothing.
    Returns the number of transactions updated."""
    now = now or timezone.now()
    with db_transaction.atomic():
        qs = (BorrowTransaction.objects.select_for_update()
              .filter(status=BorrowTransaction.Status.ACTIVE,
                      expected_return_at__isnull=False,
                      expected_return_at__lt=now))
        key_ids = list(qs.values_list("key_id", flat=True))
        if not key_ids:
            return 0
        count = BorrowTransaction.objects.filter(
            status=BorrowTransaction.Status.ACTIVE,
            expected_return_at__isnull=False,
            expected_return_at__lt=now,
        ).update(status=BorrowTransaction.Status.OVERDUE, updated_at=now)
        Key.objects.filter(pk__in=key_ids, status=Key.Status.BORROWED).update(
            status=Key.Status.OVERDUE, updated_at=now)
    return count


def parse_due(raw):
    """expected_return_at from the POS: an ISO datetime or the browser's
    datetime-local value ("2026-10-31T17:30"), read in the server's local time.
    Raises ValueError with a guard-readable message when it can't be parsed."""
    from django.utils.dateparse import parse_datetime

    if not raw:
        return None
    try:
        dt = parse_datetime(str(raw))
    except ValueError:
        dt = None
    if dt is None:
        raise ValueError("Expected return time is not a valid date/time.")
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt
