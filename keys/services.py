from django.db import transaction as db_transaction
from django.db.models import Max
from .models import Key, KeyCategory


class KeyServiceError(Exception):
    """Domain exception for keys app."""
    pass


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
        key_code = f"{prefix}-{copy_num}"

        # If key_code already exists across the system, try inserting category tag
        if Key.objects.filter(key_code=key_code).exists():
            cat_tag = category.name[:3].upper()
            key_code = f"{prefix}-{cat_tag}-{copy_num}"
            if Key.objects.filter(key_code=key_code).exists():
                raise KeyServiceError(
                    f"Generated key code '{key_code}' already exists. "
                    "Please provide a custom key code prefix."
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
