from django.urls import path
from . import views

app_name = "facilities"

urlpatterns = [
    path("buildings/", views.building_list, name="building_list"),
    path("buildings/new/", views.building_create, name="building_create"),
    path("buildings/<int:pk>/edit/", views.building_update, name="building_update"),
    path("rooms/", views.room_list, name="room_list"),
    path("rooms/new/", views.room_create, name="room_create"),
    path("rooms/<int:pk>/edit/", views.room_update, name="room_update"),
]
