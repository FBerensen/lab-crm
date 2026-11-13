from django.db import migrations, models
from django.db.models import F, Q
import django.db.models.deletion

TARGET_LOCATION = "61 комната, горизонтальный холодильник (-40С)"


def link_existing_locations(apps, schema_editor):
    alias = schema_editor.connection.alias
    Place = apps.get_model("lab", "StoragePlace")
    Sample = apps.get_model("lab", "Sample")
    Place.objects.using(alias).get_or_create(name=TARGET_LOCATION, defaults={"active": True, "sort_order": 100})
    Sample.objects.using(alias).all().update(original_location=F("location"))
    for place in Place.objects.using(alias).all().iterator():
        Sample.objects.using(alias).filter(storage_place__isnull=True, location=place.name).update(storage_place_id=place.pk)


class Migration(migrations.Migration):
    dependencies = [("lab", "0009_preparation_self_approval_autoclaving_mark")]
    operations = [
        migrations.AddField(model_name="storageplace", name="parent", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="children", to="lab.storageplace", verbose_name="Внутри места хранения")),
        migrations.AddConstraint(model_name="storageplace", constraint=models.CheckConstraint(condition=Q(parent__isnull=True) | ~Q(parent=F("pk")), name="storage_place_not_self_parent")),
        migrations.AddField(model_name="sample", name="storage_place", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="stored_samples", to="lab.storageplace", verbose_name="Место хранения из справочника")),
        migrations.AddField(model_name="sample", name="original_location", field=models.CharField(blank=True, default="", editable=False, max_length=240, verbose_name="Место хранения до привязки к справочнику")),
        migrations.AlterField(model_name="sample", name="location", field=models.CharField(blank=True, max_length=240, verbose_name="Место хранения (текст)")),
    ]
