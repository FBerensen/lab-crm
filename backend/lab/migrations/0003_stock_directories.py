from django.db import migrations

CATEGORIES = ("Пластик", "Нанофор 05", "реактив для ПЦР", "реактивы")
PLACES = (
    "15к. холодильник серый +4",
    "15к. холодильник адмирал +4",
    "15к. морозильник ПЦР -20",
    "15к. морозилка адмирал -20",
    "15к. морозилка горизонтальная -20",
    "15к. морозилка Bosch -20",
    "15к. холодильник Bosch +4",
    "15к. холодильник POZIS +4",
)


def fill_directories(apps, schema_editor):
    db = schema_editor.connection.alias
    Category = apps.get_model("lab", "ProductCategory")
    Place = apps.get_model("lab", "StoragePlace")
    Lot = apps.get_model("lab", "Lot")
    for order, name in enumerate(CATEGORIES, 1):
        Category.objects.using(db).get_or_create(name=name, defaults={"sort_order": order})
    for order, name in enumerate(PLACES, 1):
        Place.objects.using(db).get_or_create(name=name, defaults={"sort_order": order})
    # Preserve original, potentially non-standard locations as directory entries.
    locations = Lot.objects.using(db).values_list("location", flat=True).distinct()
    for name in locations.iterator():
        if name and name.strip():
            place, _ = Place.objects.using(db).get_or_create(name=name)
            Lot.objects.using(db).filter(location=name).update(storage_place_id=place.pk)
    # Do not guess categories or historical initial quantities.
    # Existing stock, reservations, codes and movements are untouched.


class Migration(migrations.Migration):
    dependencies = [("lab", "0002_stock_products")]
    operations = [migrations.RunPython(fill_directories, migrations.RunPython.noop)]
