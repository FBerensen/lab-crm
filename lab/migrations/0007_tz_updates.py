from decimal import Decimal
import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


def link_sample_storage_places(apps, schema_editor):
    db = schema_editor.connection.alias
    Sample = apps.get_model("lab", "Sample")
    StoragePlace = apps.get_model("lab", "StoragePlace")

    places_by_name = {
        p.name: p.pk for p in StoragePlace.objects.using(db).all()
    }
    for sample in Sample.objects.using(db).filter(storage_place__isnull=True).iterator():
        loc = (sample.location or "").strip()
        if loc and loc in places_by_name:
            Sample.objects.using(db).filter(pk=sample.pk).update(
                storage_place_id=places_by_name[loc]
            )


class Migration(migrations.Migration):

    dependencies = [
        ("lab", "0006_ensure_default_categories"),
    ]

    operations = [
        migrations.AddField(
            model_name="sample",
            name="storage_place",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="samples",
                to="lab.storageplace",
                verbose_name="Место хранения (из справочника)",
            ),
        ),
        migrations.AlterField(
            model_name="sample",
            name="location",
            field=models.CharField(
                blank=True,
                help_text="Заполняется автоматически при выборе места из справочника либо вручную.",
                max_length=240,
                verbose_name="Место хранения",
            ),
        ),
        migrations.AddField(
            model_name="movement",
            name="worklog",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="movements",
                to="lab.worklog",
                verbose_name="Запись журнала работ",
            ),
        ),
        migrations.AlterField(
            model_name="movement",
            name="task",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="movements",
                to="lab.task",
                verbose_name="Задача",
            ),
        ),
        migrations.AlterField(
            model_name="movement",
            name="preparation",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="movements",
                to="lab.preparation",
                verbose_name="Общелабораторная работа",
            ),
        ),
        migrations.AlterModelOptions(
            name="preparation",
            options={
                "verbose_name": "Общелабораторная работа",
                "verbose_name_plural": "Приготовление и автоклавирование",
            },
        ),
        migrations.AlterField(
            model_name="preparation",
            name="kind",
            field=models.CharField(
                choices=[
                    ("Приготовление реактива", "Приготовление реактива"),
                    ("Приготовление буфера", "Приготовление буфера"),
                    ("Автоклавирование расходников", "Автоклавирование расходников"),
                ],
                max_length=40,
                verbose_name="Тип задачи",
            ),
        ),
        migrations.AlterField(
            model_name="preparation",
            name="output",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="production_record",
                to="lab.lot",
                verbose_name="Добавить реактив",
            ),
        ),
        migrations.AlterField(
            model_name="preparation",
            name="qty",
            field=models.DecimalField(
                decimal_places=4,
                help_text="Допустимый диапазон: от 0,1 до 100000.",
                max_digits=16,
                validators=[
                    django.core.validators.MinValueValidator(Decimal("0.1")),
                    django.core.validators.MaxValueValidator(Decimal("100000")),
                ],
                verbose_name="Количество",
            ),
        ),
        migrations.AddField(
            model_name="preparation",
            name="unit",
            field=models.CharField(
                choices=[
                    ("л", "л"),
                    ("мл", "мл"),
                    ("мкл", "мкл"),
                    ("кг", "кг"),
                    ("г", "г"),
                    ("мкг", "мкг"),
                    ("шт", "шт"),
                    ("упак", "упак"),
                ],
                default="мл",
                max_length=20,
                verbose_name="Единица измерения",
            ),
        ),
        migrations.AddField(
            model_name="component",
            name="unit",
            field=models.CharField(
                blank=True,
                choices=[
                    ("л", "л"),
                    ("мл", "мл"),
                    ("мкл", "мкл"),
                    ("кг", "кг"),
                    ("г", "г"),
                    ("мкг", "мкг"),
                    ("шт", "шт"),
                    ("упак", "упак"),
                ],
                default="",
                max_length=20,
                verbose_name="Единица измерения",
            ),
        ),
        migrations.AlterField(
            model_name="task",
            name="stage",
            field=models.CharField(
                choices=[
                    ("Получение материала", "Получение материала"),
                    ("Фиксация материала", "Фиксация материала"),
                    ("Выделение ДНК/РНК", "Выделение ДНК/РНК"),
                    ("Создание блоков", "Создание блоков"),
                    ("Постановка ПЦР", "Постановка ПЦР"),
                    ("Real-Time ПЦР", "Real-Time ПЦР"),
                    ("Фрагментный анализ (ФА)", "Фрагментный анализ (ФА)"),
                    ("Секвенирование", "Секвенирование"),
                    ("Маркерный анализ", "Маркерный анализ"),
                    ("Анализ результатов", "Анализ результатов"),
                    ("Проверка результатов", "Проверка результатов"),
                ],
                default="Получение материала",
                max_length=80,
                verbose_name="Этап",
            ),
        ),
        migrations.AlterField(
            model_name="taskevent",
            name="stage",
            field=models.CharField(
                blank=True,
                choices=[
                    ("Получение материала", "Получение материала"),
                    ("Фиксация материала", "Фиксация материала"),
                    ("Выделение ДНК/РНК", "Выделение ДНК/РНК"),
                    ("Создание блоков", "Создание блоков"),
                    ("Постановка ПЦР", "Постановка ПЦР"),
                    ("Real-Time ПЦР", "Real-Time ПЦР"),
                    ("Фрагментный анализ (ФА)", "Фрагментный анализ (ФА)"),
                    ("Секвенирование", "Секвенирование"),
                    ("Маркерный анализ", "Маркерный анализ"),
                    ("Анализ результатов", "Анализ результатов"),
                    ("Проверка результатов", "Проверка результатов"),
                ],
                max_length=80,
                verbose_name="Новый этап",
            ),
        ),
        migrations.RunPython(
            link_sample_storage_places,
            migrations.RunPython.noop,
        ),
    ]
