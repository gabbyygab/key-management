from django.urls import path
from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    # Borrower = people who can borrow (professors/staff/students)
    path("borrowers/", views.borrower_list, name="borrower_list"),
    path("borrowers/new/", views.borrower_create, name="borrower_create"),
    path("borrowers/<int:pk>/edit/", views.borrower_update, name="borrower_update"),
    path("borrowers/<int:pk>/toggle/", views.borrower_toggle, name="borrower_toggle"),
]
