from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from accounts.forms import BuildingForm, RoomForm
from accounts.permissions import admin_required
from scheduling.services import get_room_today_schedule
from .models import Building, Room


@admin_required
def building_list(request):
    return render(request, "facilities/building_list.html",
                  {"buildings": Building.objects.all()})


@admin_required
def building_create(request):
    form = BuildingForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Building added.")
        return redirect("facilities:building_list")
    return render(request, "facilities/building_form.html", {"form": form})


@admin_required
def building_update(request, pk):
    obj = get_object_or_404(Building, pk=pk)
    form = BuildingForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Building updated.")
        return redirect("facilities:building_list")
    return render(request, "facilities/building_form.html", {"form": form})


@admin_required
def room_list(request):
    return render(request, "facilities/room_list.html",
                  {"rooms": Room.objects.select_related("building").all()})


@admin_required
def room_create(request):
    form = RoomForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        messages.success(request, f"Room {obj} added.")
        return redirect("facilities:room_list")
    return render(request, "facilities/room_form.html", {"form": form, "title": "Add room"})


@admin_required
def room_update(request, pk):
    obj = get_object_or_404(Room, pk=pk)
    form = RoomForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Room {obj} updated.")
        return redirect("facilities:room_list")
    return render(request, "facilities/room_form.html", {"form": form, "title": "Edit room"})


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
