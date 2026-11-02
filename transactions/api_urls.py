from django.urls import path

from . import api

app_name = "transactions_api"

urlpatterns = [
    path("active/", api.active_list, name="active"),
    path("borrow/", api.transaction_borrow, name="borrow"),
    path("<int:pk>/return/", api.transaction_return, name="return"),
]
