from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [("lab", "0012_sample_collection_fields")]
    operations = [
        migrations.CreateModel(
            name="MaterialPassport",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("vir_catalogue_number", models.CharField("Кат. № ВИР", max_length=100, blank=True, default="", db_index=True)),
                ("species", models.CharField("Вид", max_length=240, blank=True, default="")),
                ("origin", models.TextField("Происхождение", blank=True, default="")),
            ],
            options={"verbose_name": "Паспорт материала", "verbose_name_plural": "Паспорта материалов", "ordering": ("vir_catalogue_number", "pk")},
        ),
        migrations.AddField(
            model_name="sample", name="material_passport",
            field=models.ForeignKey(
                "lab.MaterialPassport", on_delete=django.db.models.deletion.PROTECT,
                verbose_name="Паспорт материала", related_name="samples", null=True, blank=True,
            ),
        ),
    ]
