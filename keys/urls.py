from django.urls import path
from . import views

app_name = "keys"

urlpatterns = [
    path("", views.key_list, name="key_list"),
    path("archived/", views.key_archived, name="key_archived"),
    path("availability/", views.key_availability, name="key_availability"),
    path("new/", views.key_create, name="key_create"),
    path("<int:pk>/edit/", views.key_update, name="key_update"),
    path("<int:pk>/archive/", views.key_archive, name="key_archive"),
    path("bulk-create/", views.key_bulk_create, name="key_bulk_create"),
    # Delete forever: archived rows only, admin only, POST only (retention).
    path("<int:pk>/delete/", views.key_delete, name="key_delete"),
    path("bulk/", views.key_bulk, name="key_bulk"),
    path("categories/", views.category_list, name="category_list"),
    path("categories/archived/", views.category_archived, name="category_archived"),
    path("categories/new/", views.category_create, name="category_create"),
    path("categories/<int:pk>/edit/", views.category_update, name="category_update"),
    path("categories/<int:pk>/toggle/", views.category_toggle, name="category_toggle"),
    path("categories/<int:pk>/delete/", views.category_delete, name="category_delete"),
    path("categories/bulk/", views.category_bulk, name="category_bulk"),
]
