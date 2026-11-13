from django.db import migrations

class Migration(migrations.Migration):
    dependencies = [("lab", "0015_worklog_expense_permissions")]
    operations = [migrations.CreateModel(
        name="StockAvailability", fields=[], bases=("lab.lot",),
        options={"proxy": True, "verbose_name": "Наличие и ожидающий расход",
            "verbose_name_plural": "Наличие и ожидающий расход", "ordering": ("name", "pk"),
            "default_permissions": ("view",), "indexes": [], "constraints": []},
    )]
