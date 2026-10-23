"""accounts/services.py — business rules for user account management.

All approval / rejection logic lives here; views only translate HTTP
to service calls and back (per guide.md §4).
"""

from django.db import transaction as db_transaction
from django.utils import timezone


class ApprovalError(Exception):
    """Raised when an approval/rejection cannot be completed."""


@db_transaction.atomic
def approve_user(*, user_id, approved_by):
    """Set user to APPROVED and record who approved them and when.

    Raises ApprovalError if the user is not in PENDING status.
    """
    from .models import User  # local import avoids circular imports at module load

    try:
        user = User.objects.select_for_update().get(pk=user_id)
    except User.DoesNotExist:
        raise ApprovalError("User not found.")

    if user.approval_status != User.Approval.PENDING:
        raise ApprovalError(
            f"{user.full_name} is already {user.get_approval_status_display().lower()}."
        )

    user.approval_status = User.Approval.APPROVED
    user.approved_by = approved_by
    user.approved_at = timezone.now()
    user.save(update_fields=["approval_status", "approved_by", "approved_at"])
    return user


@db_transaction.atomic
def reject_user(*, user_id, rejected_by, reason):
    """Set user to REJECTED and record who rejected them.

    Raises ApprovalError if:
    - the user is not in PENDING status
    - reason is blank
    """
    from .models import User

    reason = reason.strip()
    if not reason:
        raise ApprovalError("A rejection reason is required.")

    try:
        user = User.objects.select_for_update().get(pk=user_id)
    except User.DoesNotExist:
        raise ApprovalError("User not found.")

    if user.approval_status != User.Approval.PENDING:
        raise ApprovalError(
            f"{user.full_name} is already {user.get_approval_status_display().lower()}."
        )

    user.approval_status = User.Approval.REJECTED
    # Re-use approved_by / approved_at to record who actioned this and when.
    user.approved_by = rejected_by
    user.approved_at = timezone.now()
    user.save(update_fields=["approval_status", "approved_by", "approved_at"])
    return user


def pending_count():
    """Return the number of PENDING users (for the sidebar badge)."""
    from .models import User
    return User.objects.filter(approval_status=User.Approval.PENDING).count()
