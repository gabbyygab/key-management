from django.urls import path
from . import views

app_name = "reports"

urlpatterns = [
    path("", views.reports_index, name="index"),
    path("export/<str:report_type>/", views.report_export, name="export"),
    path("export/<str:report_type>/<str:export_format>/", views.report_export, name="export_format"),
]
