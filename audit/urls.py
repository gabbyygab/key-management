from django.urls import path
from . import views

app_name = "audit"

urlpatterns = [
    path("logs/", views.log_list, name="log_list"),
]
