from decimal import Decimal
import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("lab", "0023_source_block_storage"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ElectrophoresisGel",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Когда внесено")),
                ("actual_day", models.DateField(default=django.utils.timezone.localdate, verbose_name="Дата выполнения")),
                ("agarose_percent", models.DecimalField(decimal_places=2, default=Decimal("1.50"), max_digits=5, validators=[django.core.validators.MinValueValidator(Decimal("0.10"))], verbose_name="Концентрация агарозы, %")),
                ("voltage_v", models.PositiveIntegerField(default=120, verbose_name="Напряжение, В")),
                ("duration_min", models.PositiveIntegerField(default=30, verbose_name="Время фореза, мин")),
                ("marker_name", models.CharField(blank=True, max_length=120, verbose_name="Маркер молекулярного веса (Ladder)")),
                ("status", models.CharField(choices=[("draft", "Черновик"), ("completed", "Проведён")], default="draft", max_length=20, verbose_name="Статус")),
                ("result_summary", models.TextField(blank=True, verbose_name="Заключение по гелю")),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL, verbose_name="Кто внёс запись")),
                ("buffer_lot", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="electrophoresis_gels", to="lab.lot", verbose_name="Партия буфера (TAE/TBE)")),
                ("laboratory_run", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="electrophoresis_gels", to="lab.laboratoryprotocolrun", verbose_name="Выполнение методики (выделение ДНК / ПЦР)")),
                ("task", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="electrophoresis_gels", to="lab.task", verbose_name="Задача")),
            ],
            options={
                "verbose_name": "Гель-электрофорез (Форез)",
                "verbose_name_plural": "Форезы (гель-электрофорез)",
                "ordering": ["-actual_day", "-created_at"],
            },
        ),
        migrations.CreateModel(
            name="ElectrophoresisLane",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("lane_number", models.PositiveSmallIntegerField(verbose_name="Дорожка №")),
                ("is_control", models.BooleanField(default=False, verbose_name="Контроль / Маркер")),
                ("sample_volume_ul", models.DecimalField(decimal_places=2, default=Decimal("5.00"), max_digits=6, validators=[django.core.validators.MinValueValidator(Decimal("0.10"))], verbose_name="Объём пробы, мкл")),
                ("band_size_bp", models.CharField(blank=True, max_length=80, verbose_name="Размер фрагмента, п.н.")),
                ("quality", models.CharField(choices=[("pass", "Зачёт (чёткая полоса)"), ("weak", "Слабая полоса"), ("smear", "Шмер / деградация ДНК"), ("empty", "Отсутствие сигнала"), ("control_ok", "Контроль чистый")], default="pass", max_length=20, verbose_name="Оценка дорожки")),
                ("note", models.CharField(blank=True, max_length=240, verbose_name="Примечание")),
                ("gel", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lanes", to="lab.electrophoresisgel", verbose_name="Гель")),
                ("sample", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="lab.sample", verbose_name="Проба")),
            ],
            options={
                "verbose_name": "Дорожка фореза",
                "verbose_name_plural": "Дорожки фореза",
                "ordering": ["lane_number"],
                "constraints": [
                    models.UniqueConstraint(fields=("gel", "lane_number"), name="unique_gel_lane_number")
                ],
            },
        ),
    ]
