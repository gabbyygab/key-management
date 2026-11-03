from django.urls import path
from . import views

app_name = "facilities"

urlpatterns = [
    path("buildings/", views.building_list, name="building_list"),
    path("buildings/archived/", views.building_archived, name="building_archived"),
    path("buildings/new/", views.building_create, name="building_create"),
    path("buildings/<int:pk>/edit/", views.building_update, name="building_update"),
    path("buildings/<int:pk>/toggle/", views.building_toggle, name="building_toggle"),
    # Delete forever: archived rows only, admin only, POST only (retention).
    path("buildings/<int:pk>/delete/", views.building_delete, name="building_delete"),
    path("buildings/bulk/", views.building_bulk, name="building_bulk"),
    path("rooms/", views.room_list, name="room_list"),
    path("rooms/archived/", views.room_archived, name="room_archived"),
    path("rooms/new/", views.room_create, name="room_create"),
    path("rooms/bulk-new/", views.room_bulk_create, name="room_bulk_create"),
    path("rooms/<int:pk>/", views.room_detail, name="room_detail"),
    path("rooms/<int:pk>/edit/", views.room_update, name="room_update"),
    path("rooms/<int:pk>/toggle/", views.room_toggle, name="room_toggle"),
    path("rooms/<int:pk>/delete/", views.room_delete, name="room_delete"),
    path("rooms/<int:pk>/generate-key/", views.room_generate_key, name="room_generate_key"),
    path("rooms/bulk/", views.room_bulk, name="room_bulk"),
    path("posts/", views.post_list, name="post_list"),
    path("posts/archived/", views.post_archived, name="post_archived"),
    path("posts/bulk/", views.post_bulk, name="post_bulk"),
    path("posts/new/", views.post_create, name="post_create"),
    path("posts/<int:pk>/edit/", views.post_update, name="post_update"),
    path("posts/<int:pk>/toggle/", views.post_toggle, name="post_toggle"),
    path("posts/<int:pk>/delete/", views.post_delete, name="post_delete"),
]
