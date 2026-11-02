from django.urls import path

from . import api

app_name = "keys_api"

urlpatterns = [
    path("search/", api.key_search, name="search"),
    path("<int:pk>/", api.key_detail, name="detail"),
]
