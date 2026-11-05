import tempfile
from django.conf import settings
from django.core.management import call_command
from django.db import connection


def run():
    if connection.settings_dict["NAME"] != "labcrm_preparation_check":
        raise RuntimeError("Run only with DB_NAME=labcrm_preparation_check")
    name = "test_labcrm_preparation_check"
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s", [name])
        owner = cursor.fetchone()
    if owner != (connection.settings_dict["USER"],):
        raise RuntimeError("Precreated test database missing or wrong owner")
    connection.close()
    settings.DATABASES["default"]["TEST"]["NAME"] = name
    connection.settings_dict["TEST"]["NAME"] = name
    with tempfile.TemporaryDirectory(prefix="labcrm-stage5a-static-") as root:
        settings.STATIC_ROOT = root
        call_command("collectstatic", interactive=False, verbosity=0)
        call_command("test", "lab.test_ui_stage1", "lab.test_preparation_stage2", "lab.test_preparation_wizard", "lab.test_storage_stage4", "lab.test_storage_upgrade", "lab.test_sample_series_stage5", keepdb=True, interactive=False, verbosity=2)
