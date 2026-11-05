from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Q
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from audit.services import Action, log_activity
from .crud import (BULK_ACTIONS, archive_context,
                   bulk_delete_forever, bulk_response, bulk_selection,
                   bulk_summary, delete_forever, form_view, is_modal,
                   paginate, post_redirect, querystring_without_page)
from . import lockout
from .forms import (AdminPasswordResetForm, BorrowerForm, OwnPasswordChangeForm,
                    UserCreateForm, UserEditForm)
from .models import Borrower, Department, User
from .permissions import admin_or_guard_required, admin_required
from .retention import mark_archived
from .services import push_active, push_delete, push_password, push_profile
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

@login_not_required
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
        account = lockout.canonical(username)

        # B7.4: refuse before even checking the password while the account is locked.
        if lockout.is_locked(account):
            error = f"Too many failed sign-in attempts. Try again in about {lockout.minutes_text()}."
            status = 429
        else:
            user = authenticate(request, username=username, password=password)
            if user is not None:
                if not (user.is_superuser or user.role in {"ADMIN", "GUARD"}):
                    error = "This account is not allowed to log in."
                else:
                    lockout.clear(account)
                    login(request, user)
                    if nxt:
                        return redirect(nxt)
                    return redirect(get_post_login_redirect(user))
            elif getattr(request, "supabase_auth_unavailable", False):
                # Not the user's fault, so it does not count toward the lockout.
                error = "The sign-in service cannot be reached right now. Try again shortly."
                status = 503
            elif _is_deactivated(username, password):
                # Right password, but an admin switched the account off. Only
                # said once the password matches, so the message never tells a
                # stranger which usernames exist or are deactivated.
                error = "Your account has been deactivated by an administrator. Contact your admin to restore access."
                status = 403
            else:
                lockout.record_failure(account)
                left = lockout.attempts_left(account)
                error = ("Invalid credentials." if left > 0 else
                         "Invalid credentials. This account is now locked for "
                         f"about {lockout.minutes_text()}.")
                if 0 < left < settings.LOGIN_RATE_LIMIT:
                    error += f" {left} attempt{'s' if left != 1 else ''} left."

    # `next` goes back into the template so the form can carry it through the
    # POST; the form used to drop it, which sent every guard to the dashboard.
    return render(request, "accounts/login.html", {"error": error, "next": nxt}, status=status)


def _is_deactivated(identifier, password):
    """True when the login names an inactive user and the password is theirs.

    authenticate() returns None for inactive users before the password is even
    checked (and Supabase bans them), so the local hash answers here. Every
    password change in KEYTRACK sets that hash, so it matches the real one.
    """
    identifier = identifier.strip()
    if not identifier or not password:
        return False
    user = User.objects.filter(username=identifier).first()
    if user is None and "@" in identifier:
        user = User.objects.filter(email__iexact=identifier).first()
    return user is not None and not user.is_active and user.check_password(password)


@require_POST
def logout_view(request):
    """B8.2: POST only. A GET logout is CSRF-able, and logging out closes the
    guard's shift — an <img src="/accounts/logout/"> could end a shift."""
    logout(request)
    # After logout() the session is a fresh, empty one, so the message is
    # stored there and the login page shows it as a toast.
    messages.success(request, "You have been signed out.")
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
    """Live borrowers only. Archived ones live on borrower_archived."""
    return _borrower_list(request, archived=False)


@admin_required
def borrower_archived(request):
    """Archived borrowers only, with Restore.

    A borrower is never hard-deleted: BorrowTransaction PROTECTs the row, so
    archiving (is_active=False) is what takes them out of the guard's search.
    """
    return _borrower_list(request, archived=True)


