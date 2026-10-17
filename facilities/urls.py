from django.urls import path
from . import views

app_name = "facilities"

urlpatterns = [
    path("buildings/", views.building_list, name="building_list"),
    path("buildings/new/", views.building_create, name="building_create"),
    path("buildings/<int:pk>/edit/", views.building_update, name="building_update"),
]
