from django.urls import path
from . import views

app_name = "scheduling"

urlpatterns = [
    path("scheduling/", views.schedule_list, name="schedule_list"),
    path("scheduling/timetable/", views.room_timetable, name="room_timetable"),
    path("scheduling/free-rooms/", views.free_rooms, name="free_rooms"),
    path("api/scheduling/free-rooms/", views.free_rooms_api, name="free_rooms_api"),
    path("api/scheduling/check-conflict/", views.conflict_check_api, name="conflict_check"),
    path("api/scheduling/blocks/", views.schedule_block_save_api, name="schedule_block_save"),
    path("api/scheduling/blocks/<int:pk>/delete/", views.schedule_block_delete_api, name="schedule_block_delete"),
    path("api/scheduling/rooms/<int:pk>/today/", views.room_today_schedule_api, name="room_today_schedule_api"),
]
