from types import MethodType
from lab.presentation import compact_number

GROUPS = (
    ('lab_work', 'Проекты и работа', ('Project', 'Task', 'WorkLog', 'TaskEvent', 'Report')),
    ('lab_samples', 'Образцы и пробы', ('Sample', 'MaterialPassport', 'SourceBlock')),
    ('lab_dna', 'Выделение ДНК и методики', ('LaboratoryProtocol', 'LaboratoryProtocolRun', 'TaskMaterialPlan')),
    ('lab_electrophoresis', 'Форезы (электрофорез)', ('ElectrophoresisGel',)),
    ('lab_stock', 'Склад и хранение', ('Lot', 'Movement', 'StoragePlace', 'StockAvailability')),
    ('lab_preparation', 'Приготовление растворов и буферов', (
        'BufferName', 'PreparationProtocol', 'PreparationCalculator',
        'Preparation', 'PreparationDraftSelection', 'Autoclaving',
    )),
    ('lab_reference', 'Справочники и управление', ('ProductCategory', 'Audit')),
)


def group_app_list(apps):
    result = []
    for app in apps:
        if app["app_label"] != "lab":
            result.append(app)
            continue
        remaining = {model["object_name"]: model for model in app["models"]}
        for label, title, names in GROUPS:
            models = [remaining.pop(name) for name in names if name in remaining]
            if models:
                result.append({**app, "app_label": label, "name": title, "models": models})
        if remaining:
            result.append({**app, "name": "Другие разделы лаборатории", "models": list(remaining.values())})
    return result


def configure_admin(site):
    if getattr(site, "_lab_ui_stage1", False):
        return
    original = site.get_app_list

    def grouped(self, request, app_label=None):
        return group_app_list(original(request, app_label))

    site.get_app_list = MethodType(grouped, site)
    from django.contrib import admin
    from lab.models import Lot, Preparation

    @admin.display(description="Приготовление")
    def preparation_title(self, obj):
        return str(obj)

    preparation_admin = site._registry[Preparation]
    preparation_admin.preparation_title = MethodType(preparation_title, preparation_admin)
    preparation_admin.list_display = ("preparation_title", "processed", "approved_by", "output_storage_place")
    preparation_admin.list_display_links = ("preparation_title",)
    preparation_admin.list_select_related = tuple(dict.fromkeys((*preparation_admin.list_select_related, "output_storage_place", "approved_by")))

    lot_admin = site._registry[Lot]
    numeric_fields = ("initial_quantity", "quantity", "reserved", "free_qty")
    replacements = {}
    for field in numeric_fields:
        original_method = getattr(lot_admin, field, None)
        original_description = getattr(original_method, "short_description", None)
        heading = original_description or (Lot._meta.get_field(field).verbose_name if field != "free_qty" else "Свободно")

        def display(self, obj, field=field):
            return compact_number(obj.free if field == "free_qty" else getattr(obj, field))

        display.__name__ = "ui_" + field
        display.short_description = heading
        if field != "free_qty":
            display.admin_order_field = field
        setattr(lot_admin, display.__name__, MethodType(display, lot_admin))
        replacements[field] = display.__name__
    lot_admin.list_display = tuple(replacements.get(field, field) for field in lot_admin.list_display)

    @admin.display(description="Продукт / приготовленная партия", ordering="name")
    def product_title(self, obj):
        try:
            preparation = obj.production_record
        except AttributeError:
            preparation = None
        return str(obj) if preparation is not None and preparation.kind == "Приготовление реактива" else obj.name

    lot_admin.ui_product_title = MethodType(product_title, lot_admin)
    lot_admin.list_display = tuple("ui_product_title" if field == "name" else field for field in lot_admin.list_display)
    lot_admin.list_display_links = ("ui_product_title",)
    lot_admin.list_select_related = tuple(dict.fromkeys((*lot_admin.list_select_related, "production_record", "production_record__actor", "production_record__recipe__product", "production_record__output")))
    site._lab_ui_stage1 = True
