from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def create_trainee_group(apps, schema_editor):
    apps.get_model("auth", "Group").objects.using(schema_editor.connection.alias).get_or_create(name="Стажёры")


class Migration(migrations.Migration):
    dependencies = [
        ("lab", "0008_alter_preparation_options"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]
    operations = [
        migrations.AlterModelOptions(name="preparation", options={
            "verbose_name": "Приготовление раствора или буфера",
            "verbose_name_plural": "Приготовление растворов и буферов",
            "permissions": [("approve_preparation", "Может допускать приготовленные растворы и буферы")],
        }),
        migrations.AddField(model_name="preparation", name="autoclaving_done", field=models.BooleanField(default=False, editable=False, verbose_name="Автоклавирование всей партии выполнено")),
        migrations.AddField(model_name="preparation", name="autoclaved_by", field=models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to=settings.AUTH_USER_MODEL, verbose_name="Кто отметил автоклавирование")),
        migrations.AddField(model_name="preparation", name="autoclaved_at", field=models.DateTimeField(blank=True, editable=False, null=True, verbose_name="Когда отмечено автоклавирование")),
        migrations.AddConstraint(model_name="preparation", constraint=models.CheckConstraint(
                condition=(models.Q(autoclaving_done=False, autoclaved_by__isnull=True, autoclaved_at__isnull=True)
                    | models.Q(autoclaving_done=True, autoclaved_by__isnull=False, autoclaved_at__isnull=False)),
                name="prep_autoclaving_mark_complete",
            )),
        migrations.RunPython(create_trainee_group, migrations.RunPython.noop),
    ]
