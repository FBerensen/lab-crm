from django.db import migrations, models


def copy_existing_samples(apps, schema_editor):
    Task = apps.get_model("lab", "Task")
    database = schema_editor.connection.alias

    for task in (
        Task.objects.using(database)
        .exclude(sample_id=None)
        .iterator()
    ):
        task.samples.add(task.sample_id)


class Migration(migrations.Migration):

    dependencies = [
        ("lab", "0004_sample_series_number"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="samples",
            field=models.ManyToManyField(
                blank=True,
                related_name="tasks",
                to="lab.sample",
                verbose_name="Образцы / пробы",
            ),
        ),
        migrations.RunPython(copy_existing_samples),
    ]