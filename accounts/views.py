from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from .decorators import role_required
from .forms import BorrowerForm
from .models import Borrower


def home(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(request, "home.html")


def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    error = None
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        if user is not None:
            if not (user.is_superuser or user.role == "GUARD"):
                error = "Only guard accounts can log in here. Admins use /admin/."
            else:
                login(request, user)
                return redirect(request.GET.get("next") or "dashboard")
        else:
            error = "Invalid credentials, or account not approved yet."
    return render(request, "accounts/login.html", {"error": error})


def logout_view(request):
    if request.method == "POST":
        logout(request)
        return redirect("accounts:login")
    # Allow GET logout for guard convenience (link click).
    logout(request)
    return redirect("accounts:login")


@role_required("GUARD")
def borrower_list(request):
    q = request.GET.get("q", "").strip()
    borrowers = Borrower.objects.select_related("department").all()
    if q:
        borrowers = borrowers.filter(
            Q(first_name__icontains=q) | Q(last_name__icontains=q)
            | Q(id_number__icontains=q))
    return render(request, "accounts/borrower_list.html",
                  {"borrowers": borrowers[:100], "q": q})


@role_required("GUARD")
def borrower_create(request):
    if request.method == "POST":
        form = BorrowerForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "User added.")
            return redirect("accounts:borrower_list")
    else:
        form = BorrowerForm()
    return render(request, "accounts/borrower_form.html", {"form": form, "title": "Add user"})


@role_required("GUARD")
def borrower_update(request, pk):
    obj = get_object_or_404(Borrower, pk=pk)
    if request.method == "POST":
        form = BorrowerForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "User updated.")
            return redirect("accounts:borrower_list")
    else:
        form = BorrowerForm(instance=obj)
    return render(request, "accounts/borrower_form.html", {"form": form, "title": "Edit user"})


@role_required("GUARD")
def borrower_toggle(request, pk):
    obj = get_object_or_404(Borrower, pk=pk)
    if request.method == "POST":
        obj.is_active = not obj.is_active
        obj.save(update_fields=["is_active"])
        messages.success(request, f"{obj} {'activated' if obj.is_active else 'deactivated'}.")
    return redirect("accounts:borrower_list")
