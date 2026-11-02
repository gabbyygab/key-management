from django.urls import path
from . import views

app_name = "facilities"

urlpatterns = [
    path("buildings/", views.building_list, name="building_list"),
    path("buildings/new/", views.building_create, name="building_create"),
    path("buildings/<int:pk>/edit/", views.building_update, name="building_update"),
    path("buildings/<int:pk>/toggle/", views.building_toggle, name="building_toggle"),
    path("rooms/", views.room_list, name="room_list"),
    path("rooms/new/", views.room_create, name="room_create"),
    path("rooms/bulk-new/", views.room_bulk_create, name="room_bulk_create"),
    path("rooms/<int:pk>/", views.room_detail, name="room_detail"),
    path("rooms/<int:pk>/edit/", views.room_update, name="room_update"),
    path("rooms/<int:pk>/toggle/", views.room_toggle, name="room_toggle"),
    path("posts/", views.post_list, name="post_list"),
    path("posts/new/", views.post_create, name="post_create"),
    path("posts/<int:pk>/edit/", views.post_update, name="post_update"),
    path("posts/<int:pk>/toggle/", views.post_toggle, name="post_toggle"),
]
