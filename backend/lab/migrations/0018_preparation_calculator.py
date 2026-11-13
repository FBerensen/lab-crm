from django.db import migrations

class Migration(migrations.Migration):
    dependencies = [("lab", "0017_laboratory_protocols_and_plans")]
    operations = [migrations.CreateModel(
        name="PreparationCalculator", fields=[], bases=("lab.preparationprotocol",),
        options={"proxy": True, "verbose_name": "Калькулятор приготовления", "verbose_name_plural": "Калькулятор приготовления",
            "ordering": ("product__name", "title", "version"), "default_permissions": ("view",), "indexes": [], "constraints": []},
    )]
