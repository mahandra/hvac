from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path


def superuser_only(request):
    """Django admin is available to superusers only."""
    return request.user.is_active and request.user.is_superuser


admin.site.has_permission = superuser_only
admin.site.site_header = "HVAC Tracker admin"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(redirect_authenticated_user=True), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("tracker.urls")),
]
