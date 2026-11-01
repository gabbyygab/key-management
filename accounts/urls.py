from django.urls import path
from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("post-login/", views.post_login_router, name="post_login_router"),
    # Borrower = people who can borrow (professors/staff/students)
    path("borrowers/", views.borrower_list, name="borrower_list"),
    path("borrowers/lookup/", views.borrower_lookup, name="borrower_lookup"),
    path("borrowers/new/", views.borrower_create, name="borrower_create"),
    path("borrowers/<int:pk>/edit/", views.borrower_update, name="borrower_update"),
    path("borrowers/<int:pk>/toggle/", views.borrower_toggle, name="borrower_toggle"),
    path("borrowers/<int:pk>/history/", views.borrower_history, name="borrower_history"),
    # Users & Guards
    path("users/", views.user_list, name="user_list"),
    path("users/new/", views.user_create, name="user_create"),
    path("users/<int:pk>/edit/", views.user_update, name="user_update"),
    path("users/<int:pk>/toggle/", views.user_toggle, name="user_toggle"),
    path("users/<int:pk>/reset-password/", views.user_reset_password, name="user_reset_password"),
    # User Approvals
    path("users/pending/", views.pending_user_list, name="pending_user_list"),
    path("users/<int:pk>/approve/", views.user_approve, name="user_approve"),
    path("users/<int:pk>/reject/", views.user_reject, name="user_reject"),
]
