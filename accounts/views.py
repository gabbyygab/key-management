from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from audit.services import Action, log_activity
from .crud import form_view, is_modal, querystring_without_page
from .forms import AdminPasswordResetForm, BorrowerForm, UserCreateForm, UserEditForm
from .models import Borrower, Department, User
from .permissions import admin_or_guard_required, admin_required
from .services import ApprovalError, approve_user, push_active, push_password, push_profile, reject_user
from .supabase_auth import SupabaseAuthError


def home(request):
    if request.user.is_authenticated:
        return redirect(get_post_login_redirect(request.user))
    return render(request, "home.html")


def get_post_login_redirect(user):
    if user.is_superuser or user.role == "ADMIN":
        return "dashboard"
    return "transactions:borrow_dashboard"


def post_login_router(request):
    if not request.user.is_authenticated:
        return redirect("accounts:login")
    return redirect(get_post_login_redirect(request.user))


def permission_denied_view(request, exception=None):
    required_role = getattr(exception, "required_role", None)
    return render(request, "403.html", {"required_role": required_role}, status=403)


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------

def _throttle_key(request, username):
    ip = request.META.get("REMOTE_ADDR", "")
    return f"login-fail:{ip}:{username.lower()}"


def _is_throttled(key):
    return cache.get(key, 0) >= settings.LOGIN_RATE_LIMIT


