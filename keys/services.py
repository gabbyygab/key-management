from django.db import transaction as db_transaction
from django.db.models import Max

from accounts.retention import mark_archived
from .models import Key, KeyCategory


class KeyServiceError(Exception):
    """Domain exception for keys app."""
    pass


def generate_key_code(*, room, copy_number=1, category=None, prefix=None, exclude_key_id=None):
    """Generate a standard unique key_code for a room and copy_number.

    Default format: [Building]-[Room]-[CopyNumber] (e.g. CBA-101-1).
    If that code already exists, tries [Building]-[Room]-[CategoryTag]-[CopyNumber].
    If it still clashes, appends an incrementing numeric suffix.
    """
    if not prefix:
        building_code = room.building.code if hasattr(room, "building") and room.building else ""
        if building_code:
            prefix = f"{building_code}-{room.room_code}"
        else:
            prefix = f"{room.room_code}"
    prefix = prefix.strip()

    candidate = f"{prefix}-{copy_number}"
    qs = Key.objects.filter(key_code=candidate)
    if exclude_key_id:
        qs = qs.exclude(pk=exclude_key_id)
    if not qs.exists():
        return candidate

    cat_name = category.name if category else ""
    cat_tag = cat_name[:3].upper() if cat_name else "KEY"
    candidate = f"{prefix}-{cat_tag}-{copy_number}"
    qs = Key.objects.filter(key_code=candidate)
    if exclude_key_id:
        qs = qs.exclude(pk=exclude_key_id)
    if not qs.exists():
        return candidate

    suffix = 2
    while True:
        candidate = f"{prefix}-{cat_tag}-{copy_number}_{suffix}"
        qs = Key.objects.filter(key_code=candidate)
        if exclude_key_id:
            qs = qs.exclude(pk=exclude_key_id)
        if not qs.exists():
            return candidate
        suffix += 1


@db_transaction.atomic
def bulk_create_key_copies(
    *,
    room,
    category,
    count,
    key_code_prefix=None,
    status=Key.Status.AVAILABLE,
    description="",
    remarks="",
    user=None,
):
    """
    Bulk creates N copies of keys for a room and category.
    Automatically assigns the next sequential copy numbers to respect the
    unique_key_copy_per_room_category constraint.
    """
    if count < 1:
        raise KeyServiceError("Number of copies must be at least 1.")
    if count > 50:
        raise KeyServiceError("Cannot add more than 50 copies in one batch.")

    # Find highest existing copy_number for room and category
    max_copy = (
        Key.objects.filter(room=room, category=category)
        .aggregate(Max("copy_number"))["copy_number__max"]
        or 0
    )

    prefix = (key_code_prefix or "").strip()
    if not prefix:
        building_code = room.building.code if hasattr(room, "building") and room.building else ""
        if building_code:
            prefix = f"{building_code}-{room.room_code}"
        else:
            prefix = f"{room.room_code}"

    created_keys = []
    for i in range(1, count + 1):
        copy_num = max_copy + i
        key_code = generate_key_code(
            room=room,
            copy_number=copy_num,
            category=category,
            prefix=prefix,
        )

        key = Key.objects.create(
            key_code=key_code,
            room=room,
            category=category,
            copy_number=copy_num,
            status=status,
            description=description,
            remarks=remarks,
            created_by=user,
        )
        created_keys.append(key)

    return created_keys


# ---------------------------------------------------------------------------
# One key per new room
#
# A room is useless to the guard post until it has a key to hand over, so
# adding a room mints its single original key (copy_number=1, no copies) and it
# shows up on the Keys page immediately. Extra copies stay a deliberate act:
# the admin adds them from the key bulk modal (bulk_create_key_copies).
# ---------------------------------------------------------------------------

# Seeded by `manage.py seed_demo`; recreated here if the install has neither.
DEFAULT_CATEGORIES = {
    "Room": "Regular door key for a room.",
    "Office": "Faculty or staff office key.",
}


