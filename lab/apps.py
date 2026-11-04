from django.apps import AppConfig


class LabConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "lab"
    verbose_name = "Лабораторная CRM"

    def ready(self):
        from . import security  # noqa: F401
