from django.urls import path
from . import views

app_name = "scheduling"

urlpatterns = [
    path("scheduling/", views.schedule_list, name="schedule_list"),
    path("scheduling/free-rooms/", views.free_rooms, name="free_rooms"),
    path("api/scheduling/free-rooms/", views.free_rooms_api, name="free_rooms_api"),
]