def _record_failure(key):
    # add() is a no-op if the key exists, so the window starts at the first
    # failure and is not extended by later ones.
    cache.add(key, 0, timeout=settings.LOGIN_RATE_WINDOW)
    try:
        cache.incr(key)
    except ValueError:  # expired between add() and incr()
        cache.set(key, 1, timeout=settings.LOGIN_RATE_WINDOW)


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

    status = 200
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        key = _throttle_key(request, username)

        # B7.4: refuse before even checking the password once the limit is hit.
        if _is_throttled(key):
            minutes = max(1, settings.LOGIN_RATE_WINDOW // 60)
            error = f"Too many failed sign-in attempts. Try again in about {minutes} minute{'s' if minutes != 1 else ''}."
            status = 429
        else:
            user = authenticate(request, username=username, password=password)
            if user is not None:
                if not (user.is_superuser or user.role in {"ADMIN", "GUARD"}):
                    error = "This account is not allowed to log in."
                else:
                    cache.delete(key)
                    login(request, user)
                    if nxt:
                        return redirect(nxt)
                    return redirect(get_post_login_redirect(user))
            elif getattr(request, "supabase_auth_unavailable", False):
                # Not the user's fault, so it does not count toward the lockout.
                error = "The sign-in service cannot be reached right now. Try again shortly."
                status = 503
            else:
                _record_failure(key)
                error = "Invalid credentials, or account not approved yet."

    # `next` goes back into the template so the form can carry it through the
    # POST; the form used to drop it, which sent every guard to the dashboard.
    return render(request, "accounts/login.html", {"error": error, "next": nxt}, status=status)


@require_POST
def logout_view(request):
    """B8.2: POST only. A GET logout is CSRF-able, and logging out closes the
    guard's shift — an <img src="/accounts/logout/"> could end a shift."""
    logout(request)
    return redirect("accounts:login")


# ---------------------------------------------------------------------------
# Borrowers (admin master data)
# ---------------------------------------------------------------------------

def _holding_annotation():
    from transactions.models import BorrowTransaction
    return Exists(BorrowTransaction.objects.filter(
        borrower=OuterRef("pk"),
        status__in=[BorrowTransaction.Status.ACTIVE, BorrowTransaction.Status.OVERDUE]))


@admin_required
def borrower_list(request):
    q = request.GET.get("q", "").strip()
    type_filter = request.GET.get("type", "").strip()
    dept_filter = request.GET.get("department", "").strip()
    auth_filter = request.GET.get("auth", "").strip()

    borrowers = (Borrower.objects.select_related("department")
                 .annotate(is_holding=_holding_annotation()))
    if q:
        borrowers = borrowers.filter(
            Q(first_name__icontains=q) | Q(last_name__icontains=q)
            | Q(id_number__icontains=q))
    if type_filter:
        borrowers = borrowers.filter(borrower_type=type_filter)
    if dept_filter.isdigit():
        borrowers = borrowers.filter(department_id=int(dept_filter))
    today = timezone.localdate()
    if auth_filter == "authorized":
        borrowers = borrowers.filter(is_authorized=True).filter(
            Q(authorized_until__isnull=True) | Q(authorized_until__gte=today))
    elif auth_filter == "unauthorized":
        borrowers = borrowers.filter(is_authorized=False)
    elif auth_filter == "expired":
        borrowers = borrowers.filter(is_authorized=True, authorized_until__lt=today)
    elif auth_filter == "holding":
        borrowers = borrowers.filter(is_holding=True)

    page_obj = Paginator(borrowers, 25).get_page(request.GET.get("page"))
    return render(request, "accounts/borrower_list.html", {
        "borrowers": page_obj, "page_obj": page_obj,
        "querystring": querystring_without_page(request),
        "q": q, "type_filter": type_filter, "dept_filter": dept_filter,
        "auth_filter": auth_filter, "today": today,
        "borrower_types": Borrower.BorrowerType.choices,
        "departments": Department.objects.filter(is_active=True),
    })


@admin_required
def borrower_create(request):
    return form_view(
        request,
        form=BorrowerForm(request.POST or None),
        full_template="accounts/borrower_form.html",
        modal_template="accounts/_borrower_form_modal.html",
        context={"title": "Add borrower"},
        redirect_to="accounts:borrower_list",
        success_message=lambda b: f"Borrower {b.full_name} added.",
        created=True,
    )


@admin_required
def borrower_update(request, pk):
    obj = get_object_or_404(Borrower, pk=pk)
    return form_view(
        request,
        form=BorrowerForm(request.POST or None, instance=obj),
        full_template="accounts/borrower_form.html",
        modal_template="accounts/_borrower_form_modal.html",
        context={"title": f"Edit {obj.full_name}", "instance": obj},
        redirect_to="accounts:borrower_list",
        success_message=lambda b: f"Borrower {b.full_name} updated.",
        created=False,
    )


@admin_required
@require_POST
def borrower_toggle(request, pk):
    """B8.3: POST only, no silent GET redirect."""
    obj = get_object_or_404(Borrower, pk=pk)
    if obj.is_active and obj.active_transaction():
        messages.error(request, f"{obj.full_name} still holds a key. Receive it before deactivating.")
        return redirect("accounts:borrower_list")
    obj.is_active = not obj.is_active
    obj.save(update_fields=["is_active"])
    state = "activated" if obj.is_active else "deactivated"
    log_activity(request.user, Action.UPDATE, obj=obj, request=request,
                 description=f"Borrower {obj.full_name} {state}.",
                 changes={"is_active": [str(not obj.is_active), str(obj.is_active)]})
    messages.success(request, f"{obj.full_name} {state}.")
    return redirect("accounts:borrower_list")


@admin_required
def borrower_history(request, pk):
    """F4.5 'View history' — the borrower's transactions, as a modal partial."""
    from transactions.models import BorrowTransaction
    borrower = get_object_or_404(Borrower.objects.select_related("department"), pk=pk)
    txns = (BorrowTransaction.objects.filter(borrower=borrower)
            .select_related("key__room__building", "released_by", "received_by")
            .order_by("-borrowed_at")[:50])
    template = ("accounts/_borrower_history_modal.html" if is_modal(request)
                else "accounts/borrower_history.html")
    return render(request, template, {"borrower": borrower, "txns": txns})


@admin_or_guard_required
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
    page_obj = Paginator(borrowers, 25).get_page(request.GET.get("page"))
    return render(request, "accounts/borrower_lookup.html", {
        "borrowers": page_obj,
        "page_obj": page_obj,
        "querystring": querystring_without_page(request),
        "q": q,
    })


# ---------------------------------------------------------------------------
# Users & guards (B2.1 / B2.2)
# ---------------------------------------------------------------------------

@admin_required
def user_list(request):
    """Admin view: Users & Guards list."""
    q = request.GET.get("q", "").strip()
    role_filter = request.GET.get("role", "").strip()
    approval_filter = request.GET.get("approval", "").strip()
    dept_filter = request.GET.get("department", "").strip()

    users = User.objects.select_related("department", "assigned_post").all()
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
    if approval_filter:
        users = users.filter(approval_status=approval_filter)
    if dept_filter.isdigit():
        users = users.filter(department_id=int(dept_filter))
    users = users.order_by("-date_joined")

    page_obj = Paginator(users, 25).get_page(request.GET.get("page"))
    return render(request, "accounts/user_list.html", {
        "users": page_obj,
        "page_obj": page_obj,
        "querystring": querystring_without_page(request),
        "roles": User.Role.choices,
        "approvals": User.Approval.choices,
        "departments": Department.objects.filter(is_active=True),
        "q": q,
        "role_filter": role_filter,
        "approval_filter": approval_filter,
        "dept_filter": dept_filter,
    })


def _push_or_fail(push, user, *args):
    """Run a Supabase Auth push; turn a refusal into a form error so the
    surrounding atomic block rolls the Django change back."""
    try:
        push(user, *args)
    except SupabaseAuthError as e:
        raise ValidationError(f"Supabase Auth refused the change: {e}") from None


@admin_required
def user_create(request):
    def save(form):
        # Admin-created users are pre-approved (B2.2).
        user = form.save(commit=False)
        user.approval_status = User.Approval.APPROVED
        user.approved_by = request.user
        user.approved_at = timezone.now()
        user.save()
        _push_or_fail(push_password, user, form.cleaned_data["password1"])
        return user

    return form_view(
        request,
        form=UserCreateForm(request.POST or None),
        full_template="accounts/user_form.html",
        modal_template="accounts/_user_form_modal.html",
        context={"title": "Add user"},
        redirect_to="accounts:user_list",
        success_message=lambda u: f"{u.full_name} ({u.get_role_display()}) created.",
        created=True,
        save=save,
    )


@admin_required
def user_update(request, pk):
    obj = get_object_or_404(User, pk=pk)
    form = UserEditForm(request.POST or None, instance=obj)
    # An admin demoting themselves would lock themselves out mid-session.
    if request.method == "POST" and obj.pk == request.user.pk and not request.user.is_superuser:
        if form.is_valid() and form.cleaned_data.get("role") != User.Role.ADMIN:
            form.add_error("role", "You cannot remove your own admin role.")

    def save(form):
        user = form.save()
        _push_or_fail(push_profile, user)
        return user

    return form_view(
        request,
        form=form,
        full_template="accounts/user_form.html",
        modal_template="accounts/_user_form_modal.html",
        context={"title": f"Edit {obj.full_name}", "instance": obj},
        redirect_to="accounts:user_list",
        success_message=lambda u: f"{u.full_name} updated.",
        created=False,
        save=save,
    )


@admin_required
@require_POST
def user_toggle(request, pk):
    obj = get_object_or_404(User, pk=pk)
    if obj.pk == request.user.pk:
        messages.error(request, "You cannot deactivate your own account.")
        return redirect("accounts:user_list")
    obj.is_active = not obj.is_active
    obj.save(update_fields=["is_active"])
    state = "activated" if obj.is_active else "deactivated"
    try:
        push_active(obj)
    except SupabaseAuthError as e:
        # Django's is_active is what the login checks, so the change stands.
        messages.warning(request, f"Supabase was not updated ({e}). Toggle again to retry.")
    if not obj.is_active:
        # A deactivated guard is off duty immediately.
        from transactions.models import GuardShift
        GuardShift.objects.filter(guard=obj, is_on_duty=True).update(
            is_on_duty=False, shift_end=timezone.now())
    log_activity(request.user, Action.UPDATE, obj=obj, request=request,
                 description=f"User {obj.username} {state}.",
                 changes={"is_active": [str(not obj.is_active), str(obj.is_active)]})
    messages.success(request, f"{obj.full_name} {state}.")
    return redirect("accounts:user_list")


@admin_required
def user_reset_password(request, pk):
    obj = get_object_or_404(User, pk=pk)
    form = AdminPasswordResetForm(obj, request.POST or None)
    modal = is_modal(request)
    ctx = {"form": form, "instance": obj, "is_modal": modal,
           "title": f"Reset password — {obj.full_name}"}
    if request.method == "POST":
        saved = False
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save()
                    _push_or_fail(push_password, obj, form.cleaned_data["new_password1"])
                saved = True
            except ValidationError as e:
                form.add_error(None, e)
        if saved:
            log_activity(request.user, Action.UPDATE, obj=obj, request=request,
                         description=f"Password reset for {obj.username}.")
            msg = f"Password reset for {obj.full_name}."
            if modal:
                return HttpResponse(msg, status=200)
            messages.success(request, msg)
            return redirect("accounts:user_list")
        if modal:
            return render(request, "accounts/_password_reset_modal.html", ctx, status=422)
    if modal:
        return render(request, "accounts/_password_reset_modal.html", ctx)
    return render(request, "accounts/user_form.html", ctx)


# ---------------------------------------------------------------------------
# Approval workflow (B2.3)
# ---------------------------------------------------------------------------

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
        log_activity(request.user, Action.APPROVE, obj=user, request=request,
                     description=f"Approved {user.username} as {user.get_role_display()}.")
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
        log_activity(request.user, Action.REJECT, obj=user, request=request,
                     description=f"Rejected {user.username}. Reason: {reason}")
        messages.warning(
            request,
            f"Rejected registration for {user.full_name}. Reason: {reason}",
        )
    except ApprovalError as e:
        messages.error(request, str(e))
    return redirect("accounts:pending_user_list")
