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
        from transactions.services import request_active_shift
        return {"navbar_shift": request_active_shift(request)}

    return {"navbar_shift": None}


def default_term_context(request):
    """Navbar term picker (admins only): the live terms and the current default."""
    user = request.user
    if not user.is_authenticated or not (user.is_superuser or getattr(user, "role", None) == "ADMIN"):
        return {}
    from scheduling.term_default import cached_live_terms, default_term_from
    terms = cached_live_terms()
    pinned = request.session.get("kt_default_term")
    return {
        "nav_terms": terms,
        "nav_default_term": default_term_from(request, terms) if terms else None,
        "nav_term_is_manual": bool(pinned) and any(t.pk == pinned for t in terms),
    }


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
    elif ns == "audit":
        if current in {"log_list"}:
            active = "audit_log"

    return {"active_nav": active}
