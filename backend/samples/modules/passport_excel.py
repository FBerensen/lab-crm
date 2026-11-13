from lab.sample_projects8a import build_sample,graph_lock,SampleProjectsField
import hashlib
import json
from io import BytesIO
from pathlib import Path
from types import MethodType
from zipfile import ZipFile, BadZipFile
from django import forms
from django.contrib import messages
from django.contrib.admin.widgets import AdminDateWidget
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template import engines
from django.urls import path, reverse
from django.utils.html import format_html
from lab.models import Sample, MaterialPassport, StoragePlace, Audit, validate_sample_series, validate_sample_number
from lab.sample_batch import can_edit_samples
from lab.sample_stage5b_admin import Stage5BBatchForm
from lab.sample_dates import SAMPLE_FIELDS
HEADERS = ('Серия', 'Номер', 'Кат № ВИР', 'Вид', 'Происхождение', 'Примечание')
PASSPORT = ('vir_catalogue_number', 'species', 'origin')
INLINE = ('passport_vir', 'passport_species', 'passport_origin')
SALT = 'labcrm.passport-excel.5b3.v1'
MAX_ROWS = 1000

def passport_values(sample):
    p = sample.material_passport if sample.material_passport_id else None
    return tuple((getattr(p, name) if p else '' for name in PASSPORT))

def fingerprint(sample):
    content = [sample.pk, sample.series, sample.number, sample.material_passport_id, *passport_values(sample), sample.note]
    return hashlib.sha256(json.dumps(content, ensure_ascii=False).encode()).hexdigest()

def audit(user, obj, detail):
    Audit.objects.create(actor=user, entity=obj._meta.label, object_id=str(obj.pk), detail=json.dumps(detail, cls=DjangoJSONEncoder, ensure_ascii=False))

def assign_passport(sample, values, user):
    values = tuple(((v or '').strip() for v in values))
    if values == passport_values(sample):
        return False
    if any(values):
        passport = MaterialPassport(**dict(zip(PASSPORT, values)))
        passport.full_clean()
        passport.save()
        audit(user, passport, {'действие': 'Индивидуальный паспорт пробы', 'проба': sample.code, **dict(zip(PASSPORT, values))})
        sample.material_passport = passport
    else:
        sample.material_passport = None
    return True

