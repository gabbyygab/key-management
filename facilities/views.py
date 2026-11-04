from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction as db_transaction
from django.db.models import (Case, CharField, Count, F, Prefetch, Q, Value,
                              When)
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.cache_utils import remember
from accounts.crud import (BULK_ACTIONS, archive_context, paginate,
                           bulk_delete_forever, bulk_response, bulk_selection,
                           bulk_summary, delete_forever, form_view, is_modal,
                           post_redirect, querystring_without_page)
from accounts.forms import BuildingForm, GuardPostForm, RoomBulkForm, RoomForm
from accounts.permissions import admin_required
from accounts.retention import mark_archived
from audit.services import Action, log_activity
from keys.models import Key
from keys.services import ensure_room_key
from scheduling.services import get_room_today_schedule
from .models import Building, GuardPost, Room

OUT_STATUSES = [Key.Status.BORROWED, Key.Status.OVERDUE]


# ---------------------------------------------------------------------------
# Buildings (F4.1)
# ---------------------------------------------------------------------------

@admin_required
def building_list(request):
    """Live buildings only. Archived ones live on building_archived."""
    return _building_list(request, archived=False)


@admin_required
def building_archived(request):
    """Archived buildings only, with Restore."""
    return _building_list(request, archived=True)


def _building_list(request, *, archived):
    q = request.GET.get("q", "").strip()
    # Live counts only, so the numbers match the lists the columns link to:
    # an archived room or key is reported on its own Archived page.
    buildings = Building.objects.annotate(
        room_count=Count("rooms", filter=Q(rooms__is_active=True), distinct=True),
        key_count=Count("rooms__keys", distinct=True,
                        filter=Q(rooms__is_active=True) &
                               ~Q(rooms__keys__status=Key.Status.INACTIVE)),
    ).filter(is_active=not archived)
    if q:
        buildings = buildings.filter(Q(code__icontains=q) | Q(name__icontains=q))
    return render(request, "facilities/building_list.html",
                  {"buildings": buildings, "q": q,
                   **archive_context(
                       request, archived=archived,
                       live_url=reverse("facilities:building_list"),
                       archived_url=reverse("facilities:building_archived"),
                       noun="buildings",
                       archived_count=Building.objects.filter(
                           is_active=False).count(),
                   )})


@admin_required
def building_create(request):
    return form_view(
        request,
        form=BuildingForm(request.POST or None),
        full_template="facilities/building_form.html",
        modal_template="facilities/_building_form_modal.html",
        context={"title": "Add building"},
        redirect_to="facilities:building_list",
        success_message=lambda b: f"Building {b.code} added.",
        created=True,
    )


@admin_required
def building_update(request, pk):
    obj = get_object_or_404(Building, pk=pk)
    return form_view(
        request,
        form=BuildingForm(request.POST or None, instance=obj),
        full_template="facilities/building_form.html",
        modal_template="facilities/_building_form_modal.html",
        context={"title": f"Edit {obj.code}", "instance": obj},
        redirect_to="facilities:building_list",
        success_message=lambda b: f"Building {b.code} updated.",
        created=False,
    )


def _building_blocker(building):
    """Why this building cannot be archived right now, or None."""
    out = Key.objects.filter(room__building=building, status__in=OUT_STATUSES).count()
    if out:
        return f"{building.code} has {out} key(s) still out"
    return None


def _set_building_archived(request, building, archived):
    """Flip is_active and write the audit row. The caller checks the blocker."""
    building.is_active = not archived
    # Stamps/clears the retention clock (accounts/retention.py): an archived
    # building is purged once ARCHIVE_RETENTION_DAYS have passed.
    building.save(update_fields=["is_active", *mark_archived(building, archived=archived)])
    verb = "archived" if archived else "restored"
    log_activity(request.user, Action.UPDATE, obj=building, request=request,
                 description=f"Building {building.code} {verb}.",
                 changes={"is_active": [str(archived), str(not archived)]})
    return verb


