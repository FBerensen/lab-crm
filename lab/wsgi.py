"""Обратная совместимость для запуска WSGI через lab.wsgi:application."""
from config.wsgi import application  # noqa: F401
