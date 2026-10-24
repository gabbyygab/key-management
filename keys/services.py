from django.db import transaction as db_transaction

from .models import Key, KeyCategory


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

    When deactivating (category.is_active is True):
      1. Hard-blocks if any assigned keys are currently BORROWED or OVERDUE.
      2. Warns/blocks if any keys are assigned to the category unless force=True.
      3. Otherwise sets is_active=False.

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
                f"Cannot deactivate category '{category.name}': {borrowed_count} key(s) are currently borrowed or overdue.",
                key_count=category.keys.count(),
                borrowed_count=borrowed_count,
            )

        key_count = category.keys.count()
        if key_count > 0 and not force:
            raise CategoryInUseError(
                f"Category '{category.name}' is in use by {key_count} key(s). Deactivation must be confirmed.",
                key_count=key_count,
                borrowed_count=0,
            )

        category.is_active = False
        category.save(update_fields=["is_active"])
        return False
    else:
        category.is_active = True
        category.save(update_fields=["is_active"])
        return True
