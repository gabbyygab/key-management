from .services import pending_count


def pending_approvals(request):
    """Expose pending_approvals_count to templates for admin users."""
    user = getattr(request, "user", None)
    if user and user.is_authenticated and (user.is_superuser or getattr(user, "role", None) == "ADMIN"):
        return {"pending_approvals_count": pending_count()}
    return {"pending_approvals_count": 0}


def navbar_context(request):
    """Inject `navbar_shift` for authenticated guards on every page.

    * ADMIN / superuser: None (they see the quick-add + dropdown instead).
    * GUARD: the active GuardShift row, or None if off-duty.
    * Other roles: None (context key still present, just falsy).
    """
    if not request.user.is_authenticated:
        return {}

    # Avoid a DB hit for admins — they don't show the shift indicator.
    if request.user.is_superuser or getattr(request.user, "role", None) == "ADMIN":
        return {"navbar_shift": None}

    if getattr(request.user, "role", None) == "GUARD":
        from transactions.services import get_active_shift
        return {"navbar_shift": get_active_shift(request.user)}

    return {"navbar_shift": None}


def active_nav_context(request):
    """Compute active navigation item identifier for sidebar highlighting (F2.1 / F7.3)."""
    match = getattr(request, "resolver_match", None)
    if not match:
        return {"active_nav": ""}

    ns = match.namespace or ""
    current = match.url_name or ""
    is_today = bool(request.GET.get("today"))

    active = ""
    if not ns and current == "dashboard":
        active = "dashboard"
    elif ns == "transactions":
        if current == "borrow_dashboard":
            active = "pos"
        elif current == "shift_history":
            active = "shift_history"
        elif current == "history":
            active = "today_transactions" if is_today else "transaction_history"
        elif current == "reports":
            active = "reports"
    elif ns == "scheduling":
        if current == "room_timetable":
            active = "room_schedule"
        elif current == "free_rooms":
            active = "room_finder"
        elif current in {"conflicts", "schedule_list"}:
            active = "conflicts"
    elif ns == "keys":
        if current in {"key_list", "key_create", "key_update"}:
            active = "key_inventory"
        elif current in {"category_list", "category_create", "category_update"}:
            active = "key_categories"
        elif current == "key_availability":
            active = "key_availability"
    elif ns == "facilities":
        if current in {"building_list", "building_create", "building_update"}:
            active = "buildings"
        elif current in {"room_list", "room_create", "room_update"}:
            active = "rooms"
    elif ns == "accounts":
        if current in {"borrower_list", "borrower_create", "borrower_update"}:
            active = "borrowers"
        elif current == "borrower_lookup":
            active = "borrower_lookup"
        elif current in {"user_list", "user_create", "user_update"}:
            active = "users_guards"
        elif current in {"pending_user_list", "user_approve", "user_reject"}:
            active = "pending_approvals"
    elif ns == "audit":
        if current in {"log_list"}:
            active = "audit_log"

    return {"active_nav": active}
