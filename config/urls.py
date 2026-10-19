from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from transactions import views as txn_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', txn_views.dashboard, name='dashboard'),
    path('accounts/', include('accounts.urls')),
    path('facilities/', include('facilities.urls')),
    path('keys/', include('keys.urls')),
    path('transactions/', include('transactions.urls')),
    path('api/transactions/', include('transactions.api_urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
