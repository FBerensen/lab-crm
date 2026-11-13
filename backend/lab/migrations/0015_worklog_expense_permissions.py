from django.db import migrations

def grant(apps, schema_editor):
    alias = schema_editor.connection.alias
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    content_type, _ = ContentType.objects.using(alias).get_or_create(app_label="lab", model="workexpenserequest")
    permission, _ = Permission.objects.using(alias).get_or_create(content_type=content_type,
        codename="post_worklog_expense", defaults={"name": "Проводить расход по журналу работ"})
    for name in ("Руководители", "Склад"):
        group, _ = Group.objects.using(alias).get_or_create(name=name)
        group.permissions.add(permission)

class Migration(migrations.Migration):
    dependencies = [("lab", "0014_worklog_expenses")]
    operations = [migrations.RunPython(grant, reverse_code=migrations.RunPython.noop)]
