"""Shared plumbing for the admin create/edit screens (F4.x).

Every master-data form answers two audiences from one view:

  * a plain GET/POST renders the full-page template (the no-JS fallback);
  * ``GET ?modal=1`` returns just the form partial for modal-form.js, and a
    modal POST answers 201/200 on success or 422 + the re-rendered partial
    (bound data + inline errors) on failure, so the modal stays open with the
    admin's typed input intact.

Business rules still live in each app's forms/services; this only translates
HTTP and writes the audit row.
"""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from audit.services import log_form_save
from . import retention


def is_modal(request):
    return (request.GET.get("modal") == "1"
            or request.headers.get("x-requested-with") == "XMLHttpRequest")


def form_view(request, *, form, full_template, modal_template, context=None,
              redirect_to, success_message, created, save=None):
    """Run a ModelForm through the modal/full-page contract.

    ``save(form)`` may be passed to customise how the object is persisted (set
    created_by, push the password to Supabase...). It must return the saved
    instance.
    ``success_message`` is a string or a callable taking the instance.
    """
    modal = is_modal(request)
    ctx = {"form": form, "is_modal": modal, **(context or {})}

    if request.method == "POST":
        if form.is_valid():
            # ``save`` may raise ValidationError after writing (e.g. Supabase
            # refused the account); the atomic block undoes the local write
            # and the error is shown on the form like any other.
            try:
                with transaction.atomic():
                    obj = save(form) if save else form.save()
            except ValidationError as e:
                form.add_error(None, e)
            else:
                log_form_save(request, form, obj, created=created)
                msg = success_message(obj) if callable(success_message) else success_message
                if modal:
                    return HttpResponse(msg, status=201 if created else 200)
                messages.success(request, msg)
                return redirect(redirect_to)
        if modal:
            return render(request, modal_template, ctx, status=422)

    if modal:
        return render(request, modal_template, ctx)
    return render(request, full_template, ctx)


def querystring_without_page(request):
    params = request.GET.copy()
    params.pop("page", None)
    return params.urlencode()


# Default list page size (guide.md §5 "List pages: pagination"). Every list
# page that adopts the shared pagination footer pages at this size.
LIST_PAGE_SIZE = 10


def paginate(request, queryset, per_page=LIST_PAGE_SIZE):
    """Page ``queryset`` by ``?page=`` (bad or out-of-range values clamp to a
    real page). Returns the Django Page for partials/pagination.html."""
    return Paginator(queryset, per_page).get_page(request.GET.get("page"))


# ---------------------------------------------------------------------------
# Bulk list actions (archive / restore)
#
# KEYTRACK never hard-deletes master data: Building, Room, KeyCategory and Key
# are all referenced by PROTECTed foreign keys (rooms -> building, keys ->
# room/category, transactions -> key), so a real DELETE would either be refused
# by the database or silently destroy borrow history. "Archive" is therefore
# the delete verb the UI offers: the row stops being offered anywhere new
# (borrow screen, room finder, pickers) while every past transaction that
# points at it stays readable. "Restore" puts it back.
#
# Both the single-row buttons and the multi-select toolbar (static/js/
# bulk-actions.js) post here. AJAX gets JSON; a no-JS POST gets a message and
# a redirect, so the toolbar still works with scripting off.
# ---------------------------------------------------------------------------

ARCHIVE_ACTIONS = ("archive", "restore")

# ... plus the one real delete. "Delete forever" is the manual counterpart of
# the 30-day sweep (accounts/retention.py): same guard, same cascade, same
# audit row, no waiting. It is offered on the Archived pages only — a live row
# has to be archived first, so an accidental click always has Restore in front
# of it — and it is admin-only and POST-only like every other row action.
BULK_ACTIONS = ARCHIVE_ACTIONS + ("delete",)


