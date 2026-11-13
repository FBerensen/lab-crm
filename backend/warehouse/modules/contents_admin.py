from types import MethodType
from collections.abc import Mapping
from urllib.parse import urlencode
from django.contrib import admin
from django.core.paginator import Paginator
from django.db.models import Q, Prefetch
from django.http import HttpResponseNotAllowed
from django.urls import reverse
from django.utils.html import format_html
from lab.models import StoragePlace, Sample, Lot, SourceBlock, SourceBlockWell
from lab.storage_support import descendant_ids, place_path
from lab.storage8d_support import occupancy, block_code_id
from lab.presentation import compact_number

GROUPS = (("all", "Всё содержимое"), ("samples", "Образцы и пробы"),
          ("lots", "Складские продукты"), ("blocks", "Исходные блоки"))
COLUMNS = {
    "samples": (("name", "Серия и номер"), ("kind", "Тип материала"),
                ("projects", "Проекты"), ("place", "Место")),
    "lots": (("name", "Продукт"), ("category", "Внесение"),
             ("quantity", "Физический остаток"), ("reserved", "Резерв"),
             ("free", "Свободно"), ("unit", "Единица"), ("place", "Место"),
             ("expiry", "Годен до"), ("released", "Допуск"), ("code", "Код")),
    "blocks": (("name", "Название"), ("place", "Место"),
               ("filled", "Заполнено лунок"), ("samples", "Пробы: лунок / различных"),
               ("controls", "Контроли"), ("raw", "Не зарегистрировано"),
               ("layout", "Схема"), ("code", "Код")),
}
PAGE_KEYS = {"samples": "sample_page", "lots": "lot_page", "blocks": "block_page"}

def page_size(value):
    return int(value) if str(value) in {"25", "50", "100"} else 50

def selected_columns(params, group):
    allowed = COLUMNS[group]
    if params.get("columns_set") != "1":
        return allowed
    chosen = set(params.getlist(group + "_cols"))
    result = tuple(item for item in allowed if item[0] in chosen or item[0] == "name")
    return result

def page_links(params, page, key):
    result = {}
    for label, number in (("previous", page.previous_page_number() if page.has_previous() else None),
                          ("next", page.next_page_number() if page.has_next() else None)):
        if number is not None:
            copied = params.copy()
            copied[key] = str(number)
            result[label] = "?" + copied.urlencode()
    return result

def stat_value(stats, key):
    return stats[key] if isinstance(stats, Mapping) else getattr(stats, key)

def cell(value, url=None):
    return {"value": value, "url": url}

