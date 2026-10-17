from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import role_required
from accounts.forms import BuildingForm
from .models import Building


@role_required("GUARD")
def building_list(request):
    return render(request, "facilities/building_list.html",
                  {"buildings": Building.objects.all()})


@role_required("GUARD")
def building_create(request):
    form = BuildingForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Building added.")
        return redirect("facilities:building_list")
    return render(request, "facilities/building_form.html", {"form": form})


@role_required("GUARD")
def building_update(request, pk):
    obj = get_object_or_404(Building, pk=pk)
    form = BuildingForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Building updated.")
        return redirect("facilities:building_list")
    return render(request, "facilities/building_form.html", {"form": form})
