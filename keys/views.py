from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import role_required
from accounts.forms import CategoryForm, KeyForm
from .models import Key, KeyCategory


@role_required("GUARD")
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


@role_required("GUARD")
def key_create(request):
    form = KeyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.created_by = request.user
        obj.save()
        messages.success(request, f"Key {obj.key_code} added.")
        return redirect("keys:key_list")
    return render(request, "keys/key_form.html", {"form": form, "title": "Add key"})


@role_required("GUARD")
def key_update(request, pk):
    obj = get_object_or_404(Key, pk=pk)
    form = KeyForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Key {obj.key_code} updated.")
        return redirect("keys:key_list")
    return render(request, "keys/key_form.html", {"form": form, "title": "Edit key"})


@role_required("GUARD")
def category_list(request):
    return render(request, "keys/category_list.html",
                  {"categories": KeyCategory.objects.all()})


@role_required("GUARD")
def category_create(request):
    form = CategoryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Category added.")
        return redirect("keys:category_list")
    return render(request, "keys/key_form.html", {"form": form, "title": "Add category"})
