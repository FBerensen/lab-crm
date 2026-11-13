import os
from django.contrib import admin
from django.shortcuts import redirect
from django.urls import include, path
from api import views as api_views

ADMIN_URL = os.getenv("DJANGO_ADMIN_URL", "admin/").lstrip("/")
if not ADMIN_URL.endswith("/"):
    ADMIN_URL += "/"

urlpatterns = [
    path("healthz/", api_views.healthz_view, name="healthz"),
    path("api/", include("api.urls")),
    path("", lambda request: redirect(f"/{ADMIN_URL}")),
    path(ADMIN_URL, admin.site.urls),
]
