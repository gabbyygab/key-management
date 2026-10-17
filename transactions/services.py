from django.db import transaction as db_transaction
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
