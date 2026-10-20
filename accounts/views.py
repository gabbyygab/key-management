from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from .decorators import role_required
from .forms import BorrowerForm
from .models import Borrower
from .permissions import admin_required, guard_required


def home(request):
    if request.user.is_authenticated:
        return redirect(get_post_login_redirect(request.user))
    return render(request, "home.html")


def get_post_login_redirect(user):
    if user.is_superuser or user.role == "ADMIN":
        return "dashboard"
    return "transactions:borrow_dashboard"


def post_login_router(request):
    return redirect(get_post_login_redirect(request.user))


def permission_denied_view(request, exception=None):
    required_role = getattr(exception, "required_role", None)
    return render(request, "403.html", {"required_role": required_role}, status=403)


def login_view(request):
    if request.user.is_authenticated:
        return redirect(get_post_login_redirect(request.user))
    error = None
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        if user is not None:
            if not (user.is_superuser or user.role in {"ADMIN", "GUARD"}):
                error = "This account is not allowed to log in."
            else:
                login(request, user)
                return redirect(request.GET.get("next") or get_post_login_redirect(user))
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


@admin_required
def borrower_list(request):
    q = request.GET.get("q", "").strip()
    borrowers = Borrower.objects.select_related("department").all()
    if q:
        borrowers = borrowers.filter(
            Q(first_name__icontains=q) | Q(last_name__icontains=q)
            | Q(id_number__icontains=q))
    return render(request, "accounts/borrower_list.html",
                  {"borrowers": borrowers[:100], "q": q})


@admin_required
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


@admin_required
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


@admin_required
def borrower_toggle(request, pk):
    obj = get_object_or_404(Borrower, pk=pk)
    if request.method == "POST":
        obj.is_active = not obj.is_active
        obj.save(update_fields=["is_active"])
        messages.success(request, f"{obj} {'activated' if obj.is_active else 'deactivated'}.")
    return redirect("accounts:borrower_list")
