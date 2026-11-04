from django.db import migrations

REQUIRED_CATEGORIES = (
    ("Пластик", 1),
    ("Реактив", 2),
    ("Буфер", 3),
    ("Нанофор 05", 4),
    ("реактив для ПЦР", 5),
    ("реактивы", 6),
)

DEFAULT_BUFFERS = (
    ("PBS 1x", 10),
    ("TAE 1x", 20),
    ("TBE 1x", 30),
    ("TE буфер (pH 8.0)", 40),
    ("Лизирующий буфер CTAB", 50),
)


def ensure_default_directories(apps, schema_editor):
    db = schema_editor.connection.alias
    ProductCategory = apps.get_model("lab", "ProductCategory")
    BufferName = apps.get_model("lab", "BufferName")

    for name, sort_order in REQUIRED_CATEGORIES:
        category, created = ProductCategory.objects.using(db).get_or_create(
            name=name,
            defaults={"active": True, "sort_order": sort_order},
        )
        if not created and not category.active and name in ("Пластик", "Реактив", "Буфер"):
            category.active = True
            category.save(update_fields=["active"])

    for name, sort_order in DEFAULT_BUFFERS:
        BufferName.objects.using(db).get_or_create(
            name=name,
            defaults={"active": True, "sort_order": sort_order},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("lab", "0005_task_samples"),
    ]

    operations = [
        migrations.RunPython(
            ensure_default_directories,
            migrations.RunPython.noop,
        ),
    ]
