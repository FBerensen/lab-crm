from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("lab", "0011_link_sample_storage_locations")]
    operations = [
        migrations.AddField("sample", "sample_type", models.CharField(
            "Тип пробы", max_length=16, blank=True, default="",
            choices=[("genotype", "генотип"), ("bulk", "bulk-проба")],
        )),
        migrations.AddField("sample", "plant_part", models.CharField(
            "Часть растения", max_length=240, blank=True, default="",
        )),
        migrations.AddField("sample", "sampling_location", models.CharField(
            "Место отбора пробы", max_length=500, blank=True, default="",
        )),
        migrations.AddField("sample", "sampling_date", models.DateField(
            "Дата отбора", null=True, blank=True,
        )),
        migrations.AddField("sample", "received_in_lab_date", models.DateField(
            "Дата получения в лабораторию", null=True, blank=True,
        )),
        migrations.AddConstraint("sample", models.CheckConstraint(
            condition=models.Q(sample_type__in=["", "genotype", "bulk"]),
            name="sample_type_allowed_5b1",
        )),
        migrations.AddConstraint("sample", models.CheckConstraint(
            condition=(models.Q(sampling_date__isnull=True)
                       | models.Q(received_in_lab_date__isnull=True)
                       | models.Q(received_in_lab_date__gte=models.F("sampling_date"))),
            name="sample_received_not_before_sampling_5b1",
        )),
    ]
