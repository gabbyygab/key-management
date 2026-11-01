from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction as db_transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.crud import form_view, is_modal, querystring_without_page
from accounts.forms import BuildingForm, GuardPostForm, RoomBulkForm, RoomForm
from accounts.permissions import admin_required
from audit.services import Action, log_activity
from keys.models import Key
from scheduling.services import get_room_today_schedule
from .models import Building, GuardPost, Room

OUT_STATUSES = [Key.Status.BORROWED, Key.Status.OVERDUE]


# ---------------------------------------------------------------------------
# Buildings (F4.1)
# ---------------------------------------------------------------------------

@admin_required
def building_list(request):
    q = request.GET.get("q", "").strip()
    state = request.GET.get("state", "").strip()
    buildings = Building.objects.annotate(
        room_count=Count("rooms", distinct=True),
        key_count=Count("rooms__keys", distinct=True),
    )
    if q:
        buildings = buildings.filter(Q(code__icontains=q) | Q(name__icontains=q))
    if state == "active":
        buildings = buildings.filter(is_active=True)
    elif state == "inactive":
        buildings = buildings.filter(is_active=False)
    return render(request, "facilities/building_list.html",
                  {"buildings": buildings, "q": q, "state": state})


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


@admin_required
@require_POST
def building_toggle(request, pk):
    """B3.3: soft delete. Buildings are PROTECTed by rooms, so they are never
    hard-deleted; deactivating is refused while a key in it is still out."""
    obj = get_object_or_404(Building, pk=pk)
    if obj.is_active:
        out = Key.objects.filter(room__building=obj, status__in=OUT_STATUSES).count()
        if out:
            messages.error(request, f"Cannot deactivate {obj.code}: {out} key(s) in this "
                                    "building are still out. Receive them first.")
            return redirect("facilities:building_list")
    obj.is_active = not obj.is_active
    obj.save(update_fields=["is_active"])
    state = "activated" if obj.is_active else "deactivated"
    log_activity(request.user, Action.UPDATE, obj=obj, request=request,
                 description=f"Building {obj.code} {state}.",
                 changes={"is_active": [str(not obj.is_active), str(obj.is_active)]})
    messages.success(request, f"Building {obj.code} {state}.")
    return redirect("facilities:building_list")


# ---------------------------------------------------------------------------
# Rooms (B3.2 / F4.2)
# ---------------------------------------------------------------------------

@admin_required
def room_list(request):
    q = request.GET.get("q", "").strip()
    building_id = request.GET.get("building", "").strip()
    room_type = request.GET.get("room_type", "").strip()
    status = request.GET.get("status", "").strip()

    rooms = (Room.objects.select_related("building")
             .annotate(key_count=Count("keys"))
             .order_by("building__code", "room_code"))
    if q:
        rooms = rooms.filter(Q(room_code__icontains=q) | Q(name__icontains=q))
    if building_id.isdigit():
        rooms = rooms.filter(building_id=int(building_id))
    if room_type:
        rooms = rooms.filter(room_type=room_type)
    if status:
        rooms = rooms.filter(status=status)

    page_obj = Paginator(rooms, 25).get_page(request.GET.get("page"))
    return render(request, "facilities/room_list.html", {
        "rooms": page_obj,
        "page_obj": page_obj,
        "querystring": querystring_without_page(request),
        "q": q, "building_id": building_id, "room_type": room_type, "status": status,
        "buildings": Building.objects.order_by("code"),
        "room_types": Room.RoomType.choices,
        "statuses": Room.Status.choices,
    })


@admin_required
def room_create(request):
    initial = {}
    if request.method == "GET" and request.GET.get("building", "").isdigit():
        initial["building"] = request.GET["building"]
    return form_view(
        request,
        form=RoomForm(request.POST or None, initial=initial),
        full_template="facilities/room_form.html",
        modal_template="facilities/_room_form_modal.html",
        context={"title": "Add room"},
        redirect_to="facilities:room_list",
        success_message=lambda r: f"Room {r} added.",
        created=True,
    )


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


@admin_required
@require_POST
def room_toggle(request, pk):
    obj = get_object_or_404(Room.objects.select_related("building"), pk=pk)
    if obj.is_active:
        out = obj.keys.filter(status__in=OUT_STATUSES).count()
        if out:
            messages.error(request, f"Cannot deactivate {obj}: {out} of its key(s) are still out.")
            return redirect("facilities:room_list")
    obj.is_active = not obj.is_active
    obj.save(update_fields=["is_active", "updated_at"])
    state = "activated" if obj.is_active else "deactivated"
    log_activity(request.user, Action.UPDATE, obj=obj, request=request,
                 description=f"Room {obj} {state}.",
                 changes={"is_active": [str(not obj.is_active), str(obj.is_active)]})
    messages.success(request, f"Room {obj} {state}.")
    return redirect("facilities:room_list")


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
                         floor=d.get("floor"), capacity=d.get("capacity"))
                    for code in codes if code not in existing
                ])
            log_activity(request.user, Action.CREATE, obj=building, request=request,
                         description=f"Bulk-created {len(created)} room(s) in {building.code}: "
                                     f"{codes[0]}–{codes[-1]}.")
            msg = f"Created {len(created)} room(s) in {building.code}."
            if existing:
                msg += f" Skipped {len(existing)} that already existed."
            if modal:
                return HttpResponse(msg, status=201)
            messages.success(request, msg)
            return redirect("facilities:room_list")
        if modal:
            return render(request, "facilities/_room_bulk_modal.html", ctx, status=422)
    if modal:
        return render(request, "facilities/_room_bulk_modal.html", ctx)
    return render(request, "facilities/room_form.html", ctx)


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
    posts = (GuardPost.objects.select_related("building")
             .annotate(guard_count=Count("guards")))
    return render(request, "facilities/post_list.html", {"posts": posts})


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


@admin_required
@require_POST
def post_toggle(request, pk):
    obj = get_object_or_404(GuardPost, pk=pk)
    obj.is_active = not obj.is_active
    obj.save(update_fields=["is_active"])
    state = "activated" if obj.is_active else "deactivated"
    log_activity(request.user, Action.UPDATE, obj=obj, request=request,
                 description=f"Guard post {obj.name} {state}.")
    messages.success(request, f"Guard post {obj.name} {state}.")
    return redirect("facilities:post_list")