def install(site):
    if getattr(site, "_storage8e_installed", False):
        return
    if not getattr(site, "_storage8d_installed", False):
        raise RuntimeError("8E requires installed 8D")
    places_admin = site._registry[StoragePlace]
    old_contents = places_admin.contents_view
    admins = {"samples": site._registry[Sample], "lots": site._registry[Lot],
              "blocks": site._registry[SourceBlock]}

    @admin.display(description="Содержимое")
    def contents_link(self, obj):
        if not obj.pk:
            return "Сначала сохраните место."
        return format_html(
            '<a class="button" style="display:inline-block;padding:10px 16px;font-size:14px;line-height:1.4" href="{}">Посмотреть содержимое</a>',
            reverse(site.name + ":lab_storageplace_contents", args=[obj.pk]))

    def contents(self, request, object_id):
        if request.method not in {"GET", "HEAD"}:
            return HttpResponseNotAllowed(["GET", "HEAD"])
        response = old_contents(request, object_id)
        if not hasattr(response, "context_data") or response.is_rendered:
            raise RuntimeError("8E requires unrendered storage TemplateResponse")
        ctx = response.context_data
        params = request.GET
        group = params.get("content_type", "all")
        if group not in dict(GROUPS):
            group = "all"
        places = list(StoragePlace.objects.all())
        ids = descendant_ids(ctx["place"].pk, places) if ctx["include_children"] else {ctx["place"].pk}
        permissions = {"samples": ctx["can_samples"], "lots": ctx["can_lots"], "blocks": ctx["can_blocks"]}
        kinds = list(Sample._meta.get_field("kind").choices)
        selected_kind = params.get("material_kind", "")
        if selected_kind not in dict(kinds):
            selected_kind = ""
        category_options = []
        if permissions["lots"]:
            category_options = list(admins["lots"].get_queryset(request).filter(
                storage_place_id__in=ids).exclude(category_id=None).order_by(
                "category__name", "category_id").values_list("category_id", "category__name").distinct())
        valid_categories = {str(pk) for pk, name in category_options}
        selected_category = params.get("category", "")
        if selected_category not in valid_categories:
            selected_category = ""
        sections = []
        settings = []
        for key in ("samples", "lots", "blocks"):
            if not permissions[key]:
                continue
            columns = selected_columns(params, key)
            size = page_size(params.get(key + "_size", "50"))
            chosen = {name for name, label in columns}
            settings.append({"key": key, "title": dict(GROUPS)[key], "size": size,
                "sizes": [{"value": n, "selected": n == size} for n in (25, 50, 100)],
                "columns": [{"key": name, "label": label, "checked": name in chosen,
                             "mandatory": name == "name"} for name, label in COLUMNS[key]]})
            if group not in {"all", key}:
                continue
            qs = admins[key].get_queryset(request).filter(storage_place_id__in=ids)
            query = ctx["q"]
            if key == "samples":
                qs = qs.select_related("storage_place").prefetch_related("projects").order_by("series", "number", "pk")
                if selected_kind:
                    qs = qs.filter(kind=selected_kind)
                if query:
                    qs = qs.filter(Q(series__icontains=query) | Q(number__icontains=query) |
                                   Q(projects__title__icontains=query)).distinct()
            elif key == "lots":
                qs = qs.select_related("storage_place", "category").order_by("name", "pk")
                if not ctx["show_empty"]:
                    qs = qs.filter(quantity__gt=0)
                if selected_category:
                    qs = qs.filter(category_id=selected_category)
                if query:
                    qs = qs.filter(Q(name__icontains=query) | Q(code__icontains=query) |
                                   Q(catalog_number__icontains=query) | Q(manufacturer__icontains=query))
            else:
                qs = qs.select_related("storage_place").order_by("name", "pk")
                if query:
                    criterion = (Q(name__icontains=query) | Q(note__icontains=query) |
                                 Q(wells__raw_label__icontains=query) | Q(wells__sample__series__icontains=query) |
                                 Q(wells__sample__number__icontains=query))
                    if " №" in query:
                        series, number = query.rsplit(" №", 1)
                        criterion |= Q(wells__sample__series__iexact=series, wells__sample__number__iexact=number)
                    code = block_code_id(query)
                    if code is not None:
                        criterion |= Q(pk=code)
                    qs = qs.filter(criterion).distinct()
                qs = qs.prefetch_related(Prefetch("wells", queryset=SourceBlockWell.objects.only(
                    "id", "block_id", "kind", "sample_id"), to_attr="_storage8e_wells"))
            page = Paginator(qs, size).get_page(params.get(PAGE_KEYS[key]))
            rows = []
            for obj in page.object_list:
                url = reverse(site.name + ":lab_" + obj._meta.model_name + "_change", args=[obj.pk])
                values = {"name": cell(obj.code if key == "samples" else obj.name, url),
                          "place": cell(place_path(obj.storage_place_id, places))}
                if key == "samples":
                    values.update(kind=cell(obj.kind), projects=cell(obj.project_names))
                elif key == "lots":
                    expiry = str(obj.expiry) if obj.expiry else "Не указан"
                    if obj.expired:
                        expiry += " — просрочено"
                    values.update(category=cell(obj.category.name if obj.category_id else "Не указано"),
                        quantity=cell(compact_number(obj.quantity)), reserved=cell(compact_number(obj.reserved)),
                        free=cell(compact_number(obj.free)), unit=cell(obj.unit), expiry=cell(expiry),
                        released=cell("Допущен" if obj.released else "Не допущен"), code=cell(obj.code))
                else:
                    stats = occupancy(obj._storage8e_wells)
                    values.update(filled=cell(str(stat_value(stats, "filled")) + " / 96"),
                        samples=cell(str(stat_value(stats, "sample_wells")) + " / " + str(stat_value(stats, "unique_samples"))),
                        controls=cell(stat_value(stats, "controls")), raw=cell(stat_value(stats, "raw")), code=cell(obj.code, url),
                        layout=cell("Открыть схему", reverse(site.name + ":lab_sourceblock_layout", args=[obj.pk])))
                rows.append([values[name] for name, label in columns])
            sections.append({"key": key, "title": dict(GROUPS)[key], "columns": columns,
                "rows": rows, "page": page, "links": page_links(params, page, PAGE_KEYS[key])})
        ctx.update({"storage8e_sections": sections, "storage8e_settings": settings,
            "storage8e_groups": [{"value": key, "label": label, "selected": key == group}
                                for key, label in GROUPS if key == "all" or permissions[key]],
            "storage8e_kinds": [{"value": key, "label": label, "selected": key == selected_kind} for key, label in kinds],
            "storage8e_categories": [{"value": str(pk), "label": name, "selected": str(pk) == selected_category}
                                     for pk, name in category_options],
            "storage8e_can_samples": permissions["samples"], "storage8e_can_lots": permissions["lots"],
            "storage8e_reset": reverse(site.name + ":lab_storageplace_contents", args=[ctx["place"].pk])})
        response.template_name = "admin/lab/storageplace/contents8e.html"
        return response

    places_admin.contents_link = MethodType(contents_link, places_admin)
    places_admin.contents_view = MethodType(contents, places_admin)
    site._storage8e_installed = True
