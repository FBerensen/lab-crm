import json

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template import engines
from django.urls import path, reverse

from .models import (
    Audit, Project, Sample,
    validate_sample_number, validate_sample_series,
)


def can_edit_samples(user):
    return bool(
        user.is_active
        and user.is_staff
        and (
            user.is_superuser
            or user.groups.filter(
                name__in=("Руководители", "Исполнители")
            ).exists()
        )
    )


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
            "Например: 5* 12* 52:. Разделяйте номера пробелами "
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
    location = forms.CharField(
        label="Место хранения",
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


BATCH_TEMPLATE = """
{% extends "admin/base_site.html" %}
{% block content %}
<h1>{{ title }}</h1>
<p>Каждый номер создаёт отдельную карточку.
При ошибке или дубликате вся партия не сохраняется.</p>
<form method="post">
  {% csrf_token %}
  {{ form.as_p }}
  <div class="submit-row">
    <input type="submit" value="Создать партию" class="default">
    <a href="{{ list_url }}">Вернуться к образцам</a>
  </div>
</form>
{% endblock %}
"""


class SampleBatchMixin:
    def has_add_permission(self, request):
        return can_edit_samples(request.user)

    def has_change_permission(self, request, obj=None):
        return can_edit_samples(request.user)

    def has_delete_permission(self, request, obj=None):
        return False

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
                            location=data["location"],
                            note=data["note"],
                        )
                        for number in numbers
                    ]

                    # Validate every card before saving any card.
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
                return redirect(
                    list_url + "?series__exact=" + data["series"]
                    if data["series"].isalnum()
                    else list_url
                )

        context = {
            **self.admin_site.each_context(request),
            "title": "Добавить партию образцов",
            "opts": opts,
            "form": form,
            "list_url": list_url,
        }

        template = engines["django"].from_string(BATCH_TEMPLATE)
        return HttpResponse(template.render(context, request))

    def save_model(self, request, obj, form, change):
        before = None

        if change:
            before = (
                Sample.objects.filter(pk=obj.pk).values().get()
            )

        super().save_model(request, obj, form, change)

        if change:
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