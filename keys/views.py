from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from accounts.forms import CategoryForm, KeyForm
from accounts.permissions import admin_required
from .models import Key, KeyCategory


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
    return render(request, "keys/category_list.html",
                  {"categories": KeyCategory.objects.all()})


@admin_required
def category_create(request):
    form = CategoryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Category added.")
        return redirect("keys:category_list")
    return render(request, "keys/category_form.html", {"form": form, "title": "Add category"})


@admin_required
def category_update(request, pk):
    obj = get_object_or_404(KeyCategory, pk=pk)
    form = CategoryForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Category updated.")
        return redirect("keys:category_list")
    return render(request, "keys/category_form.html", {"form": form, "title": "Edit category"})


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
