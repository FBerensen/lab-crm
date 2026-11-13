import json
from types import MethodType
from django import forms
from django.contrib import admin
from django.contrib.admin.widgets import AutocompleteSelect
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.urls import reverse
from django.utils.html import format_html
from lab.models import Audit, MaterialPassport, Sample
from lab.sample_batch import can_edit_samples
from lab.sample_stage5b_admin import Stage5BBatchForm

class MaterialPassportAdmin(admin.ModelAdmin):
    list_display = ("id", "vir_catalogue_number", "species", "origin", "related_samples")
    search_fields = ("vir_catalogue_number", "species", "origin")
    fields = ("vir_catalogue_number", "species", "origin", "related_samples")
    readonly_fields = ("related_samples",)
    ordering = ("vir_catalogue_number", "pk")

    def has_add_permission(self, request):
        return can_edit_samples(request.user)

    def has_change_permission(self, request, obj=None):
        return can_edit_samples(request.user)

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        sample_admin = self.admin_site._registry[Sample]
        return bool(can_edit_samples(request.user) or sample_admin.has_view_permission(request))

    def has_module_permission(self, request):
        return self.has_view_permission(request)

    @admin.display(description="Связанные пробы")
    def related_samples(self, obj):
        if obj is None or obj.pk is None:
            return "Сначала сохраните паспорт."
        url = reverse(f"{self.admin_site.name}:lab_sample_changelist")
        return format_html('<a href="{}?material_passport__id__exact={}">Открыть пробы</a>', url, obj.pk)

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        before = MaterialPassport.objects.filter(pk=obj.pk).values().get() if change else None
        obj.full_clean()
        super().save_model(request, obj, form, change)
        after = MaterialPassport.objects.filter(pk=obj.pk).values().get()
        changes = {key: {"было": before.get(key) if before else None, "стало": value}
                   for key, value in after.items() if before is None or before.get(key) != value}
        if changes:
            Audit.objects.create(actor=request.user, entity=obj._meta.label,
                object_id=str(obj.pk), detail=json.dumps({
                    "действие": "Изменение паспорта" if change else "Создание паспорта",
                    "изменения": changes,
                }, cls=DjangoJSONEncoder, ensure_ascii=False))


def strip_passport(fields):
    result = []
    for item in fields:
        if isinstance(item, (tuple, list)):
            nested = strip_passport(item)
            if nested:
                result.append(tuple(nested))
        elif item != "material_passport":
            result.append(item)
    return result


def install(site):
    sample_admin = site._registry[Sample]
    if getattr(sample_admin, "_stage5b2_installed", False):
        return
    if not site.is_registered(MaterialPassport):
        site.register(MaterialPassport, MaterialPassportAdmin)
    previous_fieldsets = sample_admin.get_fieldsets
    def get_fieldsets(self, request, obj=None):
        result = [("Паспорт материала", {"fields": ("material_passport",)})]
        for title, options in previous_fieldsets(request, obj):
            fields = strip_passport(options.get("fields", ()))
            if fields:
                result.append((title, {**options, "fields": fields}))
        return result
    sample_admin.get_fieldsets = MethodType(get_fieldsets, sample_admin)
    sample_admin.autocomplete_fields = tuple(sample_admin.autocomplete_fields) + ("material_passport",)
    sample_admin.search_fields = tuple(sample_admin.search_fields) + (
        "material_passport__vir_catalogue_number", "material_passport__species", "material_passport__origin",
    )
    Stage5BBatchForm.base_fields["material_passport"] = forms.ModelChoiceField(
        label="Паспорт материала", queryset=MaterialPassport.objects.all(), required=False,
        widget=AutocompleteSelect(Sample._meta.get_field("material_passport"), site),
        help_text="Общий паспорт всех создаваемых проб. Неизвестный паспорт оставьте пустым.",
    )
    sample_admin._stage5b2_installed = True
