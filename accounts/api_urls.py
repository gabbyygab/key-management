from django.urls import path

from . import api

app_name = "accounts_api"

urlpatterns = [
    path("search/", api.borrower_search, name="borrower_search"),
]