@admin_required
@require_POST
def building_toggle(request, pk):
    """B3.3: archive, not delete. Rooms PROTECT their building and keys PROTECT
    their room, so a hard DELETE is either refused by the database or takes
    borrow history with it. Archiving hides the building everywhere new while
    the history stays readable. Refused while a key in it is still out."""
    obj = get_object_or_404(Building, pk=pk)
    if obj.is_active:
        blocker = _building_blocker(obj)
        if blocker:
            messages.error(request, f"Cannot archive: {blocker}. Receive them first.")
            return redirect(post_redirect(request, "facilities:building_list"))
    verb = _set_building_archived(request, obj, archived=obj.is_active)
    messages.success(request, f"Building {obj.code} {verb}.")
    return redirect(post_redirect(request, "facilities:building_list"))


@admin_required
@require_POST
def building_delete(request, pk):
    """Delete an archived building for good, with its rooms, keys and their
    borrow history (B3.3 / retention). Refused while a key below it is out."""
    obj = get_object_or_404(Building, pk=pk)
    return delete_forever(request, obj, noun="building(s)",
                          redirect_to=post_redirect(request, "facilities:building_archived"))


@admin_required
@require_POST
def building_bulk(request):
    """Archive, restore or delete several buildings in one post (bulk-actions.js)."""
    action = request.POST.get("action", "")
    if action not in BULK_ACTIONS:
        return bulk_response(request, message="Unknown bulk action.", ok=False,
                             redirect_to=post_redirect(request, "facilities:building_list"))
    if action == "delete":
        return bulk_delete_forever(
            request, bulk_selection(request, Building.objects.all()),
            noun="building(s)",
            redirect_to=post_redirect(request, "facilities:building_archived"))
    archive = action == "archive"
    done, blocked = [], []
    for obj in bulk_selection(request, Building.objects.all()):
        if obj.is_active is not archive:
            continue  # already in the requested state
        if archive:
            why = _building_blocker(obj)
            if why:
                blocked.append(why)
                continue
        _set_building_archived(request, obj, archived=archive)
        done.append(obj)
    message = bulk_summary(done=done, blocked=blocked, noun="building(s)",
                           verb="archived" if archive else "restored")
    return bulk_response(request, message=message or "Nothing to update.",
                         ok=bool(done), blocked=blocked,
                         redirect_to=post_redirect(request, "facilities:building_list"))


# ---------------------------------------------------------------------------
# Rooms (B3.2 / F4.2)
# ---------------------------------------------------------------------------

@admin_required
def room_list(request):
    """Live rooms only. Archived ones live on room_archived."""
    return _room_list(request, archived=False)


@admin_required
def room_archived(request):
    """Archived rooms only, with Restore."""
    return _room_list(request, archived=True)


def _room_list(request, *, archived):
    q = request.GET.get("q", "").strip()
    building_id = request.GET.get("building", "").strip()
    room_type = request.GET.get("room_type", "").strip()
    status = request.GET.get("status", "").strip()

    rooms = (Room.objects.select_related("building", "department")
             .prefetch_related(Prefetch(
                 "keys",
                 queryset=Key.objects.exclude(status=Key.Status.INACTIVE).select_related("category").order_by("copy_number", "key_code")
             ))
             .annotate(key_count=Count(
                 "keys", filter=~Q(keys__status=Key.Status.INACTIVE)),
                 out_count=Count("keys", filter=Q(keys__status__in=OUT_STATUSES)))
             # Occupied is derived from live borrows, never stored: a room whose
             # key is out reads Occupied unless it is in Maintenance/Inactive.
             .annotate(effective_status=Case(
                 When(Q(status=Room.Status.AVAILABLE, out_count__gt=0),
                      then=Value(Room.Status.OCCUPIED)),
                 default=F("status"), output_field=CharField()))
             .filter(is_active=not archived)
             .order_by("building__code", "room_code", "-pk"))
    if q:
        rooms = rooms.filter(Q(room_code__icontains=q) | Q(name__icontains=q))
    if building_id.isdigit():
        rooms = rooms.filter(building_id=int(building_id))
    if room_type:
        rooms = rooms.filter(room_type=room_type)
    if status:
        rooms = rooms.filter(effective_status=status)

    page_obj = paginate(request, rooms, per_page=25)
    return render(request, "facilities/room_list.html", {
        "rooms": page_obj,
        "page_obj": page_obj,
        "querystring": querystring_without_page(request),
        "q": q, "building_id": building_id, "room_type": room_type, "status": status,
        "buildings": remember("rooms-filter-buildings", ["inventory"],
                              lambda: list(Building.objects.order_by("code"))),
        "room_types": Room.RoomType.choices,
        "statuses": Room.Status.choices,
        **archive_context(
            request, archived=archived,
            live_url=reverse("facilities:room_list"),
            archived_url=reverse("facilities:room_archived"),
            noun="rooms",
            archived_count=Room.objects.filter(is_active=False).count(),
        ),
    })


