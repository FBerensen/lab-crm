import json
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from .models import (
    Audit,
    Project,
    Sample,
    StoragePlace,
    validate_sample_number,
    validate_sample_series,
)


def can_edit_samples(user):
    return bool(
        user
        and user.is_active
        and user.is_staff
        and (
            user.is_superuser
            or user.groups.filter(
                name__in=("Руководители", "Исполнители")
            ).exists()
        )
    )


class SampleAdminForm(forms.ModelForm):
    """Форма добавления/редактирования образца с раздельным вводом серии и номера исходной пробы."""

    parent_series = forms.CharField(
        label="Исходный образец/проба — серия",
        required=False,
        max_length=5,
        validators=[validate_sample_series],
        help_text="Укажите серию исходной пробы (2–5 символов), если проба производная.",
    )
    parent_number = forms.CharField(
        label="Исходный образец/проба — номер",
        required=False,
        max_length=20,
        validators=[validate_sample_number],
        help_text="Укажите номер исходной пробы (числа 1–150, символы * , . .. :).",
    )

    class Meta:
        model = Sample
        fields = (
            "series",
            "number",
            "project",
            "kind",
            "storage_place",
            "location",
            "parent",
            "parent_series",
            "parent_number",
            "note",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "storage_place" in self.fields:
            current_place_id = getattr(self.instance, "storage_place_id", None)
            self.fields["storage_place"].queryset = StoragePlace.objects.filter(
                Q(active=True) | Q(pk=current_place_id)
            )
            self.fields["storage_place"].empty_label = "Выберите место хранения"

        if self.instance and self.instance.pk and self.instance.parent_id:
            self.fields["parent_series"].initial = self.instance.parent.series
            self.fields["parent_number"].initial = self.instance.parent.number

    def clean(self):
        cleaned = super().clean()
        p_series = (cleaned.get("parent_series") or "").strip()
        p_number = (cleaned.get("parent_number") or "").strip()
        parent = cleaned.get("parent")
        project = cleaned.get("project") or getattr(self.instance, "project", None)

        if p_series or p_number:
            if not (p_series and p_number):
                raise forms.ValidationError(
                    "Для указания исходного образца по серии и номеру заполните оба поля: "
                    "«Исходный образец/проба — серия» и «Исходный образец/проба — номер»."
                )
            qs = Sample.objects.filter(series=p_series, number=p_number)
            if project:
                qs = qs.filter(project=project)
            found_parent = qs.first()
            if not found_parent:
                raise forms.ValidationError(
                    f"Исходный образец/проба с серией «{p_series}» и номером «{p_number}» "
                    "не найден в выбранном проекте."
                )
            cleaned["parent"] = found_parent
            self.instance.parent = found_parent
        elif parent:
            cleaned["parent"] = parent

        storage_place = cleaned.get("storage_place")
        location = (cleaned.get("location") or "").strip()
        if storage_place:
            cleaned["location"] = storage_place.name
            self.instance.location = storage_place.name
        elif not location:
            raise forms.ValidationError(
                "Выберите «Место хранения (из справочника)» или укажите место хранения вручную."
            )

        return cleaned


class SampleBatchForm(forms.Form):
    series = forms.CharField(
        label="Серия",
        min_length=2,
        max_length=5,
        validators=[validate_sample_series],
    )
    first = forms.IntegerField(
        label="Первый номер", min_value=1, max_value=150, initial=1
    )
    last = forms.IntegerField(
        label="Последний номер", min_value=1, max_value=150
    )
    extra_numbers = forms.CharField(
        label="Дополнительные номера",
        required=False,
        max_length=3000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=(
            "Например: 5* 12* 52.. 75:. Разделяйте номера пробелами "
            "или переносами строк. Запятая может быть частью номера."
        ),
    )
    project = forms.ModelChoiceField(
        label="Проект", queryset=Project.objects.all()
    )
    kind = forms.ChoiceField(
        label="Тип материала",
        choices=Sample._meta.get_field("kind").choices,
    )
    storage_place = forms.ModelChoiceField(
        label="Место хранения (из справочника)",
        queryset=StoragePlace.objects.filter(active=True),
        required=False,
    )
    location = forms.CharField(
        label="Место хранения (текстом, если нет в справочнике)",
        required=False,
        max_length=Sample._meta.get_field("location").max_length,
    )
    note = forms.CharField(
        label="Общее примечание",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def clean(self):
        data = super().clean()
        first, last = data.get("first"), data.get("last")

        storage_place = data.get("storage_place")
        location = (data.get("location") or "").strip()
        if storage_place:
            data["location"] = storage_place.name
        elif not location:
            raise forms.ValidationError(
                "Выберите место хранения из справочника или укажите его текстом."
            )

        if first is None or last is None:
            return data

        if first > last:
            raise forms.ValidationError(
                "Первый номер не должен быть больше последнего."
            )

        numbers = [str(n) for n in range(first, last + 1)]
        extras = data.get("extra_numbers", "").split()

        if len(extras) > 300:
            raise forms.ValidationError(
                "За один раз допускается не больше 300 дополнительных номеров."
            )

        for value in extras:
            if len(value) > 20:
                raise forms.ValidationError(
                    f"Слишком длинный номер: {value[:30]}"
                )
            if not any(char in "0123456789" for char in value):
                raise forms.ValidationError(
                    f"Номер должен содержать цифры: {value}"
                )
            try:
                validate_sample_number(value)
            except ValidationError as exc:
                raise forms.ValidationError(
                    f"Номер {value}: {'; '.join(exc.messages)}"
                )

        numbers.extend(extras)

        if len(set(numbers)) != len(numbers):
            raise forms.ValidationError(
                "Внутри партии есть повторяющиеся номера. "
                "Проверьте диапазон и дополнительные номера."
            )

        data["numbers"] = numbers
        return data


class SampleBatchMixin:
    form = SampleAdminForm
    batch_add_template = "admin/lab/sample/batch_add.html"
    audit_in_save_model = False

    def has_add_permission(self, request):
        return can_edit_samples(request.user)

    def has_change_permission(self, request, obj=None):
        return can_edit_samples(request.user)

    def has_delete_permission(self, request, obj=None):
        return False

    def add_view(self, request, form_url="", extra_context=None):
        extra = dict(extra_context or {})
        extra.setdefault("title", "Добавить образец/ пробу")
        return super().add_view(request, form_url=form_url, extra_context=extra)

    def get_urls(self):
        opts = self.model._meta
        custom = [
            path(
                "batch-add/",
                self.admin_site.admin_view(self.batch_add_view),
                name=f"{opts.app_label}_{opts.model_name}_batch_add",
            )
        ]
        return custom + super().get_urls()

    def batch_add_view(self, request):
        if not self.has_add_permission(request):
            raise PermissionDenied

        opts = self.model._meta
        list_url = reverse(
            f"admin:{opts.app_label}_{opts.model_name}_changelist"
        )
        form = SampleBatchForm(
            request.POST if request.method == "POST" else None
        )

        if request.method == "POST" and form.is_valid():
            data = form.cleaned_data

            try:
                with transaction.atomic():
                    numbers = data["numbers"]

                    existing = list(
                        Sample.objects.filter(
                            series=data["series"],
                            number__in=numbers,
                        ).values_list("number", flat=True)
                    )

                    if existing:
                        raise ValidationError(
                            "Уже существуют номера этой серии: "
                            + ", ".join(sorted(existing))
                        )

                    samples = [
                        Sample(
                            series=data["series"],
                            number=number,
                            project=data["project"],
                            kind=data["kind"],
                            storage_place=data.get("storage_place"),
                            location=data["location"],
                            note=data["note"],
                        )
                        for number in numbers
                    ]

                    for sample in samples:
                        sample.full_clean()

                    for sample in samples:
                        sample.save()

                        Audit.objects.create(
                            actor=request.user,
                            entity=sample._meta.label,
                            object_id=str(sample.pk),
                            detail=(
                                "Создание в составе партии: "
                                f"{sample.code}; "
                                f"проект={sample.project_id}"
                            ),
                        )
                        self.log_addition(
                            request,
                            sample,
                            [{"added": {}}],
                        )

            except ValidationError as exc:
                form.add_error(None, "; ".join(exc.messages))

            except IntegrityError:
                form.add_error(
                    None,
                    "Партия не сохранена из-за конфликта в базе. "
                    "Возможно, один из номеров одновременно "
                    "создал другой сотрудник. Проверьте список образцов.",
                )

            else:
                self.message_user(
                    request,
                    f"Создано проб: {len(samples)}. "
                    f"Серия: {data['series']}.",
                    level=messages.SUCCESS,
                )
                query = urlencode({"series__exact": data["series"]})
                return redirect(f"{list_url}?{query}")

        context = {
            **self.admin_site.each_context(request),
            "title": "Добавить партию образцов",
            "opts": opts,
            "form": form,
            "list_url": list_url,
        }

        return TemplateResponse(request, self.batch_add_template, context)

    def save_model(self, request, obj, form, change):
        before = None

        if change:
            before = Sample.objects.filter(pk=obj.pk).values().get()

        super().save_model(request, obj, form, change)

        if not change:
            Audit.objects.create(
                actor=request.user,
                entity=obj._meta.label,
                object_id=str(obj.pk),
                detail=f"Создание записи: {obj.code}; проект={obj.project_id}",
            )
        elif before is not None:
            after = Sample.objects.filter(pk=obj.pk).values().get()
            changes = {
                key: {
                    "было": before.get(key),
                    "стало": after.get(key),
                }
                for key in after
                if before.get(key) != after.get(key)
            }

            if changes:
                Audit.objects.create(
                    actor=request.user,
                    entity=obj._meta.label,
                    object_id=str(obj.pk),
                    detail=json.dumps(
                        changes,
                        ensure_ascii=False,
                        cls=DjangoJSONEncoder,
                    ),
                )