# Archived rows live on their own page.
#
# Every list page comes in two halves that share one view and one template:
# the live list (``archived=False``) hides archived rows entirely, and the
# Archived page (``archived=True``) shows nothing but archived rows. Keeping
# one view means the filters, pagination, bulk toolbar and row actions behave
# the same on both. ``bulk-actions.js`` reads ``data-bulk-state`` per row, so
# the toolbar on the Archived page offers Restore only, with no extra JS.
def archive_context(request, *, archived, live_url, archived_url, noun,
                    archived_count=None):
    """Context for partials/archive_switch.html plus the ``archived`` flag.

    ``retention_days`` is what the Archived page warns with: archiving is a
    soft delete *with a deadline* now (accounts/retention.py), so the page has
    to say that the rows go for good after that many days. 0 means retention is
    off and the partial says nothing.
    """
    return {
        "archived": archived,
        "archive_live_url": live_url,
        "archive_archived_url": archived_url,
        "archive_noun": noun,
        "archived_count": archived_count,
        "retention_days": retention.retention_days(),
    }


def post_redirect(request, default):
    """Where a row or bulk action should land after a non-AJAX POST.

    An action fired from the Archived page has to come back to the Archived
    page, not to the live list, so the form posts its own path as ``next``.
    Anything that is not a local path falls back to ``default``.
    """
    nxt = request.POST.get("next", "")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts=None,
                                               require_https=request.is_secure()):
        return nxt
    return default


def is_ajax(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def bulk_selection(request, queryset):
    """The rows a bulk form checked, in list order. Unknown ids are ignored."""
    ids = [int(v) for v in request.POST.getlist("ids") if v.isdigit()]
    return list(queryset.filter(pk__in=ids))


def bulk_response(request, *, message, redirect_to, ok=True, blocked=None):
    """One plain summary for both audiences (AJAX JSON / redirect + message)."""
    if is_ajax(request):
        return JsonResponse(
            {"ok": ok, "message": message, "blocked": blocked or []},
            status=200 if ok else 422,
        )
    (messages.success if ok else messages.error)(request, message)
    return redirect(redirect_to)


def delete_forever(request, obj, *, noun, redirect_to):
    """Row action: delete one archived row and everything below it.

    Answers both audiences the same way the bulk endpoints do — JSON for the
    AJAX caller (200 done / 422 refused), a message plus a redirect for a no-JS
    POST. The refusal reasons come from retention.purge_one: the row is not
    archived, a key below it is still out, or a transaction below it is open.
    """
    outcome = retention.purge_one(obj, user=request.user, request=request)
    if outcome.purged:
        message = (f"{outcome.label} deleted for good "
                   f"({outcome.rows} row(s) removed).")
    else:
        message = f"Cannot delete {outcome.label}: {outcome.blocked}."
    return bulk_response(request, message=message, ok=outcome.purged,
                         blocked=[] if outcome.purged else [outcome.blocked],
                         redirect_to=redirect_to)


def bulk_delete_forever(request, objects, *, noun, redirect_to):
    """The same, for a multi-select. Rows a rule refuses are skipped, not fatal."""
    done, blocked = [], []
    for obj in objects:
        outcome = retention.purge_one(obj, user=request.user, request=request)
        (done if outcome.purged else blocked).append(outcome)
    message = bulk_summary(done=done, blocked=[o.blocked for o in blocked],
                           noun=noun, verb="deleted for good")
    return bulk_response(request, message=message or "Nothing to delete.",
                         ok=bool(done), blocked=[o.blocked for o in blocked],
                         redirect_to=redirect_to)


def bulk_summary(*, done, blocked, noun, verb):
    """'3 buildings archived. 1 skipped: SCI has 2 keys still out.'"""
    parts = []
    if done:
        parts.append(f"{len(done)} {noun} {verb}.")
    if blocked:
        shown = "; ".join(blocked[:3])
        if len(blocked) > 3:
            shown += f"; and {len(blocked) - 3} more"
        parts.append(f"{len(blocked)} skipped — {shown}.")
    return " ".join(parts)
