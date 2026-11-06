from .sample_projects8a import build_sample,graph_lock,SampleProjectsField
import json
from types import MethodType
from django import forms
from django.contrib import messages
from django.contrib.admin.widgets import AdminDateWidget
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template import engines
from django.urls import path, reverse
from .models import Audit, Sample, StoragePlace
from .sample_batch import SampleBatchForm
from .sample_dates import SAMPLE_FIELDS, validate_sample_dates

def date_field(label):
    return forms.DateField(label=label, required=False, input_formats=['%d.%m.%Y', '%Y-%m-%d'], widget=AdminDateWidget(format='%d.%m.%Y'))

class Stage5BBatchForm(SampleBatchForm):
    sample_type = forms.ChoiceField(label='Тип пробы', required=False, choices=[('', '---------'), ('genotype', 'генотип'), ('bulk', 'bulk-проба')])
    plant_part = forms.CharField(label='Часть растения', required=False, max_length=240)
    sampling_location = forms.CharField(label='Место отбора пробы', required=False, max_length=500)
    sampling_date = date_field('Дата отбора')
    received_in_lab_date = date_field('Дата получения в лабораторию')
    storage_place = forms.ModelChoiceField(label='Место хранения из справочника', queryset=StoragePlace.objects.none(), required=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop('location', None)
        self.fields['storage_place'].queryset = StoragePlace.objects.filter(active=True)
        self.order_fields(['series', 'first', 'last', 'extra_numbers', 'project', 'kind', 'material_passport', *SAMPLE_FIELDS, 'storage_place', 'note'])
        for name in SAMPLE_FIELDS:
            self.fields[name].help_text = 'Общее значение для всех проб создаваемой партии. Для разных значений создайте отдельные партии или измените карточки.'

    def clean(self):
        data = super().clean()
        try:
            validate_sample_dates(data.get('sampling_date'), data.get('received_in_lab_date'))
        except ValidationError as exc:
            for field, errors in exc.message_dict.items():
                self.add_error(field, errors)
        return data
BATCH_TEMPLATE = '\n{% extends "admin/base_site.html" %}\n{% load static %}\n{% block extrahead %}\n{{ block.super }}\n<link rel="stylesheet" href="{% static \'admin/css/widgets.css\' %}">\n<script src="{% url \'admin:jsi18n\' %}"></script>\n{{ form.media }}\n{% endblock %}\n{% block content %}\n<h1>{{ title }}</h1>\n<p>Каждый номер создаёт отдельную карточку. При ошибке вся партия не сохраняется.</p>\n<p>Тип пробы, часть растения, место и даты ниже применяются ко всем новым пробам этой партии.\nНеизвестные сведения оставьте пустыми.</p>\n<form method="post">{% csrf_token %}{{ form.as_p }}\n<div class="submit-row">\n<input type="submit" value="Создать партию" class="default">\n<a href="{{ list_url }}">Вернуться к образцам</a>\n</div></form>\n{% endblock %}\n'

def batch_add_view(self, request):
    if not self.has_add_permission(request):
        raise PermissionDenied
    opts = self.model._meta
    list_url = reverse(f'admin:{opts.app_label}_{opts.model_name}_changelist')
    form = Stage5BBatchForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        try:
            with transaction.atomic():
                graph_lock()
                numbers = data['numbers']
                existing = list(Sample.objects.filter(series=data['series'], number__in=numbers).values_list('number', flat=True))
                if existing:
                    raise ValidationError('Уже существуют номера этой серии: ' + ', '.join(sorted(existing)))
                place = data['storage_place']
                place = StoragePlace.objects.select_for_update().get(pk=place.pk)
                if not place.active:
                    raise ValidationError('Выбранное место хранения уже неактивно.')
                samples = [build_sample(series=data['series'], number=number, project=data['project'], kind=data['kind'], material_passport=data.get('material_passport'), storage_place=place, location=place.name, note=data['note'], **{name: data.get(name) for name in SAMPLE_FIELDS}) for number in numbers]
                for sample in samples:
                    sample.full_clean()
                for sample in samples:
                    sample.save()
                    detail = {'действие': 'Создание в составе партии', 'код': sample.code, 'проекты': sorted(sample.projects.values_list('pk', flat=True)), 'место_хранения': place.pk, 'паспорт_материала': data['material_passport'].pk if data.get('material_passport') else None, **{name: data.get(name) for name in SAMPLE_FIELDS}}
                    Audit.objects.create(actor=request.user, entity=sample._meta.label, object_id=str(sample.pk), detail=json.dumps(detail, ensure_ascii=False, default=str))
                    self.log_addition(request, sample, [{'added': {}}])
        except ValidationError as exc:
            form.add_error(None, '; '.join(exc.messages))
        except StoragePlace.DoesNotExist:
            form.add_error('storage_place', 'Место хранения больше не существует.')
        except IntegrityError:
            form.add_error(None, 'Партия не сохранена из-за конфликта в базе. Проверьте номера и повторите ввод.')
        else:
            self.message_user(request, f"Создано проб: {len(samples)}. Серия: {data['series']}.", level=messages.SUCCESS)
            return redirect(list_url + '?series__exact=' + data['series'] if data['series'].isalnum() else list_url)
    context = {**self.admin_site.each_context(request), 'title': 'Добавить партию образцов', 'opts': opts, 'form': form, 'list_url': list_url}
    return HttpResponse(engines['django'].from_string(BATCH_TEMPLATE).render(context, request))

def _remove_fields(fields):
    result = []
    for item in fields:
        if isinstance(item, (tuple, list)):
            nested = _remove_fields(item)
            if nested:
                result.append(tuple(nested))
        elif item not in SAMPLE_FIELDS:
            result.append(item)
    return result

def install(site):
    modeladmin = site._registry[Sample]
    if getattr(modeladmin, '_stage5b1_installed', False):
        return
    previous_form = modeladmin.get_form
    previous_fieldsets = modeladmin.get_fieldsets
    previous_urls = modeladmin.get_urls

    def get_form(self, request, obj=None, **kwargs):
        base = previous_form(request, obj, **kwargs)

        class BoundForm(base):

            def __init__(form_self, *args, **form_kwargs):
                super().__init__(*args, **form_kwargs)
                for name in ('sampling_date', 'received_in_lab_date'):
                    if name in form_self.fields:
                        form_self.fields[name].widget = AdminDateWidget(format='%d.%m.%Y')
                        form_self.fields[name].input_formats = ['%d.%m.%Y', '%Y-%m-%d']
        return BoundForm

    def get_fieldsets(self, request, obj=None):
        groups = [('Отбор и получение', {'fields': SAMPLE_FIELDS})]
        for title, options in previous_fieldsets(request, obj):
            cleaned = _remove_fields(options.get('fields', ()))
            if cleaned:
                groups.append((title, {**options, 'fields': cleaned}))
        return groups

    def get_urls(self):
        opts = self.model._meta
        name = f'{opts.app_label}_{opts.model_name}_batch_add'
        return [path('batch-add/', self.admin_site.admin_view(self.batch_add_view), name=name)] + [url for url in previous_urls() if getattr(url, 'name', None) != name]
    modeladmin.get_form = MethodType(get_form, modeladmin)
    modeladmin.get_fieldsets = MethodType(get_fieldsets, modeladmin)
    modeladmin.batch_add_view = MethodType(batch_add_view, modeladmin)
    modeladmin.get_urls = MethodType(get_urls, modeladmin)
    modeladmin.list_filter = tuple(modeladmin.list_filter) + tuple((name for name in ('sample_type', 'sampling_date', 'received_in_lab_date') if name not in modeladmin.list_filter))
    modeladmin.search_fields = tuple(modeladmin.search_fields) + tuple((name for name in ('plant_part', 'sampling_location') if name not in modeladmin.search_fields))
    modeladmin._stage5b1_installed = True
