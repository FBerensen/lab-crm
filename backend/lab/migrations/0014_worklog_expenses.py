from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [("lab", "0013_material_passport"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField("worklog", "samples", models.ManyToManyField(
            "lab.Sample", verbose_name="Обработанные пробы", blank=True, related_name="worklogs")),
        migrations.CreateModel(name="WorkExpenseRequest", fields=[
            ("id", models.BigAutoField(primary_key=True, serialize=False)),
            ("status", models.CharField("Состояние", max_length=12, choices=[("pending", "Ожидает подтверждения"), ("posted", "Проведён")], default="pending")),
            ("posted_at", models.DateTimeField("Когда проведён", null=True, blank=True)),
            ("created_at", models.DateTimeField("Когда создан", auto_now_add=True)),
            ("posted_by", models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.PROTECT, related_name="posted_work_expenses", verbose_name="Провёл расход", null=True, blank=True)),
            ("worklog", models.OneToOneField("lab.WorkLog", on_delete=django.db.models.deletion.PROTECT, related_name="expense_request", verbose_name="Запись журнала")),
        ], options={"verbose_name": "Расход по журналу", "verbose_name_plural": "Расходы по журналу", "permissions": [("post_worklog_expense", "Проводить расход по журналу работ")],
            "constraints": [models.CheckConstraint(condition=(models.Q(status="pending", posted_by__isnull=True, posted_at__isnull=True) | models.Q(status="posted", posted_by__isnull=False, posted_at__isnull=False)), name="work_expense_posting_state_6a")]}),
        migrations.CreateModel(name="WorkExpenseLine", fields=[
            ("id", models.BigAutoField(primary_key=True, serialize=False)),
            ("qty", models.DecimalField("Количество", max_digits=16, decimal_places=4, validators=[MinValueValidator(Decimal("0.0001"))])),
            ("mode", models.CharField("Источник списания", max_length=16, choices=[("use_free", "Свободный остаток"), ("use_reserved", "Резерв задачи")], default="use_free")),
            ("lot", models.ForeignKey("lab.Lot", on_delete=django.db.models.deletion.PROTECT, related_name="work_expense_lines", verbose_name="Продукт")),
            ("worklog", models.ForeignKey("lab.WorkLog", on_delete=django.db.models.deletion.PROTECT, related_name="expense_lines", verbose_name="Запись журнала")),
        ], options={"verbose_name": "Строка расхода", "verbose_name_plural": "Строки расхода", "constraints": [
            models.UniqueConstraint(fields=["worklog", "lot", "mode"], name="work_expense_line_unique_6a"),
            models.CheckConstraint(condition=models.Q(qty__gt=0), name="work_expense_line_positive_6a"),
            models.CheckConstraint(condition=models.Q(mode__in=["use_free", "use_reserved"]), name="work_expense_line_mode_6a")]}),
        migrations.AddField("movement", "worklog", models.ForeignKey("lab.WorkLog", on_delete=django.db.models.deletion.PROTECT, related_name="stock_movements", verbose_name="Запись журнала", null=True, blank=True)),
        migrations.AddField("movement", "expense_line", models.OneToOneField("lab.WorkExpenseLine", on_delete=django.db.models.deletion.PROTECT, related_name="stock_movement", verbose_name="Строка расхода журнала", null=True, blank=True)),
    ]
