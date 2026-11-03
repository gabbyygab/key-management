"""accounts/services.py — business rules for user account management.

Views only translate HTTP to service calls and back (per guide.md §4).
"""

import re

# ---------------------------------------------------------------------------
# Employee IDs
#
# B2.1: an admin never types one. Every account gets the next free
# EMP-0001 style number when it is created, so the column stays unique and
# the admin form has one less field that can collide.
# ---------------------------------------------------------------------------

EMPLOYEE_ID_PREFIX = "EMP-"
EMPLOYEE_ID_DIGITS = 4
_EMPLOYEE_ID_RE = re.compile(r"^%s(\d+)$" % EMPLOYEE_ID_PREFIX)


def next_employee_id():
    """The next free sequential employee ID, e.g. "EMP-0007".

    Counts only IDs this function could have produced, so the hand-written
    ones from seed_demo ("EMP-GUARD1") never shift the sequence. The uniqueness
    check at the end covers those and any race, since the column is unique.
    """
    from .models import User

    highest = 0
    existing = set(
        User.objects.filter(employee_id__startswith=EMPLOYEE_ID_PREFIX)
        .values_list("employee_id", flat=True)
    )
    for value in existing:
        match = _EMPLOYEE_ID_RE.match(value or "")
        if match:
            highest = max(highest, int(match.group(1)))
    candidate = highest + 1
    while "%s%0*d" % (EMPLOYEE_ID_PREFIX, EMPLOYEE_ID_DIGITS, candidate) in existing:
        candidate += 1
    return "%s%0*d" % (EMPLOYEE_ID_PREFIX, EMPLOYEE_ID_DIGITS, candidate)


BORROWER_ID_PREFIX = "BRW-"
BORROWER_ID_DIGITS = 5
_BORROWER_ID_RE = re.compile(r"^%s(\d+)$" % BORROWER_ID_PREFIX)


def next_borrower_id():
    """The next free sequential borrower ID, e.g. "BRW-00042".

    Same approach as next_employee_id(): hand-written IDs (seed data, imports)
    never shift the sequence, and the loop skips any collision.
    """
    from .models import Borrower

    highest = 0
    existing = set(
        Borrower.objects.filter(id_number__startswith=BORROWER_ID_PREFIX)
        .values_list("id_number", flat=True)
    )
    for value in existing:
        match = _BORROWER_ID_RE.match(value or "")
        if match:
            highest = max(highest, int(match.group(1)))
    candidate = highest + 1
    while "%s%0*d" % (BORROWER_ID_PREFIX, BORROWER_ID_DIGITS, candidate) in existing:
        candidate += 1
    return "%s%0*d" % (BORROWER_ID_PREFIX, BORROWER_ID_DIGITS, candidate)


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


def push_delete(supabase_uid):
    """Delete a Supabase Auth user by uid.

    Takes the uid, not the user, because the caller has already deleted the
    Django row by the time this runs.
    """
    from .supabase_auth import get_client, is_enabled
    if not is_enabled() or not supabase_uid:
        return
    get_client().delete_user(supabase_uid)