def default_key_category(room):
    """The category a room's auto-created key lands in.

    Prefers "Office" for an office and "Room" for everything else. An archived
    preferred category is never silently restored: the first live category wins
    instead, and only when there is no live category at all is one created.
    """
    name = "Office" if room.room_type == "OFFICE" else "Room"
    cat = KeyCategory.objects.filter(name__iexact=name, is_active=True).first()
    if cat:
        return cat
    cat = KeyCategory.objects.filter(is_active=True).order_by("name").first()
    if cat:
        return cat
    return KeyCategory.objects.create(name=name,
                                      description=DEFAULT_CATEGORIES[name])


@db_transaction.atomic
def ensure_room_key(*, room, user=None):
    """Give ``room`` its original key if it has none yet.

    Returns the Key, or None when the room already has one (so this is safe to
    call again) or when no code could be generated. Never raises: a room must
    still be addable if the key code clashes.
    """
    if Key.objects.filter(room=room).exists():
        return None
    try:
        created = bulk_create_key_copies(
            room=room, category=default_key_category(room), count=1, user=user)
    except KeyServiceError:
        return None
    return created[0]


class CategoryError(Exception):
    """Base exception for domain-level key category errors."""
    pass


class CategoryInUseError(CategoryError):
    """Raised when an operation on a category is blocked or requires confirmation because keys are assigned to it."""
    def __init__(self, message: str, key_count: int = 0, borrowed_count: int = 0):
        super().__init__(message)
        self.key_count = key_count
        self.borrowed_count = borrowed_count


@db_transaction.atomic
def toggle_category_active(*, category: KeyCategory, force: bool = False) -> bool:
    """
    Toggles a category between active and inactive.

    When archiving (category.is_active is True):
      1. Hard-blocks if any assigned keys are currently BORROWED or OVERDUE.
      2. Warns/blocks if any keys are assigned to the category unless force=True.
      3. Otherwise sets is_active=False (archived; the keys are untouched).

    When activating (category.is_active is False):
      Re-enables the category (is_active=True).

    Returns:
      bool: The new is_active status of the category.
    """
    if category.is_active:
        borrowed_count = category.keys.filter(
            status__in=[Key.Status.BORROWED, Key.Status.OVERDUE]
        ).count()
        if borrowed_count > 0:
            raise CategoryInUseError(
                f"Cannot archive category '{category.name}': {borrowed_count} key(s) are currently borrowed or overdue.",
                key_count=category.keys.count(),
                borrowed_count=borrowed_count,
            )

        key_count = category.keys.count()
        if key_count > 0 and not force:
            raise CategoryInUseError(
                f"Category '{category.name}' is in use by {key_count} key(s). Archiving must be confirmed.",
                key_count=key_count,
                borrowed_count=0,
            )

        category.is_active = False
        # Starts the retention clock: the category (and the keys below it) is
        # purged once ARCHIVE_RETENTION_DAYS have passed. See
        # accounts/retention.py.
        category.save(update_fields=["is_active",
                                     *mark_archived(category, archived=True)])
        return False
    else:
        category.is_active = True
        category.save(update_fields=["is_active",
                                     *mark_archived(category, archived=False)])
        return True


# ---------------------------------------------------------------------------
# Archiving a key (B3.3 vocabulary)
#
# Key has no is_active flag: Status.INACTIVE is the archived state. A key is
# never hard-deleted because transactions.Transaction PROTECTs it — deleting
# the row would mean deleting the borrow history that points at it.
# ---------------------------------------------------------------------------

OUT_STATUSES = (Key.Status.BORROWED, Key.Status.OVERDUE)


def key_archive_blocker(key: Key):
    """Why this key cannot be archived right now, or None."""
    if key.status in OUT_STATUSES:
        return f"{key.key_code} is currently {key.get_status_display().lower()}"
    return None


@db_transaction.atomic
def set_key_archived(*, key: Key, archived: bool) -> str:
    """Archive (INACTIVE) or restore (AVAILABLE) a key. Returns the past-tense
    verb for the message. The caller checks ``key_archive_blocker`` first."""
    key.status = Key.Status.INACTIVE if archived else Key.Status.AVAILABLE
    key.save(update_fields=["status", "updated_at",
                            *mark_archived(key, archived=archived)])
    return "archived" if archived else "restored"
