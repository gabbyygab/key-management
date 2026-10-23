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