@admin_required
def room_create(request):
    initial = {}
    if request.method == "GET" and request.GET.get("building", "").isdigit():
        initial["building"] = request.GET["building"]

    # A new room mints its original key (one key, no copies) so it is ready to
    # hand over and appears on the Keys page straight away. See
    # keys.services.ensure_room_key; copies stay a separate, deliberate action.
    minted = {}

    def save(form):
        room = form.save()
        if room.is_active:
            minted["key"] = ensure_room_key(room=room, user=request.user)
        return room

    def message(room):
        key = minted.get("key")
        if key:
            return f"Room {room} added with key {key.key_code}."
        return f"Room {room} added."

    response = form_view(
        request,
        form=RoomForm(request.POST or None, initial=initial),
        full_template="facilities/room_form.html",
        modal_template="facilities/_room_form_modal.html",
        context={"title": "Add room"},
        redirect_to="facilities:room_list",
        success_message=message,
        created=True,
        save=save,
    )
    key = minted.get("key")
    if key:
        log_activity(request.user, Action.CREATE, obj=key, request=request,
                     description=f"Key {key.key_code} created with room {key.room}.")
    return response


@admin_required
def room_update(request, pk):
    obj = get_object_or_404(Room.objects.select_related("building"), pk=pk)
    return form_view(
        request,
        form=RoomForm(request.POST or None, instance=obj),
        full_template="facilities/room_form.html",
        modal_template="facilities/_room_form_modal.html",
        context={"title": f"Edit room {obj}", "instance": obj},
        redirect_to="facilities:room_list",
        success_message=lambda r: f"Room {r} updated.",
        created=False,
    )


def _room_blocker(room):
    """Why this room cannot be archived right now, or None."""
    out = room.keys.filter(status__in=OUT_STATUSES).count()
    if out:
        return f"{room} has {out} key(s) still out"
    return None


def _set_room_archived(request, room, archived):
    room.is_active = not archived
    room.save(update_fields=["is_active", "updated_at",
                             *mark_archived(room, archived=archived)])
    verb = "archived" if archived else "restored"
    log_activity(request.user, Action.UPDATE, obj=room, request=request,
                 description=f"Room {room} {verb}.",
                 changes={"is_active": [str(archived), str(not archived)]})
    return verb


@admin_required
@require_POST
def room_toggle(request, pk):
    """Archive, not delete: keys PROTECT the room and transactions PROTECT the
    keys, so the row has to stay for the history to stay."""
    obj = get_object_or_404(Room.objects.select_related("building"), pk=pk)
    if obj.is_active:
        blocker = _room_blocker(obj)
        if blocker:
            messages.error(request, f"Cannot archive: {blocker}. Receive them first.")
            return redirect(post_redirect(request, "facilities:room_list"))
    verb = _set_room_archived(request, obj, archived=obj.is_active)
    messages.success(request, f"Room {obj} {verb}.")
    return redirect(post_redirect(request, "facilities:room_list"))


@admin_required
@require_POST
def room_delete(request, pk):
    """Delete an archived room for good, with its keys, their borrow history
    and its schedule blocks (retention). Refused while a key is still out."""
    obj = get_object_or_404(Room.objects.select_related("building"), pk=pk)
    return delete_forever(request, obj, noun="room(s)",
                          redirect_to=post_redirect(request, "facilities:room_archived"))


