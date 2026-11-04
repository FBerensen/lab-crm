import hashlib
import logging

from django.conf import settings
from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.core.cache import cache
from django.db import connection
from django.dispatch import receiver
from django.http import HttpResponse, JsonResponse

logger = logging.getLogger("lab.security")


def get_client_ip(request) -> str:
    """Извлекает IP клиента с учётом доверенного прокси (X-Forwarded-For)."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _rate_limit_keys(ip: str, username: str = "") -> list[str]:
    ip_hash = hashlib.sha256(ip.encode("utf-8")).hexdigest()[:16]
    keys = [f"labcrm:login_fail:ip:{ip_hash}"]
    if username:
        user_hash = hashlib.sha256(
            username.strip().lower().encode("utf-8")
        ).hexdigest()[:16]
        keys.append(f"labcrm:login_fail:user_ip:{user_hash}:{ip_hash}")
    return keys


class HealthcheckMiddleware:
    """Обрабатывает /healthz/ до проверки ALLOWED_HOSTS и SECURE_SSL_REDIRECT.

    Гарантирует, что Docker healthcheck на 127.0.0.1 никогда не упадёт с 400/301,
    даже если в ALLOWED_HOSTS указан только боевой домен, а SECURE_SSL_REDIRECT включён.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path in ("/healthz", "/healthz/"):
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
            except Exception as exc:
                return JsonResponse(
                    {"status": "error", "detail": str(exc)}, status=503
                )
            return JsonResponse({"status": "ok"})
        return self.get_response(request)


class AdminLoginRateLimitMiddleware:
    """Защита /admin/login/ от перебора паролей (brute-force)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and request.path.rstrip("/") == "/admin/login":
            limit = getattr(settings, "LOGIN_FAILURE_LIMIT", 5)
            ip = get_client_ip(request)
            username = request.POST.get("username", "")
            for key in _rate_limit_keys(ip, username):
                attempts = cache.get(key, 0)
                if attempts >= limit:
                    logger.warning(
                        "Заблокирована попытка перебора пароля для IP=%s, username=%s",
                        ip,
                        username,
                    )
                    return HttpResponse(
                        "Слишком много неудачных попыток входа. Подождите 15 минут и попробуйте снова.",
                        status=429,
                        content_type="text/plain; charset=utf-8",
                    )
        return self.get_response(request)


@receiver(user_login_failed)
def on_user_login_failed(sender, credentials, request, **kwargs):
    if request is None:
        return
    timeout = getattr(settings, "LOGIN_COOLOFF_SECONDS", 15 * 60)
    ip = get_client_ip(request)
    username = (credentials or {}).get("username", "")
    for key in _rate_limit_keys(ip, username):
        current = cache.get(key, 0)
        cache.set(key, current + 1, timeout=timeout)


@receiver(user_logged_in)
def on_user_logged_in(sender, request, user, **kwargs):
    if request is None:
        return
    ip = get_client_ip(request)
    username = getattr(user, "username", "")
    for key in _rate_limit_keys(ip, username):
        cache.delete(key)