class SamplePassportForm(forms.ModelForm):
    passport_vir = forms.CharField(label='Кат № ВИР', required=False, max_length=100)
    passport_species = forms.CharField(label='Вид', required=False, max_length=240)
    passport_origin = forms.CharField(label='Происхождение', required=False, widget=forms.TextInput)

    class Meta:
        model = Sample
        exclude = ('material_passport',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field, value in zip(INLINE, passport_values(self.instance)):
            self.initial.setdefault(field, value)

class UploadForm(forms.Form):
    excel_file = forms.FileField(label='Файл Excel (.xlsx)')
    overwrite = forms.BooleanField(label='Заменять уже заполненные значения непустыми значениями из файла', required=False)

class BatchForm(Stage5BBatchForm):
    excel_file = forms.FileField(label='Файл Excel с паспортными данными (.xlsx)', required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop('material_passport', None)
        self.order_fields(['series', 'first', 'last', 'extra_numbers', 'project', 'kind', *SAMPLE_FIELDS, 'storage_place', 'note', 'excel_file'])
        self.fields['excel_file'].help_text = 'Без файла паспортные поля останутся пустыми. С файлом нужны строки для всех номеров партии.'

def excel_text(cell, number=False):
    if cell.data_type == 'f':
        raise ValidationError('Формулы в импортируемых ячейках запрещены.')
    value = cell.value
    if value is None:
        return ''
    if isinstance(value, bool):
        raise ValidationError('Логические значения не поддерживаются.')
    if number and isinstance(value, int):
        return str(value)
    if not isinstance(value, str):
        raise ValidationError('Используйте текстовые ячейки; в номере также допустимо целое число.')
    return value.strip()

def parse_excel(upload):
    from openpyxl import load_workbook
    if Path(upload.name).suffix.lower() != '.xlsx':
        raise ValidationError('Нужен файл .xlsx.')
    if upload.size > 5 * 1024 * 1024:
        raise ValidationError('Максимальный размер файла — 5 МБ.')
    content = upload.read(5 * 1024 * 1024 + 1)
    if len(content) > 5 * 1024 * 1024:
        raise ValidationError('Файл слишком большой.')
    try:
        with ZipFile(BytesIO(content)) as archive:
            items = archive.infolist()
            if len(items) > 2000 or sum((i.file_size for i in items)) > 50 * 1024 * 1024:
                raise ValidationError('Превышен допустимый распакованный размер Excel.')
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError('Не удалось прочитать файл Excel.') from exc
    try:
        if len(workbook.worksheets) != 1:
            raise ValidationError('В файле должен быть один лист с данными.')
        sheet = workbook.worksheets[0]
        if sheet.max_column and sheet.max_column > 30:
            raise ValidationError('В файле слишком много столбцов.')
        iterator = sheet.iter_rows()
        header = next(iterator, None)
        if header is None:
            raise ValidationError('Файл пуст.')
        names = [excel_text(cell) for cell in header]
        while names and (not names[-1]):
            names.pop()
        normalized = lambda text: ' '.join(text.casefold().replace('кат.', 'кат').split())
        if tuple(map(normalized, names)) != tuple(map(normalized, HEADERS)):
            raise ValidationError('Столбцы должны идти строго так: ' + ' | '.join(HEADERS))
        records, seen, errors = ([], set(), [])
        for line, cells in enumerate(iterator, 2):
            if line > MAX_ROWS + 1:
                raise ValidationError(f'Не более {MAX_ROWS} строк; удалите лишние, в том числе форматированные строки.')
            if all((cell.value is None for cell in cells)):
                continue
            try:
                values = [excel_text(cell, number=i == 1) for i, cell in enumerate(cells)]
                if any(values[6:]):
                    raise ValidationError('Лишние значения за столбцом Примечание.')
                values = (values + [''] * 6)[:6]
                series, number, vir, species, origin, note = values
                if not series or not number:
                    raise ValidationError('Серия и Номер обязательны.')
                if len(series) > 5 or len(number) > 20:
                    raise ValidationError('Слишком длинная серия или номер.')
                validate_sample_series(series)
                validate_sample_number(number)
                key = (series, number)
                if key in seen:
                    raise ValidationError('Повтор пары Серия + Номер.')
                seen.add(key)
                if len(vir) > 100 or len(species) > 240 or len(origin) > 10000 or (len(note) > 10000):
                    raise ValidationError('Превышена допустимая длина паспортного поля или примечания.')
                records.append({'line': line, 'series': series, 'number': number, 'values': [vir, species, origin], 'note': note})
            except ValidationError as exc:
                errors.append(f"Строка {line}: {'; '.join(exc.messages)}")
                if len(errors) >= 20:
                    break
        if errors:
            raise ValidationError(errors)
        if not records:
            raise ValidationError('Нет строк с данными.')
        return records
    finally:
        workbook.close()

def proposed(sample, row, overwrite):
    old = passport_values(sample)
    values = tuple((incoming if incoming and (overwrite or not current) else current for current, incoming in zip(old, row['values'])))
    note = row['note'] if row['note'] and (overwrite or not sample.note) else sample.note
    return (values, note)

def existing_preview(records, overwrite):
    query = Q(pk__in=[])
    for row in records:
        query |= Q(series=row['series'], number=row['number'])
    samples = Sample.objects.filter(query).select_related('material_passport')
    lookup = {(s.series, s.number): s for s in samples}
    preview, errors = ([], [])
    for row in records:
        sample = lookup.get((row['series'], row['number']))
        if sample is None:
            errors.append(f"Строка {row['line']}: проба {row['series']} №{row['number']} не найдена.")
            continue
        values, note = proposed(sample, row, overwrite)
        row['pk'], row['fingerprint'] = (sample.pk, fingerprint(sample))
        preview.append({'series': sample.series, 'number': sample.number, 'old': [*passport_values(sample), sample.note], 'new': [*values, note], 'changed': values != passport_values(sample) or note != sample.note})
    if errors:
        raise ValidationError(errors[:20])
    return preview

@transaction.atomic
def apply_existing(records, overwrite, user, modeladmin, request):
    graph_lock()
    ids = [r['pk'] for r in records]
    locked = {s.pk: s for s in Sample.objects.select_for_update().filter(pk__in=ids).order_by('pk')}
    for row in records:
        sample = locked.get(row['pk'])
        if sample is None or fingerprint(sample) != row['fingerprint']:
            raise ValidationError('Данные изменились после предпросмотра. Загрузите файл и проверьте его заново.')
    changed = 0
    for row in records:
        sample = locked[row['pk']]
        values, note = proposed(sample, row, overwrite)
        before = [*passport_values(sample), sample.note]
        passport_changed = assign_passport(sample, values, user)
        note_changed = note != sample.note
        if passport_changed or note_changed:
            sample.note = note
            sample.full_clean()
            sample.save(update_fields={'material_passport', 'note'})
            audit(user, sample, {'действие': 'Excel: паспорт и примечание', 'было': before, 'стало': [*values, note]})
            modeladmin.log_change(request, sample, 'Excel: паспорт и примечание')
            changed += 1
    return changed

def validate_batch_records(records, data):
    expected = {(data['series'], number) for number in data['numbers']}
    actual = {(r['series'], r['number']) for r in records}
    if actual != expected or len(actual) != len(records):
        missing, extra = (sorted(expected - actual), sorted(actual - expected))
        raise ValidationError(f'Excel не соответствует партии. Отсутствуют: {missing[:20]}; лишние: {extra[:20]}.')

@transaction.atomic
def create_batch(data, records, user, modeladmin, request):
    graph_lock()
    if records is not None:
        validate_batch_records(records, data)
    lookup = {(r['series'], r['number']): r for r in records or []}
    place = StoragePlace.objects.select_for_update().get(pk=data['storage_place'].pk)
    if not place.active:
        raise ValidationError('Место хранения уже неактивно.')
    if Sample.objects.filter(series=data['series'], number__in=data['numbers']).exists():
        raise ValidationError('В партии есть уже существующие номера. Ничего не создано.')
    samples = []
    for number in data['numbers']:
        row = lookup.get((data['series'], number))
        sample = build_sample(series=data['series'], number=number, project=data['project'], kind=data['kind'], storage_place=place, location=place.name, note=row['note'] if row and row['note'] else data['note'], **{name: data.get(name) for name in SAMPLE_FIELDS})
        sample.full_clean()
        samples.append((sample, row))
    for sample, row in samples:
        if row:
            assign_passport(sample, row['values'], user)
        sample.full_clean()
        sample.save()
        audit(user, sample, {'действие': 'Создание партии с Excel' if records else 'Создание партии вручную', 'код': sample.code, 'проекты': sorted(sample.projects.values_list('pk',flat=True)), 'паспорт': sample.material_passport_id})
        modeladmin.log_addition(request, sample, [{'added': {}}])
    return len(samples)

def seal(payload, user):
    payload = {**payload, 'user': user.pk, 'version': 1}
    token = signing.dumps(payload, salt=SALT, compress=True)
    if len(token) > 1000000:
        raise ValidationError('Предпросмотр слишком большой. Разделите файл на части.')
    return token

def unseal(token, user, mode):
    try:
        payload = signing.loads(token, salt=SALT, max_age=1800)
    except signing.BadSignature as exc:
        raise ValidationError('Предпросмотр устарел или повреждён. Загрузите файл заново.') from exc
    if payload.get('user') != user.pk or payload.get('mode') != mode or payload.get('version') != 1:
        raise ValidationError('Неверный предпросмотр.')
    return payload
TEMPLATE = '\n{% extends "admin/base_site.html" %}{% load static %}\n{% block extrahead %}{{ block.super }}\n<link rel="stylesheet" href="{% static \'admin/css/widgets.css\' %}">\n<script src="{% url \'admin:jsi18n\' %}"></script>{{ form.media }}{% endblock %}\n{% block content %}<h1>{{ title }}</h1>\n<p><a href="{{ template_url }}">Скачать шаблон Excel</a> |\n<a href="{{ import_url }}">Заполнить существующие пробы из Excel</a> |\n<a href="{{ list_url }}">К списку проб</a></p>\n<p>Столбцы: Серия | Номер | Кат № ВИР | Вид | Происхождение | Примечание.\nСерия и Номер определяют пробу. Пустые ячейки при обновлении не стирают значения.</p>\n{% if batch %}<p>Без Excel новые пробы создаются с пустым паспортом.\nОбщее примечание используется, если в строке Excel примечание пустое.</p>{% endif %}\n{% if errors %}<ul class="errorlist">{% for error in errors %}<li>{{ error }}</li>{% endfor %}</ul>{% endif %}\n{% if token %}\n<h2>Предварительный просмотр</h2>\n<p>Проверьте значения до сохранения. Предпросмотр действует 30 минут.</p>\n<table><thead><tr><th>Серия</th><th>Номер</th><th>До: ВИР / Вид / Происхождение / Примечание</th>\n<th>После: ВИР / Вид / Происхождение / Примечание</th><th>Изменение</th></tr></thead>\n<tbody>{% for row in preview %}<tr><td>{{ row.series }}</td><td>{{ row.number }}</td>\n<td>{% for value in row.old %}<div>{{ value|default:"—" }}</div>{% endfor %}</td>\n<td>{% for value in row.new %}<div>{{ value|default:"—" }}</div>{% endfor %}</td>\n<td>{{ row.changed|yesno:"Да,Нет" }}</td></tr>{% endfor %}</tbody></table>\n<form method="post">{% csrf_token %}<input type="hidden" name="phase" value="apply">\n<input type="hidden" name="token" value="{{ token }}">\n<input type="submit" value="Подтвердить и сохранить" class="default"></form>\n<p><a href="">Отменить и вернуться к вводу</a></p>\n{% else %}<form method="post" enctype="multipart/form-data">{% csrf_token %}{{ form.as_p }}\n<input type="submit" value="{% if batch %}Продолжить{% else %}Проверить файл{% endif %}" class="default"></form>{% endif %}\n{% endblock %}\n'

def render_page(modeladmin, request, form, batch=False, **extra):
    context = {**modeladmin.admin_site.each_context(request), 'opts': Sample._meta, 'form': form, 'title': 'Добавить партию проб' if batch else 'Паспортные данные из Excel', 'batch': batch, 'template_url': reverse('admin:lab_sample_passport_template'), 'import_url': reverse('admin:lab_sample_passport_excel'), 'list_url': reverse('admin:lab_sample_changelist'), **extra}
    return HttpResponse(engines['django'].from_string(TEMPLATE).render(context, request))

def import_view(self, request):
    if not can_edit_samples(request.user) or not self.has_change_permission(request):
        raise PermissionDenied
    form = UploadForm(request.POST or None, request.FILES or None)
    try:
        if request.method == 'POST' and request.POST.get('phase') == 'apply':
            payload = unseal(request.POST.get('token', ''), request.user, 'existing')
            count = apply_existing(payload['records'], payload['overwrite'], request.user, self, request)
            self.message_user(request, f'Обновлено проб: {count}. Остальные без изменений.', messages.SUCCESS)
            return redirect(reverse('admin:lab_sample_changelist'))
        if request.method == 'POST' and form.is_valid():
            records = parse_excel(form.cleaned_data['excel_file'])
            overwrite = form.cleaned_data['overwrite']
            preview = existing_preview(records, overwrite)
            token = seal({'mode': 'existing', 'records': records, 'overwrite': overwrite}, request.user)
            return render_page(self, request, form, token=token, preview=preview)
    except (ValidationError, IntegrityError) as exc:
        errors = exc.messages if isinstance(exc, ValidationError) else ['Конфликт записи. Ничего не сохранено; повторите предпросмотр.']
        return render_page(self, request, form, errors=errors)
    return render_page(self, request, form)

def batch_view(self, request):
    if not can_edit_samples(request.user) or not self.has_add_permission(request):
        raise PermissionDenied
    form = BatchForm(request.POST or None, request.FILES or None)
    try:
        if request.method == 'POST' and request.POST.get('phase') == 'apply':
            payload = unseal(request.POST.get('token', ''), request.user, 'batch')
            form = BatchForm(payload['common'])
            if not form.is_valid():
                raise ValidationError('Настройки партии больше не действуют. Введите партию заново.')
            count = create_batch(form.cleaned_data, payload['records'], request.user, self, request)
        elif request.method == 'POST' and form.is_valid():
            data = form.cleaned_data
            upload = data.get('excel_file')
            if upload:
                records = parse_excel(upload)
                validate_batch_records(records, data)
                if Sample.objects.filter(series=data['series'], number__in=data['numbers']).exists():
                    raise ValidationError('Некоторые номера партии уже существуют.')
                common = {name: request.POST.get(name, '') for name in form.fields if name != 'excel_file'}
                preview = [{'series': r['series'], 'number': r['number'], 'old': [], 'new': [*r['values'], r['note'] or data['note']], 'changed': True} for r in records]
                token = seal({'mode': 'batch', 'records': records, 'common': common}, request.user)
                return render_page(self, request, form, batch=True, token=token, preview=preview)
            count = create_batch(data, None, request.user, self, request)
        else:
            return render_page(self, request, form, batch=True)
    except (ValidationError, IntegrityError, StoragePlace.DoesNotExist) as exc:
        errors = exc.messages if isinstance(exc, ValidationError) else ['Партия не сохранена: данные изменились или возник конфликт. Повторите ввод.']
        return render_page(self, request, form, batch=True, errors=errors)
    self.message_user(request, f'Создано проб: {count}.', messages.SUCCESS)
    return redirect(reverse('admin:lab_sample_changelist'))

def template_view(self, request):
    if not can_edit_samples(request.user):
        raise PermissionDenied
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Паспортные данные'
    sheet.append(HEADERS)
    sheet.freeze_panes = 'C2'
    for cell in sheet[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='24476A')
    for name, width in zip('ABCDEF', (14, 14, 22, 32, 45, 50)):
        sheet.column_dimensions[name].width = width
        sheet.column_dimensions[name].number_format = '@'
    output = BytesIO()
    workbook.save(output)
    response = HttpResponse(output.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="sample_passports.xlsx"'
    return response

def _strip(fields):
    result = []
    for field in fields:
        if isinstance(field, (tuple, list)):
            nested = _strip(field)
            if nested:
                result.append(tuple(nested))
        elif field not in (*INLINE, 'material_passport', 'passport_excel_tools'):
            result.append(field)
    return result

def install(site):
    ma = site._registry[Sample]
    if getattr(ma, '_stage5b3_installed', False):
        return
    old_fieldsets, old_save, old_urls, old_changelist = (ma.get_fieldsets, ma.save_model, ma.get_urls, ma.changelist_view)
    ma.form = SamplePassportForm
    ma.autocomplete_fields = tuple((x for x in ma.autocomplete_fields if x != 'material_passport'))
    ma.readonly_fields = tuple(ma.readonly_fields) + ('passport_excel_tools',)
    for field, label, index in zip(INLINE, ('Кат № ВИР', 'Вид', 'Происхождение'), range(3)):

        def display(self, obj, index=index):
            return passport_values(obj)[index] if obj is not None else ''
        display.short_description = label
        setattr(ma, field, MethodType(display, ma))

    def tools(self, obj=None):
        return format_html('<a href="{}">Загрузить Excel в существующие пробы</a> | <a href="{}">Шаблон Excel</a>', reverse('admin:lab_sample_passport_excel'), reverse('admin:lab_sample_passport_template'))
    tools.short_description = 'Excel'
    ma.passport_excel_tools = MethodType(tools, ma)

    def fieldsets(self, request, obj=None):
        result = [('Паспорт материала', {'fields': (*INLINE, 'passport_excel_tools')})]
        for title, options in old_fieldsets(request, obj):
            clean = _strip(options.get('fields', ()))
            if clean:
                result.append((title, {**options, 'fields': clean}))
        return result

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        graph_lock()
        values = [form.cleaned_data.get(name, '') for name in INLINE]
        assign_passport(obj, values, request.user)
        return old_save(request, obj, form, change)

    def urls(self):
        replace = {'lab_sample_batch_add', 'lab_sample_passport_excel', 'lab_sample_passport_template'}
        return [path('batch-add/', site.admin_view(self.batch_add_view), name='lab_sample_batch_add'), path('passport-excel/', site.admin_view(self.passport_import_view), name='lab_sample_passport_excel'), path('passport-excel-template/', site.admin_view(self.passport_template_view), name='lab_sample_passport_template')] + [url for url in old_urls() if getattr(url, 'name', None) not in replace]

    def changelist(self, request, extra_context=None):
        response = old_changelist(request, extra_context=extra_context)
        if hasattr(response, 'context_data') and hasattr(response, 'template_name') and (not response.is_rendered):
            previous = response.template_name
            if isinstance(previous, (tuple, list)):
                previous = previous[0]
            response.context_data['passport_previous_template'] = previous
            response.context_data['passport_can_edit'] = can_edit_samples(request.user)
            response.template_name = 'admin/lab/sample/passport_excel_changelist.html'
        return response
    ma.get_fieldsets = MethodType(fieldsets, ma)
    ma.save_model = MethodType(save_model, ma)
    ma.batch_add_view = MethodType(batch_view, ma)
    ma.passport_import_view = MethodType(import_view, ma)
    ma.passport_template_view = MethodType(template_view, ma)
    ma.get_urls = MethodType(urls, ma)
    ma.changelist_view = MethodType(changelist, ma)
    pa = site._registry[MaterialPassport]
    pa.has_add_permission = MethodType(lambda self, request: False, pa)
    pa.has_change_permission = MethodType(lambda self, request, obj=None: False, pa)
    ma._stage5b3_installed = True
