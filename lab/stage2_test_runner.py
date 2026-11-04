from pathlib import Path
import tempfile
from django.conf import settings
from django.core.management import call_command
from django.db import connection


def run():
    if connection.settings_dict["NAME"] != "labcrm_preparation_check":
        raise RuntimeError("Run only with DB_NAME=labcrm_preparation_check")
    test_name = "test_labcrm_preparation_check"
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = %s", [test_name])
        row = cursor.fetchone()
    if row != (connection.settings_dict["USER"],):
        raise RuntimeError("Precreated test database is missing or has a different owner")
    connection.close()
    settings.DATABASES["default"]["TEST"]["NAME"] = test_name
    connection.settings_dict["TEST"]["NAME"] = test_name
    with tempfile.TemporaryDirectory(prefix="labcrm-stage2-static-") as static_root:
        settings.STATIC_ROOT = static_root
        call_command("collectstatic", interactive=False, verbosity=0)
        call_command("test", "lab.test_ui_stage1", "lab.test_preparation_stage2", keepdb=True, interactive=False, verbosity=2)
