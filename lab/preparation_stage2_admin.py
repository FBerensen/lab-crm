from types import MethodType
from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from .preparation_access import can_mark_autoclaving


def configure_admin(site):
    if getattr(site, "_lab_preparation_stage2", False):
        return
    from .models import Preparation
    modeladmin = site._registry[Preparation]
    original_urls = modeladmin.get_urls

    def mark_view(self, request, object_id):
        obj = self.get_object(request, str(object_id))
        if obj is None:
            raise Http404
        if not can_mark_autoclaving(request.user, obj):
            raise PermissionDenied
        class MarkForm(forms.Form):
            confirmed_full_batch = forms.BooleanField(
                required=True,
                label=f"Автоклавирование выполнено для всей исходной партии: {obj.qty} {obj.output.unit if obj.output_id else ''}",
            )
        form = MarkForm(request.POST if request.method == "POST" else None)
        if request.method == "POST" and form.is_valid():
            try:
                obj.mark_autoclaved(request.user, confirmed_full_batch=form.cleaned_data["confirmed_full_batch"])
            except ValidationError as exc:
                form.add_error(None, " ".join(exc.messages))
            else:
                self.message_user(request, "Обработка отмечена. Допуск к использованию выполняется отдельно.", messages.SUCCESS)
                return HttpResponseRedirect(reverse("admin:lab_preparation_change", args=[obj.pk]))
        return TemplateResponse(request, "admin/lab/preparation/mark_autoclaved.html", {
            **self.admin_site.each_context(request), "title": "Отметить автоклавирование всей партии",
            "opts": self.model._meta, "original": obj, "form": form,
            "cancel_url": reverse("admin:lab_preparation_change", args=[obj.pk]),
        })

    def urls(self):
        return [path("<int:object_id>/mark-autoclaved/", self.admin_site.admin_view(self.mark_autoclaved_view), name="lab_preparation_mark_autoclaved")] + original_urls()

    @admin.display(description="Отметка обработки")
    def control(self, obj):
        if obj is None or not obj.pk or not obj.processed:
            return "Сначала проведите приготовление."
        if obj.autoclaving_done:
            return "Выполнено; автор и время указаны ниже. Снять или перезаписать отметку через эту форму нельзя."
        if obj.approved_at is not None:
            return "Партия уже допущена; прежняя история допуска сохранена."
        if obj.autoclaving_required is None:
            return "Историческая запись без достоверного требования обработки. Нужна отдельная проверка."
        if not obj.autoclaving_required:
            return "Автоклавирование не требуется."
        return format_html('<a class="button" href="{}">Отметить выполнение всей партии</a>', reverse("admin:lab_preparation_mark_autoclaved", args=[obj.pk]))

    modeladmin.mark_autoclaved_view = MethodType(mark_view, modeladmin)
    modeladmin.get_urls = MethodType(urls, modeladmin)
    modeladmin.autoclaving_control = MethodType(control, modeladmin)
    additions = ("autoclaving_control", "autoclaving_done", "autoclaved_by", "autoclaved_at")
    modeladmin.readonly_fields = (*modeladmin.readonly_fields, *additions)
    fields = list(modeladmin.fields)
    index = fields.index("autoclaving_required") + 1
    fields[index:index] = additions
    modeladmin.fields = tuple(fields)
    modeladmin.list_select_related = tuple(dict.fromkeys((*modeladmin.list_select_related, "autoclaved_by")))
    site._lab_preparation_stage2 = True
