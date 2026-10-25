from django.contrib import messages
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.forms import CategoryForm, KeyForm
from accounts.permissions import admin_required
from .models import Key, KeyCategory
from .services import CategoryInUseError, toggle_category_active


@admin_required
def key_list(request):
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    keys = Key.objects.select_related("room__building", "category").all()
    if q:
        keys = keys.filter(Q(key_code__icontains=q) | Q(room__room_code__icontains=q)
                           | Q(description__icontains=q))
    if status:
        keys = keys.filter(status=status)
    return render(request, "keys/key_list.html",
                  {"keys": keys[:200], "q": q, "status": status,
                   "statuses": Key.Status.choices})


@admin_required
def key_create(request):
    form = KeyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.created_by = request.user
        obj.save()
        messages.success(request, f"Key {obj.key_code} added.")
        return redirect("keys:key_list")
    return render(request, "keys/key_form.html", {"form": form, "title": "Add key"})


@admin_required
def key_update(request, pk):
    obj = get_object_or_404(Key, pk=pk)
    form = KeyForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Key {obj.key_code} updated.")
        return redirect("keys:key_list")
    return render(request, "keys/key_form.html", {"form": form, "title": "Edit key"})


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
    return render(
        request,
        "keys/key_availability.html",
        {
            "keys": keys[:200],
            "q": q,
            "status": status,
            "statuses": Key.Status.choices,
        },
    )
