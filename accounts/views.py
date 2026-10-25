from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .decorators import role_required
from .forms import BorrowerForm
from .models import Borrower, User, Department
from .permissions import admin_required, guard_required
from .services import ApprovalError, approve_user, reject_user


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

    # ?next= is only honoured if it points back at this site. Redirecting to a
    # raw query parameter is an open redirect: a crafted link could bounce a
    # guard to an off-site look-alike straight after a real login.
    nxt = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if nxt and not url_has_allowed_host_and_scheme(
        nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        nxt = ""

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        if user is not None:
            if not (user.is_superuser or user.role in {"ADMIN", "GUARD"}):
                error = "This account is not allowed to log in."
            else:
                login(request, user)
                if nxt:
                    return redirect(nxt)
                return redirect(get_post_login_redirect(user))
        else:
            error = "Invalid credentials, or account not approved yet."

    # `next` goes back into the template so the form can carry it through the
    # POST; the form used to drop it, which sent every guard to the dashboard.
    return render(request, "accounts/login.html", {"error": error, "next": nxt})


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


@admin_required
def pending_user_list(request):
    q = request.GET.get("q", "").strip()
    role_filter = request.GET.get("role", "").strip()
    dept_filter = request.GET.get("department", "").strip()

    pending_users = User.objects.filter(
        approval_status=User.Approval.PENDING
    ).select_related("department")

    if q:
        pending_users = pending_users.filter(
            Q(username__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(email__icontains=q)
            | Q(employee_id__icontains=q)
        )
    if role_filter:
        pending_users = pending_users.filter(role=role_filter)
    if dept_filter:
        pending_users = pending_users.filter(department__id=dept_filter)

    pending_users = pending_users.order_by("-date_joined")
    departments = Department.objects.filter(is_active=True)

    return render(
        request,
        "accounts/pending_users.html",
        {
            "pending_users": pending_users,
            "roles": User.Role.choices,
            "departments": departments,
            "q": q,
            "role_filter": role_filter,
            "dept_filter": dept_filter,
        },
    )


@admin_required
@require_POST
def user_approve(request, pk):
    try:
        user = approve_user(user_id=pk, approved_by=request.user)
        messages.success(
            request,
            f"Approved registration for {user.full_name} ({user.get_role_display()}).",
        )
    except ApprovalError as e:
        messages.error(request, str(e))
    return redirect("accounts:pending_user_list")


@admin_required
@require_POST
def user_reject(request, pk):
    reason = request.POST.get("reason", "").strip()
    if not reason:
        messages.error(request, "A reason is required to reject an account registration.")
        return redirect("accounts:pending_user_list")

    try:
        user = reject_user(user_id=pk, rejected_by=request.user, reason=reason)
        messages.warning(
            request,
            f"Rejected registration for {user.full_name}. Reason: {reason}",
        )
    except ApprovalError as e:
        messages.error(request, str(e))
    return redirect("accounts:pending_user_list")


@admin_required
def user_list(request):
    """Admin view: Users & Guards list."""
    q = request.GET.get("q", "").strip()
    role_filter = request.GET.get("role", "").strip()
    users = User.objects.select_related("department").all()
    if q:
        users = users.filter(
            Q(username__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(employee_id__icontains=q)
            | Q(email__icontains=q)
        )
    if role_filter:
        users = users.filter(role=role_filter)
    users = users.order_by("-date_joined")
    return render(
        request,
        "accounts/user_list.html",
        {
            "users": users[:100],
            "roles": User.Role.choices,
            "q": q,
            "role_filter": role_filter,
        },
    )


@role_required("GUARD", "ADMIN")
def borrower_lookup(request):
    """Guard read-only borrower lookup."""
    q = request.GET.get("q", "").strip()
    borrowers = Borrower.objects.select_related("department").filter(is_active=True)
    if q:
        borrowers = borrowers.filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(id_number__icontains=q)
        )
    return render(
        request,
        "accounts/borrower_lookup.html",
        {"borrowers": borrowers[:100], "q": q},
    )