@admin_required
@require_POST
def room_bulk(request):
    """Archive, restore or delete several rooms in one post (bulk-actions.js)."""
    action = request.POST.get("action", "")
    if action not in BULK_ACTIONS:
        return bulk_response(request, message="Unknown bulk action.", ok=False,
                             redirect_to=post_redirect(request, "facilities:room_list"))
    if action == "delete":
        return bulk_delete_forever(
            request, bulk_selection(request, Room.objects.select_related("building")),
            noun="room(s)",
            redirect_to=post_redirect(request, "facilities:room_archived"))
    archive = action == "archive"
    done, blocked = [], []
    for obj in bulk_selection(request, Room.objects.select_related("building")):
        if obj.is_active is not archive:
            continue
        if archive:
            why = _room_blocker(obj)
            if why:
                blocked.append(why)
                continue
        _set_room_archived(request, obj, archived=archive)
        done.append(obj)
    message = bulk_summary(done=done, blocked=blocked, noun="room(s)",
                           verb="archived" if archive else "restored")
    return bulk_response(request, message=message or "Nothing to update.",
                         ok=bool(done), blocked=blocked,
                         redirect_to=post_redirect(request, "facilities:room_list"))


@admin_required
def room_bulk_create(request):
    """F4.2: generate a numbered run of rooms (101-110) in one go. Codes that
    already exist in the building are skipped, never duplicated."""
    form = RoomBulkForm(request.POST or None)
    modal = is_modal(request)
    ctx = {"form": form, "is_modal": modal, "title": "Bulk add rooms"}
    if request.method == "POST":
        if form.is_valid():
            d = form.cleaned_data
            building = d["building"]
            prefix = (d.get("prefix") or "").strip()
            codes = [f"{prefix}{n}" for n in range(d["start_number"], d["end_number"] + 1)]
            existing = set(Room.objects.filter(building=building, room_code__in=codes)
                           .values_list("room_code", flat=True))
            with db_transaction.atomic():
                created = Room.objects.bulk_create([
                    Room(building=building, room_code=code, room_type=d["room_type"],
                         floor=d.get("floor"), capacity=d.get("capacity"),
                         department=d.get("department"))
                    for code in codes if code not in existing
                ])
                created_rooms = list(Room.objects.filter(
                    building=building, room_code__in=[c for c in codes if c not in existing]
                ))
                for r in created_rooms:
                    ensure_room_key(room=r, user=request.user)
            log_activity(request.user, Action.CREATE, obj=building, request=request,
                         description=f"Bulk-created {len(created)} room(s) in {building.code}: "
                                     f"{codes[0]}–{codes[-1]}.")
            msg = f"Created {len(created)} room(s) in {building.code}."
            if existing:
                msg += f" Skipped {len(existing)} that already existed."
            if modal:
                return HttpResponse(msg, status=201)
            messages.success(request, msg)
            return redirect(post_redirect(request, "facilities:room_list"))
        if modal:
            return render(request, "facilities/_room_bulk_modal.html", ctx, status=422)
    if modal:
        return render(request, "facilities/_room_bulk_modal.html", ctx)
    return render(request, "facilities/room_form.html", ctx)


@admin_required
@require_POST
def room_generate_key(request, pk):
    room = get_object_or_404(Room.objects.select_related("building"), pk=pk)
    key = ensure_room_key(room=room, user=request.user)
    if key:
        log_activity(request.user, Action.CREATE, obj=key, request=request,
                     description=f"Key {key.key_code} created for room {room}.")
        messages.success(request, f"Key {key.key_code} generated for room {room}.")
    else:
        messages.info(request, f"Room {room} already has a key or could not generate one.")
    return redirect(post_redirect(request, "facilities:room_list"))


@admin_required
def room_detail(request, pk):
    room = get_object_or_404(
        Room.objects.select_related("building"),
        pk=pk,
    )
    keys = room.keys.select_related("category").all().order_by("copy_number", "key_code")
    today_schedule = get_room_today_schedule(room)

    return render(request, "facilities/room_detail.html", {
        "room": room,
        "keys": keys,
        "today_schedule": today_schedule,
    })


