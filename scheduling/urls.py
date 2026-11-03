from django.urls import path
from . import views

app_name = "scheduling"

urlpatterns = [
    path("scheduling/", views.schedule_list, name="schedule_list"),
    path("scheduling/conflicts/", views.schedule_list, name="conflicts"),
    path("scheduling/blocks/<int:pk>/toggle/", views.schedule_toggle, name="schedule_toggle"),
    path("scheduling/blocks/<int:pk>/exception/", views.exception_create, name="exception_create"),
    path("scheduling/timetable/", views.room_timetable, name="room_timetable"),
    path("scheduling/free-rooms/", views.free_rooms, name="free_rooms"),

    # Academic terms (2025-2026 1st Semester)
    path("scheduling/terms/", views.term_list, name="term_list"),
    path("scheduling/terms/archived/", views.term_list, name="term_archived"),
    path("scheduling/terms/new/", views.term_create, name="term_create"),
    path("scheduling/terms/<int:pk>/edit/", views.term_update, name="term_update"),
    path("scheduling/terms/<int:pk>/toggle/", views.term_toggle, name="term_toggle"),
    path("scheduling/terms/<int:pk>/delete/", views.term_delete, name="term_delete"),
    path("scheduling/terms/bulk/", views.term_bulk, name="term_bulk"),

    path("scheduling/settings/", views.schedule_settings, name="settings"),

    # Subject catalogue
    path("scheduling/departments/", views.department_list, name="department_list"),
    path("scheduling/departments/archived/", views.department_list, name="department_archived"),
    path("scheduling/departments/new/", views.department_create, name="department_create"),
    path("scheduling/departments/<int:pk>/", views.department_detail, name="department_detail"),
    path("scheduling/departments/<int:pk>/edit/", views.department_update, name="department_update"),
    path("scheduling/departments/<int:pk>/toggle/", views.department_toggle, name="department_toggle"),
    path("scheduling/departments/<int:pk>/delete/", views.department_delete, name="department_delete"),
    path("scheduling/departments/bulk/", views.department_bulk, name="department_bulk"),
    path("scheduling/subjects/", views.subject_list, name="subject_list"),
    path("scheduling/subjects/archived/", views.subject_list, name="subject_archived"),
    path("scheduling/subjects/new/", views.subject_create, name="subject_create"),
    path("scheduling/subjects/<int:pk>/edit/", views.subject_update, name="subject_update"),
    path("scheduling/subjects/<int:pk>/toggle/", views.subject_toggle, name="subject_toggle"),
    path("scheduling/subjects/<int:pk>/delete/", views.subject_delete, name="subject_delete"),
    path("scheduling/subjects/bulk/", views.subject_bulk, name="subject_bulk"),

    # Curriculum: the subjects each program/year takes per semester
    path("scheduling/curriculum/", views.curriculum, name="curriculum"),
    path("scheduling/curriculum/add/", views.curriculum_add, name="curriculum_add"),
    path("scheduling/curriculum/<int:pk>/remove/", views.curriculum_remove,
         name="curriculum_remove"),

    # Sections (BSIT-4D and its groups BSIT-4DG1 / BSIT-4DG2)
    path("scheduling/sections/", views.section_list, name="section_list"),
    path("scheduling/sections/archived/", views.section_list, name="section_archived"),
    path("scheduling/sections/new/", views.section_create, name="section_create"),
    path("scheduling/sections/<int:pk>/edit/", views.section_update, name="section_update"),
    path("scheduling/sections/<int:pk>/toggle/", views.section_toggle, name="section_toggle"),
    path("scheduling/sections/<int:pk>/delete/", views.section_delete, name="section_delete"),
    path("scheduling/sections/bulk/", views.section_bulk, name="section_bulk"),

    # The first-come-first-served offering queue and the auto-scheduler
    path("scheduling/offerings/", views.offering_list, name="offering_list"),
    path("scheduling/offerings/archived/", views.offering_list, name="offering_archived"),
    path("scheduling/offerings/new/", views.offering_create, name="offering_create"),
    path("scheduling/offerings/<int:pk>/edit/", views.offering_update, name="offering_update"),
    path("scheduling/offerings/<int:pk>/unschedule/", views.offering_unschedule,
         name="offering_unschedule"),
    path("scheduling/offerings/<int:pk>/archive/", views.offering_archive,
         name="offering_archive"),
    path("scheduling/offerings/<int:pk>/delete/", views.offering_delete, name="offering_delete"),
    path("scheduling/offerings/bulk/", views.offering_bulk, name="offering_bulk"),
    path("scheduling/auto-schedule/", views.autoschedule, name="autoschedule"),

    # Section timetable, swapping and export
    path("scheduling/section-timetable/", views.section_timetable, name="section_timetable"),
    path("scheduling/section-timetable/export/", views.section_export, name="section_export"),
    path("scheduling/timetable/export/", views.room_export, name="room_export"),
    path("scheduling/blocks/swap/", views.block_swap, name="block_swap"),

    path("api/scheduling/free-rooms/", views.free_rooms_api, name="free_rooms_api"),
    path("api/scheduling/offerable-subjects/", views.offerable_subjects_api,
         name="offerable_subjects_api"),
    path("api/scheduling/check-conflict/", views.conflict_check_api, name="conflict_check"),
    path("api/scheduling/blocks/", views.schedule_block_save_api, name="schedule_block_save"),
    path("api/scheduling/blocks/<int:pk>/delete/", views.schedule_block_delete_api, name="schedule_block_delete"),
    path("api/scheduling/blocks/<int:pk>/move/", views.block_move_api, name="block_move"),
    path("api/scheduling/rooms/<int:pk>/today/", views.room_today_schedule_api, name="room_today_schedule_api"),
]