def _borrower_list(request, *, archived):
    q = request.GET.get("q", "").strip()
    type_filter = request.GET.get("type", "").strip()
    dept_filter = request.GET.get("department", "").strip()
    auth_filter = request.GET.get("auth", "").strip()

    borrowers = (Borrower.objects.select_related("department")
                 .annotate(is_holding=_holding_annotation())
                 .filter(is_active=not archived))
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

    page_obj = paginate(request, borrowers.order_by("last_name", "first_name", "-pk"),
                         per_page=25)
    return render(request, "accounts/borrower_list.html", {
        "borrowers": page_obj, "page_obj": page_obj,
        "querystring": querystring_without_page(request),
        "q": q, "type_filter": type_filter, "dept_filter": dept_filter,
        "auth_filter": auth_filter, "today": today,
        "borrower_types": Borrower.BorrowerType.choices,
        "departments": Department.objects.filter(is_active=True),
        **archive_context(
            request, archived=archived,
            live_url=reverse("accounts:borrower_list"),
            archived_url=reverse("accounts:borrower_archived"),
            noun="borrowers",
            archived_count=Borrower.objects.filter(is_active=False).count(),
        ),
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


def _borrower_blocker(borrower):
    """Why this borrower cannot be archived right now, or None.

    A borrower holding a key has to hand it back first: the open transaction
    is what the POS looks at, and archiving would hide the holder from the
    guard who needs to receive the key.
    """
    if borrower.active_transaction():
        return f"{borrower.full_name} still holds a key"
    return None


def _set_borrower_archived(request, borrower, *, archived):
    """Flip is_active and write the audit row. The caller checks the blocker."""
    borrower.is_active = not archived
    # Also stamps/clears the retention clock (accounts/retention.py).
    borrower.save(update_fields=["is_active", *mark_archived(borrower, archived=archived)])
    state = "archived" if archived else "restored"
    log_activity(request.user, Action.UPDATE, obj=borrower, request=request,
                 description=f"Borrower {borrower.full_name} {state}.",
                 changes={"is_active": [str(archived), str(not archived)]})
    return state


@admin_required
@require_POST
def borrower_toggle(request, pk):
    """B8.3: POST only, no silent GET redirect."""
    obj = get_object_or_404(Borrower, pk=pk)
    back = post_redirect(request, "accounts:borrower_list")
    if obj.is_active:
        why = _borrower_blocker(obj)
        if why:
            messages.error(request, f"{why}. Receive it before archiving.")
            return redirect(back)
    state = _set_borrower_archived(request, obj, archived=obj.is_active)
    messages.success(request, f"{obj.full_name} {state}.")
    return redirect(back)


@admin_required
@require_POST
def borrower_delete(request, pk):
    """Delete an archived borrower for good, with their borrow history
    (retention). Refused while they still hold a key."""
    obj = get_object_or_404(Borrower, pk=pk)
    return delete_forever(request, obj, noun="borrower(s)",
                          redirect_to=post_redirect(request, "accounts:borrower_archived"))


@admin_required
@require_POST
def borrower_bulk(request):
    """Archive or restore several borrowers in one post (bulk-actions.js).

    There is no hard delete here: transactions PROTECT the borrower row, so
    archiving is the bulk verb and Restore undoes it.
    """
    action = request.POST.get("action", "")
    back = post_redirect(request, "accounts:borrower_list")
    if action not in BULK_ACTIONS:
        return bulk_response(request, message="Unknown bulk action.", ok=False,
                             redirect_to=back)
    if action == "delete":
        return bulk_delete_forever(
            request, bulk_selection(request, Borrower.objects.all()),
            noun="borrower(s)",
            redirect_to=post_redirect(request, "accounts:borrower_archived"))
    archive = action == "archive"
    done, blocked = [], []
    for obj in bulk_selection(request, Borrower.objects.all()):
        if obj.is_active is not archive:
            continue  # already in the requested state
        if archive:
            why = _borrower_blocker(obj)
            if why:
                blocked.append(why)
                continue
        _set_borrower_archived(request, obj, archived=archive)
        done.append(obj)
    message = bulk_summary(done=done, blocked=blocked, noun="borrower(s)",
                           verb="archived" if archive else "restored")
    return bulk_response(request, message=message or "Nothing to update.",
                         ok=bool(done), blocked=blocked, redirect_to=back)


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
    borrowers = borrowers.order_by("last_name", "first_name", "-pk")
    page_obj = paginate(request, borrowers)
    from scheduling.services import faculty_schedule
    faculty = [b for b in page_obj if b.borrower_type == Borrower.BorrowerType.FACULTY]
    held = faculty_schedule(faculty)
    for b in faculty:
        b.today_blocks = [x for x in held[b.pk] if x.is_today]
    return render(request, "accounts/borrower_lookup.html", {
        "borrowers": page_obj,
        "page_obj": page_obj,
        "querystring": querystring_without_page(request),
        "q": q,
    })


# ---------------------------------------------------------------------------
# Users & guards (B2.1 / B2.2)
# ---------------------------------------------------------------------------

def _on_duty_annotation():
    from transactions.models import GuardShift
    return Exists(GuardShift.objects.filter(guard=OuterRef("pk"), is_on_duty=True))


def _on_duty_block(request, obj, verb):
    """Refuse an edit/delete while the account is on duty.

    Returns the refusal response when blocked, else None. The open shift and
    its transactions point at this account, so it is only changed off duty.
    The list already hides both actions for an on-duty row; this covers a
    stale page or a hand-made request.
    """
    if not obj.is_on_duty:
        return None
    reason = (f"{obj.full_name} is on duty right now. They must sign out "
              f"before you can {verb} the account.")
    if is_modal(request):
        # 422 on a POST so modal-form.js does not read it as a saved form;
        # 200 on the GET that only loads the form.
        return render(request, "accounts/_blocked_modal.html",
                      {"reason": reason, "title": f"Cannot {verb} this account"},
                      status=422 if request.method == "POST" else 200)
    messages.error(request, reason)
    return redirect("accounts:user_list")


@admin_required
def user_list(request):
    """Admin view: Users & Guards list."""
    q = request.GET.get("q", "").strip()
    role_filter = request.GET.get("role", "").strip()
    dept_filter = request.GET.get("department", "").strip()

    # on_duty drives the row: a guard with an open shift cannot be edited or
    # deleted, so the template disables both actions for that row.
    users = (User.objects.select_related("department", "assigned_post")
             .annotate(on_duty=_on_duty_annotation()))
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
    if dept_filter.isdigit():
        users = users.filter(department_id=int(dept_filter))
    users = users.order_by("-date_joined", "-pk")

    page_obj = paginate(request, users)
    # Only the 10 rows on screen are looked up. `lock_seconds` drives the red
    # "Locked" badge and its live countdown (static/js/lock-countdown.js).
    for u in page_obj:
        u.lock_seconds = lockout.remaining(u.username)
    return render(request, "accounts/user_list.html", {
        "users": page_obj,
        "page_obj": page_obj,
        "querystring": querystring_without_page(request),
        "roles": User.Role.choices,
        "departments": Department.objects.filter(is_active=True),
        "q": q,
        "role_filter": role_filter,
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
        # Admin-created users start with the default password, which they
        # change themselves after signing in.
        user = form.save()
        _push_or_fail(push_password, user, settings.DEFAULT_USER_PASSWORD)
        return user

    return form_view(
        request,
        form=UserCreateForm(request.POST or None),
        full_template="accounts/user_form.html",
        modal_template="accounts/_user_form_modal.html",
        context={"title": "Add user",
                 "subtitle": f"Signs in with the default password "
                             f"{settings.DEFAULT_USER_PASSWORD} and should change it after."},
        redirect_to="accounts:user_list",
        success_message=lambda u: f"{u.full_name} ({u.get_role_display()}) created.",
        created=True,
        save=save,
    )


@admin_required
def user_update(request, pk):
    obj = get_object_or_404(User, pk=pk)
    blocked = _on_duty_block(request, obj, "edit")
    if blocked:
        return blocked
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
@require_POST
def user_delete(request, pk):
    """Permanently remove an account.

    Refused while the account is on duty, for your own account, and when the
    row is referenced by a recorded shift or transaction (those FKs are
    PROTECT, so the history would otherwise be destroyed or the delete would
    crash) — deactivate instead.
    """
    obj = get_object_or_404(User, pk=pk)
    if obj.pk == request.user.pk:
        messages.error(request, "You cannot delete your own account.")
        return redirect("accounts:user_list")
    blocked = _on_duty_block(request, obj, "delete")
    if blocked:
        return blocked

    name, username, role, pk = obj.full_name, obj.username, obj.get_role_display(), obj.pk
    supabase_uid = obj.supabase_uid
    try:
        with transaction.atomic():
            obj.delete()
    except ProtectedError:
        messages.error(request, f"{name} has recorded shifts or transactions and cannot be "
                                f"deleted. Deactivate the account instead.")
        return redirect("accounts:user_list")

    if supabase_uid:
        # The Django row is gone either way; a failed Supabase delete only
        # leaves an orphaned auth user, so it is a warning, not a rollback.
        try:
            push_delete(supabase_uid)
        except SupabaseAuthError as e:
            messages.warning(request, f"The Supabase Auth user was not removed ({e}).")

    log_activity(request.user, Action.DELETE, request=request,
                 description=f"User {username} (#{pk}, {role}) deleted.")
    messages.success(request, f"{name} deleted.")
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


@admin_or_guard_required
def password_change(request):
    """Any signed-in admin or guard changes their own password, in Django and
    Supabase together. Wrong current passwords share the login lockout, so a
    borrowed session cannot be used to guess the password."""
    user = request.user
    form = OwnPasswordChangeForm(user, request.POST or None, request=request)
    forced = user.must_change_password
    rules = "Use at least 8 characters, not only numbers, and not the default password."
    ctx = {"form": form,
           "title": "Choose your own password" if forced else "Change password",
           "subtitle": (f"You signed in with a temporary password set by an admin. "
                        f"Replace it to continue. {rules}") if forced else rules,
           "cancel_url": get_post_login_redirect(user),
           "hide_cancel": forced,
           "confirm_message": "Use the new password from your next sign-in.",
           "confirm_title": "Change your password?", "confirm_label": "Change password",
           # Nothing else sits on this page, so centre the card vertically too.
           "center_page": True}
    status = 200
    if request.method == "POST":
        account = user.username.lower()
        if lockout.is_locked(account):
            # Checked before the form, so a locked-out session never even
            # reaches authenticate().
            messages.error(request, f"Too many wrong passwords. Try again in about "
                                    f"{lockout.minutes_text()}.")
            ctx["form"] = OwnPasswordChangeForm(user, request=request)
            status = 429
        elif form.is_valid():
            try:
                with transaction.atomic():
                    form.save()
                    _push_or_fail(push_password, user, form.cleaned_data["new_password1"])
            except ValidationError as e:
                form.add_error(None, e)
                status = 422
            else:
                lockout.clear(account)
                update_session_auth_hash(request, user)  # stay signed in
                log_activity(user, Action.UPDATE, obj=user, request=request,
                             description=f"{user.username} changed their own password.")
                messages.success(request, "Your password has been changed.")
                return redirect(get_post_login_redirect(user))
        else:
            if "current_password" in form.errors:
                lockout.record_failure(account)
            status = 422
    return render(request, "accounts/user_form.html", ctx, status=status)
