from django.urls import path
from . import views

app_name = "keys"

urlpatterns = [
    path("", views.key_list, name="key_list"),
    path("new/", views.key_create, name="key_create"),
    path("<int:pk>/edit/", views.key_update, name="key_update"),
    path("categories/", views.category_list, name="category_list"),
    path("categories/new/", views.category_create, name="category_create"),
    path("categories/<int:pk>/edit/", views.category_update, name="category_update"),
]
