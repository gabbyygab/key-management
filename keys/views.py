from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.forms import BulkKeyForm, CategoryForm, KeyForm
from accounts.permissions import admin_required
from facilities.models import Building
from .models import Key, KeyCategory
from .services import (
    CategoryInUseError,
    KeyServiceError,
    bulk_create_key_copies,
    toggle_category_active,
)


@admin_required
def key_list(request):
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    building_id = request.GET.get("building", "").strip()
    category_id = request.GET.get("category", "").strip()

    keys = Key.objects.select_related("room__building", "category").all()
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
            "statuses": Key.Status.choices,
            "buildings": buildings,
            "categories": categories,
        },
    )


@admin_required
def key_create(request):
    is_modal = request.GET.get("modal") == "1" or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    form = KeyForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            obj = form.save(commit=False)
            obj.created_by = request.user
            obj.save()
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
    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()

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
    if status_filter == "active":
        categories = categories.filter(is_active=True)
    elif status_filter == "inactive":
        categories = categories.filter(is_active=False)

    form = CategoryForm()
    create_url = reverse("keys:category_create")

    return render(
        request,
        "keys/category_list.html",
        {
            "categories": categories,
            "q": q,
            "status_filter": status_filter,
            "form": form,
            "create_url": create_url,
        },
    )


@admin_required
def category_create(request):
    is_modal = request.GET.get("modal") == "1" or request.headers.get("x-requested-with") == "XMLHttpRequest"
    form = CategoryForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            obj = form.save()
            messages.success(request, f"Category '{obj.name}' added.")
            if is_modal:
                return HttpResponse("OK", status=201)
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
            messages.success(request, f"Category '{obj.name}' updated.")
            if is_modal:
                return HttpResponse("OK", status=200)
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
    category = get_object_or_404(KeyCategory, pk=pk)
    force = request.POST.get("confirm") == "true" or request.POST.get("force") == "true"
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"

    try:
        new_state = toggle_category_active(category=category, force=force)
        state_str = "activated" if new_state else "deactivated"
        messages.success(request, f"Category '{category.name}' {state_str}.")
        if is_ajax:
            return JsonResponse({"success": True, "is_active": new_state})
    except CategoryInUseError as e:
        messages.error(request, str(e))
        if is_ajax:
            return JsonResponse({
                "error": str(e),
                "key_count": e.key_count,
                "borrowed_count": e.borrowed_count,
                "requires_confirm": e.borrowed_count == 0,
            }, status=422)

    return redirect("keys:category_list")
from accounts.permissions import admin_or_guard_required


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

    # B8.11: replace the hard keys[:200] slice with real pagination so the
    # full inventory stays auditable instead of being silently cut off.
    paginator = Paginator(keys, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # Keep search/filter params in pager links (drop only the page param).
    params = request.GET.copy()
    if "page" in params:
        del params["page"]
    querystring = params.urlencode()

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
