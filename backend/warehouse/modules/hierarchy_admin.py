from types import MethodType
from urllib.parse import urlencode
from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from lab.presentation import compact_number
from lab.storage_support import descendant_ids, place_path


def configure_admin(site):
    if getattr(site, "_lab_storage_stage4", False):
        return
    from lab.models import StoragePlace, Sample, Lot
    place_admin = site._registry[StoragePlace]
    sample_admin = site._registry[Sample]
    lot_admin = site._registry[Lot]
    from lab.preparation_access import role_state
    original_urls = place_admin.get_urls
    original_queryset = place_admin.get_queryset

    def queryset(self, request):
        return original_queryset(request).select_related("parent").annotate(
            ui_lot_count=Count("products", distinct=True), ui_sample_count=Count("stored_samples", distinct=True))

    @admin.display(description="Складских карточек непосредственно здесь", ordering="ui_lot_count")
    def lots_count(self, obj):
        return obj.ui_lot_count

    @admin.display(description="Образцов непосредственно здесь", ordering="ui_sample_count")
    def samples_count(self, obj):
        return obj.ui_sample_count

    @admin.display(description="Содержимое")
    def contents_link(self, obj):
        if not obj.pk:
            return "Сначала сохраните место."
        return format_html('<a href="{}">Посмотреть содержимое</a>', reverse("admin:lab_storageplace_contents", args=[obj.pk]))

    def contents(self, request, object_id):
        place = self.get_object(request, str(object_id))
        if place is None:
            raise Http404
        if not self.has_view_permission(request, place):
            raise PermissionDenied
        places = list(StoragePlace.objects.all())
        include = request.GET.get("include_children", "1") != "0"
        show_empty = request.GET.get("show_empty") == "1"
        selected_ids = descendant_ids(place.pk, places) if include else {place.pk}
        query = request.GET.get("q", "").strip()
        can_lots = lot_admin.has_view_permission(request)
        can_samples = sample_admin.has_view_permission(request)
        lot_page = None
        sample_page = None
        lot_rows = []
        sample_rows = []
        if can_lots:
            lots = lot_admin.get_queryset(request).filter(storage_place_id__in=selected_ids).select_related("storage_place").order_by("name", "pk")
            if not show_empty:
                lots = lots.filter(quantity__gt=0)
            if query:
                lots = lots.filter(Q(name__icontains=query) | Q(code__icontains=query) | Q(catalog_number__icontains=query) | Q(manufacturer__icontains=query))
            lot_page = Paginator(lots, 50).get_page(request.GET.get("lot_page"))
            for lot in lot_page.object_list:
                lot_rows.append({"obj": lot, "url": reverse("admin:lab_lot_change", args=[lot.pk]),
                    "quantity": compact_number(lot.quantity), "reserved": compact_number(lot.reserved),
                    "free": compact_number(lot.free), "path": place_path(lot.storage_place_id, places)})
        unmatched = 0
        if can_samples:
            base = sample_admin.get_queryset(request)
            unmatched = base.filter(storage_place__isnull=True).count()
            samples = base.filter(storage_place_id__in=selected_ids).select_related("storage_place").prefetch_related("projects").order_by("series", "number", "pk")
            if query:
                samples = samples.filter(Q(series__icontains=query) | Q(number__icontains=query) | Q(projects__title__icontains=query)).distinct()
            sample_page = Paginator(samples, 50).get_page(request.GET.get("sample_page"))
            for sample in sample_page.object_list:
                sample_rows.append({"obj": sample, "url": reverse("admin:lab_sample_change", args=[sample.pk]), "path": place_path(sample.storage_place_id, places)})
        def page_links(page, key):
            params = request.GET.copy()
            links = {}
            if page is not None:
                if page.has_previous():
                    params[key] = page.previous_page_number()
                    links["previous"] = "?" + params.urlencode()
                if page.has_next():
                    params[key] = page.next_page_number()
                    links["next"] = "?" + params.urlencode()
                params.pop(key, None)
            return links
        return TemplateResponse(request, "admin/lab/storageplace/contents.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta, "title": "Содержимое: " + place.name,
            "place": place, "path": place_path(place.pk, places), "include_children": include, "show_empty": show_empty, "q": query,
            "children": [{"name": p.name, "url": reverse("admin:lab_storageplace_contents", args=[p.pk])} for p in places if p.parent_id == place.pk],
            "lot_rows": lot_rows, "sample_rows": sample_rows, "lot_page": lot_page, "sample_page": sample_page,
            "lot_links": page_links(lot_page, "lot_page"), "sample_links": page_links(sample_page, "sample_page"),
            "can_lots": can_lots, "can_samples": can_samples, "unmatched": unmatched,
            "unmatched_url": reverse("admin:lab_sample_changelist") + "?" + urlencode({"storage_place__isnull": "1"}),
            "cancel_url": reverse("admin:lab_storageplace_changelist"),
        })

    def urls(self):
        return [path("<int:object_id>/contents/", self.admin_site.admin_view(self.contents_view), name="lab_storageplace_contents")] + original_urls()

    place_admin.get_queryset = MethodType(queryset, place_admin)
    place_admin.get_urls = MethodType(urls, place_admin)
    place_admin.contents_view = MethodType(contents, place_admin)
    place_admin.ui_lots_count = MethodType(lots_count, place_admin)
    place_admin.ui_samples_count = MethodType(samples_count, place_admin)
    place_admin.contents_link = MethodType(contents_link, place_admin)
    place_admin.list_display = ("name", "parent", "active", "ui_lots_count", "ui_samples_count", "contents_link")
    place_admin.list_select_related = ("parent",)
    place_admin.fields = ("name", "parent", "active", "sort_order", "contents_link")
    place_admin.readonly_fields = (*place_admin.readonly_fields, "contents_link")
    place_admin.list_filter = ("active", "parent")
    def manage_place(self, request, obj=None):
        return role_state(request.user) in {"lead", "administrator"}
    place_admin.has_add_permission = MethodType(manage_place, place_admin)
    place_admin.has_change_permission = MethodType(manage_place, place_admin)

    previous_form = sample_admin.get_form
    def sample_form(self, request, obj=None, **kwargs):
        base = previous_form(request, obj, **kwargs)
        class BoundForm(base):
            def __init__(self, *args, **form_kwargs):
                super().__init__(*args, **form_kwargs)
                if "storage_place" in self.fields:
                    current = obj.storage_place_id if obj is not None else None
                    self.fields["storage_place"].queryset = StoragePlace.objects.filter(Q(active=True) | Q(pk=current))
                    self.fields["storage_place"].required = obj is None
        return BoundForm
    sample_admin.get_form = MethodType(sample_form, sample_admin)
    sample_admin.readonly_fields = tuple(dict.fromkeys((*sample_admin.readonly_fields, "location", "original_location")))
    sample_admin.list_display = tuple("storage_place" if f == "location" else f for f in sample_admin.list_display)
    sample_admin.list_filter = tuple(dict.fromkeys((*sample_admin.list_filter, "storage_place")))
    sample_admin.search_fields = tuple(dict.fromkeys((*sample_admin.search_fields, "storage_place__name", "original_location")))
    sample_admin.list_select_related = ("project", "parent", "storage_place")
    site._lab_storage_stage4 = True
