from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Max, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.crud import (BULK_ACTIONS, archive_context,
                           bulk_delete_forever, bulk_response, bulk_selection,
                           bulk_summary, delete_forever, is_ajax, paginate,
                           post_redirect, querystring_without_page)
from accounts.forms import BulkKeyForm, CategoryForm, KeyForm
from accounts.permissions import admin_or_guard_required, admin_required
from audit.services import Action, log_activity, log_form_save
from facilities.models import Building, Room
from .models import Key, KeyCategory
from .services import (
    CategoryInUseError,
    KeyServiceError,
    bulk_create_key_copies,
    default_key_category,
    generate_key_code,
    key_archive_blocker,
    set_key_archived,
    toggle_category_active,
)


@admin_required
def key_list(request):
    """Live key inventory: every status except the archived one (INACTIVE)."""
    return _key_list(request, archived=False)


@admin_required
def key_archived(request):
    """Archived keys only, with Restore. Nothing here is offered at the POS."""
    return _key_list(request, archived=True)


def _key_list(request, *, archived):
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    building_id = request.GET.get("building", "").strip()
    category_id = request.GET.get("category", "").strip()

    keys = Key.objects.select_related("room__building", "category").all()
    # Key has no is_active flag: Status.INACTIVE *is* the archived state, so
    # the two pages split on it and the status filter never crosses over.
    if archived:
        keys = keys.filter(status=Key.Status.INACTIVE)
        status = ""
    else:
        keys = keys.exclude(status=Key.Status.INACTIVE)
        if status == Key.Status.INACTIVE:
            status = ""
    if q:
        keys = keys.filter(
            Q(key_code__icontains=q)
            | Q(room__room_code__icontains=q)
            | Q(room__name__icontains=q)
            | Q(description__icontains=q)
        )
    if status:
        keys = keys.filter(status=status)
    if building_id:
        keys = keys.filter(room__building_id=building_id)
    if category_id:
        keys = keys.filter(category_id=category_id)

    paginator = Paginator(keys, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    buildings = Building.objects.filter(is_active=True).order_by("name")
    categories = KeyCategory.objects.all().order_by("name")

    return render(
        request,
        "keys/key_list.html",
        {
            "keys": page_obj,
            "page_obj": page_obj,
            "q": q,
            "status": status,
            "building_id": building_id,
            "category_id": category_id,
            "statuses": [(c, l) for c, l in Key.Status.choices
                         if c != Key.Status.INACTIVE],
            "buildings": buildings,
            "categories": categories,
            **archive_context(
                request, archived=archived,
                live_url=reverse("keys:key_list"),
                archived_url=reverse("keys:key_archived"),
                noun="inventory",
                archived_count=Key.objects.filter(
                    status=Key.Status.INACTIVE).count(),
            ),
        },
    )


@admin_required
def key_create(request):
    is_modal = request.GET.get("modal") == "1" or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    initial = {}
    if request.method == "GET" and request.GET.get("room", "").isdigit():
        room_id = int(request.GET["room"])
        initial["room"] = room_id
        room = Room.objects.filter(pk=room_id).select_related("building").first()
        if room:
            cat = default_key_category(room)
            initial["category"] = cat.pk
            max_copy = Key.objects.filter(room=room).aggregate(Max("copy_number"))["copy_number__max"] or 0
            copy_num = max_copy + 1
            initial["copy_number"] = copy_num
            initial["key_code"] = generate_key_code(room=room, copy_number=copy_num, category=cat)

    form = KeyForm(request.POST or None, initial=initial)

    if request.method == "POST":
        if form.is_valid():
            obj = form.save(commit=False)
            obj.created_by = request.user
            obj.save()
            log_form_save(request, form, obj, created=True)
            if is_modal:
                return HttpResponse(f"Key {obj.key_code} added.", status=201)
            messages.success(request, f"Key {obj.key_code} added.")
            return redirect("keys:key_list")
        if is_modal:
            return render(
                request,
                "keys/_key_form_modal.html",
                {"form": form, "title": "Add key", "is_modal": True},
                status=422,
            )

    if is_modal:
        return render(
            request,
            "keys/_key_form_modal.html",
            {"form": form, "title": "Add key", "is_modal": True},
        )
    return render(request, "keys/key_form.html", {"form": form, "title": "Add key"})


@admin_required
def key_update(request, pk):
    is_modal = request.GET.get("modal") == "1" or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    obj = get_object_or_404(Key, pk=pk)
    form = KeyForm(request.POST or None, instance=obj)

    if request.method == "POST":
        if form.is_valid():
            form.save()
            log_form_save(request, form, obj, created=False)
            if is_modal:
                return HttpResponse(f"Key {obj.key_code} updated.", status=200)
            messages.success(request, f"Key {obj.key_code} updated.")
            return redirect("keys:key_list")
        if is_modal:
            return render(
                request,
                "keys/_key_form_modal.html",
                {"form": form, "title": "Edit key", "instance": obj, "is_modal": True},
                status=422,
            )

    if is_modal:
        return render(
            request,
            "keys/_key_form_modal.html",
            {"form": form, "title": "Edit key", "instance": obj, "is_modal": True},
        )
    return render(request, "keys/key_form.html", {"form": form, "title": "Edit key"})


@admin_required
def key_bulk_create(request):
    is_modal = request.GET.get("modal") == "1" or request.headers.get("X-Requested-With") == "XMLHttpRequest"

    initial = {}
    if request.method == "GET":
        if "room" in request.GET:
            initial["room"] = request.GET.get("room")
        if "category" in request.GET:
            initial["category"] = request.GET.get("category")

    form = BulkKeyForm(request.POST or None, initial=initial)

    if request.method == "POST":
        if form.is_valid():
            room = form.cleaned_data["room"]
            category = form.cleaned_data["category"]
            count = form.cleaned_data["count"]
            prefix = form.cleaned_data.get("key_code_prefix")
            status = form.cleaned_data["status"]
            description = form.cleaned_data.get("description", "")
            remarks = form.cleaned_data.get("remarks", "")
            try:
                created = bulk_create_key_copies(
                    room=room,
                    category=category,
                    count=count,
                    key_code_prefix=prefix,
                    status=status,
                    description=description,
                    remarks=remarks,
                    user=request.user,
                )
                msg = f"Created {len(created)} copies of key for {room}."
                log_activity(request.user, Action.CREATE, obj=room, request=request,
                             description=msg + " Codes: " + ", ".join(k.key_code for k in created))
                if is_modal:
                    return HttpResponse(msg, status=201)
                messages.success(request, msg)
                return redirect("keys:key_list")
            except KeyServiceError as e:
                form.add_error(None, str(e))
                if is_modal:
                    return render(
                        request,
                        "keys/_key_bulk_modal.html",
                        {"form": form, "title": "Bulk Add Key Copies", "is_modal": True},
                        status=422,
                    )
        if is_modal:
            return render(
                request,
                "keys/_key_bulk_modal.html",
                {"form": form, "title": "Bulk Add Key Copies", "is_modal": True},
                status=422,
            )

    if is_modal:
        return render(
            request,
            "keys/_key_bulk_modal.html",
            {"form": form, "title": "Bulk Add Key Copies", "is_modal": True},
        )
    return render(
        request,
        "keys/key_bulk_form.html",
        {"form": form, "title": "Bulk Add Key Copies"},
    )


@admin_required
def category_list(request):
    """Live categories only. Archived ones live on category_archived."""
    return _category_list(request, archived=False)


@admin_required
def category_archived(request):
    """Archived categories only, with Restore."""
    return _category_list(request, archived=True)


def _category_list(request, *, archived):
    q = request.GET.get("q", "").strip()

    categories = KeyCategory.objects.annotate(
        key_count=Count("keys"),
        borrowed_count=Count(
            "keys",
            filter=Q(keys__status__in=[Key.Status.BORROWED, Key.Status.OVERDUE]),
        ),
    ).all()

    if q:
        categories = categories.filter(
            Q(name__icontains=q) | Q(description__icontains=q)
        )
    categories = categories.filter(is_active=not archived)

    form = CategoryForm()
    create_url = reverse("keys:category_create")

    return render(
        request,
        "keys/category_list.html",
        {
            "categories": categories,
            "q": q,
            "form": form,
            "create_url": create_url,
            **archive_context(
                request, archived=archived,
                live_url=reverse("keys:category_list"),
                archived_url=reverse("keys:category_archived"),
                noun="categories",
                archived_count=KeyCategory.objects.filter(
                    is_active=False).count(),
            ),
        },
    )


@admin_required
def category_create(request):
    is_modal = request.GET.get("modal") == "1" or request.headers.get("x-requested-with") == "XMLHttpRequest"
    form = CategoryForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            obj = form.save()
            log_form_save(request, form, obj, created=True)
            if is_modal:
                return HttpResponse(f"Category '{obj.name}' added.", status=201)
            messages.success(request, f"Category '{obj.name}' added.")
            return redirect("keys:category_list")
        elif is_modal:
            return render(
                request,
                "keys/_category_form_modal.html",
                {
                    "form": form,
                    "title": "Add Category",
                    "action_url": reverse("keys:category_create"),
                },
                status=422,
            )

    if is_modal:
        return render(
            request,
            "keys/_category_form_modal.html",
            {
                "form": form,
                "title": "Add Category",
                "action_url": reverse("keys:category_create"),
            },
        )

    return render(request, "keys/category_form.html", {"form": form, "title": "Add category"})


@admin_required
def category_update(request, pk):
    obj = get_object_or_404(KeyCategory, pk=pk)
    is_modal = request.GET.get("modal") == "1" or request.headers.get("x-requested-with") == "XMLHttpRequest"
    form = CategoryForm(request.POST or None, instance=obj)

    if request.method == "POST":
        if form.is_valid():
            obj = form.save()
            log_form_save(request, form, obj, created=False)
            if is_modal:
                return HttpResponse(f"Category '{obj.name}' updated.", status=200)
            messages.success(request, f"Category '{obj.name}' updated.")
            return redirect("keys:category_list")
        elif is_modal:
            return render(
                request,
                "keys/_category_form_modal.html",
                {
                    "form": form,
                    "category": obj,
                    "title": f"Edit Category: {obj.name}",
                    "action_url": reverse("keys:category_update", args=[obj.pk]),
                },
                status=422,
            )

    if is_modal:
        return render(
            request,
            "keys/_category_form_modal.html",
            {
                "form": form,
                "category": obj,
                "title": f"Edit Category: {obj.name}",
                "action_url": reverse("keys:category_update", args=[obj.pk]),
            },
        )

    return render(request, "keys/category_form.html", {"form": form, "title": "Edit category"})


@admin_required
@require_POST
def category_toggle(request, pk):
    """Archive, not delete: keys PROTECT their category, so the row has to stay
    for the keys (and their borrow history) to stay readable. Archiving only
    stops the category being offered for new keys."""
    category = get_object_or_404(KeyCategory, pk=pk)
    force = request.POST.get("confirm") == "true" or request.POST.get("force") == "true"
    ajax = is_ajax(request)

    try:
        new_state = toggle_category_active(category=category, force=force)
        verb = "restored" if new_state else "archived"
        log_activity(request.user, Action.UPDATE, obj=category, request=request,
                     description=f"Category '{category.name}' {verb}.",
                     changes={"is_active": [str(not new_state), str(new_state)]})
        messages.success(request, f"Category '{category.name}' {verb}.")
        if ajax:
            return JsonResponse({"success": True, "is_active": new_state,
                                 "message": f"Category '{category.name}' {verb}."})
    except CategoryInUseError as e:
        messages.error(request, str(e))
        if ajax:
            return JsonResponse({
                "error": str(e),
                "key_count": e.key_count,
                "borrowed_count": e.borrowed_count,
                "requires_confirm": e.borrowed_count == 0,
            }, status=422)

    return redirect(post_redirect(request, "keys:category_list"))


@admin_required
@require_POST
def category_bulk(request):
    """Archive or restore several categories in one post (bulk-actions.js).

    The toolbar always confirms first, so ``force`` is implied: a category that
    merely has keys attached goes through, one with a key still out does not.
    """
    action = request.POST.get("action", "")
    if action not in BULK_ACTIONS:
        return bulk_response(request, message="Unknown bulk action.", ok=False,
                             redirect_to=post_redirect(request, "keys:category_list"))
    if action == "delete":
        return bulk_delete_forever(
            request, bulk_selection(request, KeyCategory.objects.all()),
            noun="category(ies)",
            redirect_to=post_redirect(request, "keys:category_archived"))
    archive = action == "archive"
    done, blocked = [], []
    for obj in bulk_selection(request, KeyCategory.objects.all()):
        if obj.is_active is not archive:
            continue
        try:
            new_state = toggle_category_active(category=obj, force=True)
        except CategoryInUseError as e:
            blocked.append(f"'{obj.name}' has {e.borrowed_count} key(s) still out")
            continue
        verb = "restored" if new_state else "archived"
        log_activity(request.user, Action.UPDATE, obj=obj, request=request,
                     description=f"Category '{obj.name}' {verb}.",
                     changes={"is_active": [str(not new_state), str(new_state)]})
        done.append(obj)
    message = bulk_summary(done=done, blocked=blocked, noun="category(ies)",
                           verb="archived" if archive else "restored")
    return bulk_response(request, message=message or "Nothing to update.",
                         ok=bool(done), blocked=blocked,
                         redirect_to=post_redirect(request, "keys:category_list"))


@admin_required
@require_POST
def key_archive(request, pk):
    """Archive (status=INACTIVE) or restore (status=AVAILABLE) one key.

    Transactions PROTECT the key row, so a key is never hard-deleted: an
    archived key keeps every borrow it was part of and simply stops being
    offered at the POS.
    """
    key = get_object_or_404(Key.objects.select_related("room__building"), pk=pk)
    archive = key.status != Key.Status.INACTIVE
    if archive:
        blocker = key_archive_blocker(key)
        if blocker:
            messages.error(request, f"Cannot archive: {blocker}.")
            return redirect(post_redirect(request, "keys:key_list"))
    verb = set_key_archived(key=key, archived=archive)
    log_activity(request.user, Action.UPDATE, obj=key, request=request,
                 description=f"Key {key.key_code} {verb}.",
                 changes={"status": [Key.Status.INACTIVE if not archive else Key.Status.AVAILABLE,
                                      key.status]})
    messages.success(request, f"Key {key.key_code} {verb}.")
    return redirect(post_redirect(request, "keys:key_list"))


@admin_required
@require_POST
def key_delete(request, pk):
    """Delete an archived key for good, with its borrow history (retention).
    Refused while the key is out."""
    key = get_object_or_404(Key.objects.select_related("room__building"), pk=pk)
    return delete_forever(request, key, noun="key(s)",
                          redirect_to=post_redirect(request, "keys:key_archived"))


@admin_required
@require_POST
def category_delete(request, pk):
    """Delete an archived category for good, with its keys and their borrow
    history (retention). Refused while one of those keys is out."""
    obj = get_object_or_404(KeyCategory, pk=pk)
    return delete_forever(request, obj, noun="category(ies)",
                          redirect_to=post_redirect(request, "keys:category_archived"))


@admin_required
@require_POST
def key_bulk(request):
    """Archive or restore several keys in one post (bulk-actions.js)."""
    action = request.POST.get("action", "")
    if action not in BULK_ACTIONS:
        return bulk_response(request, message="Unknown bulk action.", ok=False,
                             redirect_to=post_redirect(request, "keys:key_list"))
    if action == "delete":
        return bulk_delete_forever(
            request, bulk_selection(request, Key.objects.select_related("room__building")),
            noun="key(s)",
            redirect_to=post_redirect(request, "keys:key_archived"))
    archive = action == "archive"
    done, blocked = [], []
    qs = Key.objects.select_related("room__building")
    for obj in bulk_selection(request, qs):
        if (obj.status == Key.Status.INACTIVE) is archive:
            continue  # already archived / already live
        if archive:
            why = key_archive_blocker(obj)
            if why:
                blocked.append(why)
                continue
        before = obj.status
        verb = set_key_archived(key=obj, archived=archive)
        log_activity(request.user, Action.UPDATE, obj=obj, request=request,
                     description=f"Key {obj.key_code} {verb}.",
                     changes={"status": [before, obj.status]})
        done.append(obj)
    message = bulk_summary(done=done, blocked=blocked, noun="key(s)",
                           verb="archived" if archive else "restored")
    return bulk_response(request, message=message or "Nothing to update.",
                         ok=bool(done), blocked=blocked,
                         redirect_to=post_redirect(request, "keys:key_list"))


@admin_or_guard_required
def key_availability(request):
    """Guard read-only key availability lookup."""
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    keys = Key.objects.select_related("room__building", "category").all()
    if q:
        keys = keys.filter(
            Q(key_code__icontains=q)
            | Q(room__room_code__icontains=q)
            | Q(room__building__code__icontains=q)
            | Q(description__icontains=q)
        )
    if status:
        keys = keys.filter(status=status)

    # B8.11: real pagination, 10 per page (accounts.crud.LIST_PAGE_SIZE), so
    # the full inventory stays auditable instead of being silently cut off.
    keys = keys.order_by("key_code", "-pk")
    page_obj = paginate(request, keys)
    querystring = querystring_without_page(request)

    return render(
        request,
        "keys/key_availability.html",
        {
            "keys": page_obj,
            "page_obj": page_obj,
            "querystring": querystring,
            "q": q,
            "status": status,
            "statuses": Key.Status.choices,
        },
    )