# ---------------------------------------------------------------------------
# Guard posts (B2.5)
# ---------------------------------------------------------------------------

@admin_required
def post_list(request):
    """Live posts only. Archived ones live on post_archived."""
    return _post_list(request, archived=False)


@admin_required
def post_archived(request):
    """Archived posts only, with Restore."""
    return _post_list(request, archived=True)


def _post_list(request, *, archived):
    posts = (GuardPost.objects.select_related("building")
             .annotate(guard_count=Count("guards"))
             .filter(is_active=not archived).order_by("name", "-pk"))
    return render(request, "facilities/post_list.html", {
        "posts": posts,
        **archive_context(
            request, archived=archived,
            live_url=reverse("facilities:post_list"),
            archived_url=reverse("facilities:post_archived"),
            noun="guard posts",
            archived_count=GuardPost.objects.filter(is_active=False).count(),
        ),
    })


@admin_required
def post_create(request):
    return form_view(
        request,
        form=GuardPostForm(request.POST or None),
        full_template="facilities/building_form.html",
        modal_template="facilities/_post_form_modal.html",
        context={"title": "Add guard post"},
        redirect_to="facilities:post_list",
        success_message=lambda p: f"Guard post {p.name} added.",
        created=True,
    )


@admin_required
def post_update(request, pk):
    obj = get_object_or_404(GuardPost, pk=pk)
    return form_view(
        request,
        form=GuardPostForm(request.POST or None, instance=obj),
        full_template="facilities/building_form.html",
        modal_template="facilities/_post_form_modal.html",
        context={"title": f"Edit {obj.name}", "instance": obj},
        redirect_to="facilities:post_list",
        success_message=lambda p: f"Guard post {p.name} updated.",
        created=False,
    )


def _set_post_archived(request, post, archived):
    post.is_active = not archived
    post.save(update_fields=["is_active", *mark_archived(post, archived=archived)])
    verb = "archived" if archived else "restored"
    log_activity(request.user, Action.UPDATE, obj=post, request=request,
                 description=f"Guard post {post.name} {verb}.",
                 changes={"is_active": [str(archived), str(not archived)]})
    return verb


@admin_required
@require_POST
def post_toggle(request, pk):
    """Archive or restore. Guards assigned here keep the assignment."""
    obj = get_object_or_404(GuardPost, pk=pk)
    verb = _set_post_archived(request, obj, archived=obj.is_active)
    messages.success(request, f"Guard post {obj.name} {verb}.")
    return redirect(post_redirect(request, "facilities:post_list"))


@admin_required
@require_POST
def post_delete(request, pk):
    """Delete an archived post for good (retention). Assigned guards and shifts
    are detached, not deleted."""
    obj = get_object_or_404(GuardPost, pk=pk)
    return delete_forever(request, obj, noun="guard post(s)",
                          redirect_to=post_redirect(request, "facilities:post_archived"))


@admin_required
@require_POST
def post_bulk(request):
    """Archive, restore or delete several guard posts in one post (bulk-actions.js)."""
    action = request.POST.get("action", "")
    if action not in BULK_ACTIONS:
        return bulk_response(request, message="Unknown bulk action.", ok=False,
                             redirect_to=post_redirect(request, "facilities:post_list"))
    if action == "delete":
        return bulk_delete_forever(
            request, bulk_selection(request, GuardPost.objects.all()),
            noun="guard post(s)",
            redirect_to=post_redirect(request, "facilities:post_archived"))
    archive = action == "archive"
    done = []
    for obj in bulk_selection(request, GuardPost.objects.all()):
        if obj.is_active is not archive:
            continue
        _set_post_archived(request, obj, archived=archive)
        done.append(obj)
    message = bulk_summary(done=done, blocked=[], noun="guard post(s)",
                           verb="archived" if archive else "restored")
    return bulk_response(request, message=message or "Nothing to update.",
                         ok=bool(done),
                         redirect_to=post_redirect(request, "facilities:post_list"))
