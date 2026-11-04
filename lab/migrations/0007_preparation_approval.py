from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("lab", "0006_alter_buffername_options_buffername_concentration_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="preparation",
            name="autoclaving_required",
            field=models.BooleanField(
                "Требовалось автоклавирование при приготовлении",
                null=True,
                default=None,
                editable=False,
                help_text="Фиксируется при проведении. Пусто — историческая запись без снимка требования.",
            ),
        ),
        migrations.AddField(
            model_name="preparation",
            name="approved_cycle",
            field=models.ForeignKey(
                "lab.autoclaving",
                on_delete=django.db.models.deletion.PROTECT,
                verbose_name="Цикл, учтённый при допуске",
                related_name="approved_preparations",
                null=True,
                blank=True,
                editable=False,
            ),
        ),
    ]
