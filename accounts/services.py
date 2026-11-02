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


# ---------------------------------------------------------------------------
# Supabase Auth account sync
#
# Admin screens edit the Django user; these push the matching change to the
# Supabase Auth user. Each is a no-op while SUPABASE_AUTH_ENABLED is off, and
# raises SupabaseAuthError when Supabase refuses or cannot be reached, so the
# caller can roll back or warn.
# ---------------------------------------------------------------------------

def _auth_metadata(user):
    return {"keytrack_user_id": user.pk, "username": user.username, "role": user.role}


def push_password(user, password):
    """Set the Supabase password, creating and linking the account if needed.

    If Supabase already has a user with this email but it is not linked, it is
    taken over: the caller (an admin, or a login that just proved the Django
    password) has authority over this identity, and resetting the password
    locks out whoever made that Supabase account.
    """
    from .supabase_auth import PROVISIONED_FLAG, SupabaseAuthError, get_client, is_enabled
    if not is_enabled():
        return
    client = get_client()
    if user.supabase_uid:
        client.update_user(user.supabase_uid, email=user.email, password=password,
                           user_metadata=_auth_metadata(user))
    else:
        try:
            sb_user = client.create_user(email=user.email, password=password,
                                         metadata=_auth_metadata(user))
        except SupabaseAuthError as e:
            if e.status != 422:
                raise
            sb_user = client.find_user_by_email(user.email)
            if sb_user is None:
                raise
            client.update_user(sb_user["id"], password=password,
                               user_metadata=_auth_metadata(user),
                               app_metadata={PROVISIONED_FLAG: True})
        user.supabase_uid = sb_user["id"]
        user.save(update_fields=["supabase_uid"])
    if not user.is_active:
        client.set_banned(user.supabase_uid, True)


def push_profile(user):
    """Copy email/username/role to the linked Supabase user, if any."""
    from .supabase_auth import get_client, is_enabled
    if not is_enabled() or not user.supabase_uid:
        return
    get_client().update_user(user.supabase_uid, email=user.email,
                             user_metadata=_auth_metadata(user))


def push_active(user):
    """Ban a deactivated user in Supabase, lift the ban on reactivation."""
    from .supabase_auth import get_client, is_enabled
    if not is_enabled() or not user.supabase_uid:
        return
    get_client().set_banned(user.supabase_uid, not user.is_active)
