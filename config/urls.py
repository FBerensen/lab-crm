from django.contrib import admin
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import path


def healthz_view(request):
    """Проверка работоспособности сервиса и подключения к БД (для Docker/Nginx)."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:
        return JsonResponse({"status": "error", "detail": str(exc)}, status=503)
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("", lambda request: redirect("admin:index"), name="root"),
    path("healthz/", healthz_view, name="healthz"),
    path("admin/", admin.site.urls),
]
