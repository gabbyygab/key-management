from django.urls import path
from . import views

app_name = "transactions"

urlpatterns = [
    path("borrow/", views.borrow_dashboard, name="borrow_dashboard"),
    path("borrow/confirm/", views.borrow_confirm, name="borrow_confirm"),
    path("return/<int:pk>/", views.return_confirm, name="return_confirm"),
    path("shifts/", views.shift_history, name="shift_history"),
    path("history/", views.transaction_history, name="history"),
]
