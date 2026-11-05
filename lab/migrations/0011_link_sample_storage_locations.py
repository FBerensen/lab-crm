from django.db import migrations
from django.db.models import F

TARGET_LOCATION = "61 комната, горизонтальный холодильник (-40С)"


def link_existing_locations(apps, schema_editor):
    alias = schema_editor.connection.alias
    Place = apps.get_model("lab", "StoragePlace")
    Sample = apps.get_model("lab", "Sample")
    Place.objects.using(alias).get_or_create(name=TARGET_LOCATION, defaults={"active": True, "sort_order": 100})
    Sample.objects.using(alias).filter(original_location="").update(original_location=F("location"))
    for place in Place.objects.using(alias).all().iterator():
        Sample.objects.using(alias).filter(storage_place__isnull=True, location=place.name).update(storage_place_id=place.pk)


class Migration(migrations.Migration):
    dependencies = [("lab", "0010_storage_hierarchy_sample_locations")]
    operations = [migrations.RunPython(link_existing_locations)]
