import csv
import io
import json
from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import escape, format_html

from .models import (
    Project, Sample, Task, TaskEvent, WorkLog, Lot,
    Movement, Preparation, Component, Report, Audit,
    lead, warehouse, ProductCategory, StoragePlace
)

admin.site.site_header = "Лаборатория · CRM"
admin.site.site_title = "Лабораторная CRM"
admin.site.index_title = "Проекты, работы и материалы"
admin.site.empty_value_display = "—"


def audit(user, obj, text):
    Audit.objects.create(
        actor=user,
        entity=obj._meta.label,
        object_id=str(obj.pk),
        detail=text,
    )


def safe_cell(value):
    s = "" if value is None else str(value)
    if s.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + s
    return s


@admin.action(description="Выгрузить выбранные записи в CSV")
def export_csv(modeladmin, request, queryset):
    fields = [
        f for f in queryset.model._meta.fields
        if f.name not in ("payload",)
    ]
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow([str(f.verbose_name) for f in fields])
    for obj in queryset.iterator():
        writer.writerow([
            safe_cell(getattr(obj, f.name))
            for f in fields
        ])
    response = HttpResponse(
        "\ufeff" + stream.getvalue(),
        content_type="text/csv; charset=utf-8",
    )
    response["Content-Disposition"] = 'attachment; filename="labcrm.csv"'
    return response


class ReadOnlyAdmin(admin.ModelAdmin):
    actions = [export_csv]
    list_per_page = 50

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_staff

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_staff

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False


class CaptureAdmin(ReadOnlyAdmin):
    capture_actor = True

    def get_form(self, request, obj=None, **kwargs):
        base = super().get_form(request, obj, **kwargs)
        actor = request.user
        capture = self.capture_actor

        class BoundForm(base):
            def clean(self):
                cleaned = super().clean()
                if capture and self.instance._state.adding:
                    self.instance.actor = actor
                return cleaned

        return BoundForm

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        try:
            return super().changeform_view(
                request, object_id, form_url, extra_context
            )
        except ValidationError as exc:
            self.message_user(
                request, " ".join(exc.messages), level=messages.ERROR
            )
            return HttpResponseRedirect(request.path)

    def save_model(self, request, obj, form, change):
        if self.capture_actor and not change:
            obj.actor = request.user
        super().save_model(request, obj, form, change)
        audit(
            request.user, obj,
            "Изменение: " + ", ".join(form.changed_data)
            if change else "Создание записи"
        )


@admin.register(Project)
class ProjectAdmin(CaptureAdmin):
    capture_actor = False
    list_display = ("title", "kind", "manager", "due")
    list_filter = ("kind", "manager")
    search_fields = ("title",)

    def has_add_permission(self, request):
        return lead(request.user)

    def has_change_permission(self, request, obj=None):
        return lead(request.user)


from .sample_batch import SampleBatchMixin


@admin.register(Sample)
class SampleAdmin(SampleBatchMixin, CaptureAdmin):
    capture_actor = False
    change_list_template = "admin/lab/sample/change_list.html"

    list_display = (
        "code", "series", "number",
        "project", "kind", "parent", "location",
    )
    list_filter = ("project", "kind")
    search_fields = (
        "series", "number", "location", "project__title",
    )
    ordering = ("series", "number")


from .task_samples import TaskSamplesMixin


@admin.register(Task)
class TaskAdmin(TaskSamplesMixin, CaptureAdmin):
    capture_actor = False

    list_display = (
        "id", "title", "project", "owner", "reviewer",
        "stage", "status", "due", "overdue",
    )
    list_filter = ("status", "stage", "project", "owner")
    search_fields = (
        "title", "blocker", "next_action",
        "samples__series", "samples__number",
    )
    readonly_fields = (
        "status", "result", "blocker", "next_action",
        "next_actor", "expected", "changed_at", "parent",
    )

    @admin.display(description="Просрочена", boolean=True)
    def overdue(self, obj):
        return (
            obj.status != "Завершено"
            and obj.due < timezone.localdate()
        )

@admin.register(TaskEvent)
class TaskEventAdmin(CaptureAdmin):
    list_display = ("created_at", "task", "action", "actor", "target")
    list_filter = ("action", "actor", "task__project")
    search_fields = ("note", "task__title")
    readonly_fields = ("actor", "created_at")

    def has_add_permission(self, request):
        return request.user.is_active and request.user.is_staff


@admin.register(WorkLog)
class WorkLogAdmin(CaptureAdmin):
    list_display = (
        "id", "actual_day", "actor", "task",
        "operation", "created_at", "correction_of"
    )
    list_filter = ("actual_day", "actor", "task__project")
    search_fields = ("operation", "detail")
    readonly_fields = ("actor", "created_at")
    date_hierarchy = "actual_day"

    def has_add_permission(self, request):
        return request.user.is_active and request.user.is_staff


class StockDictionaryAdmin(CaptureAdmin):
    capture_actor = False
    list_display = ("name", "active", "sort_order")
    list_filter = ("active",)
    search_fields = ("name",)
    fields = ("name", "active", "sort_order")

    def has_add_permission(self, request):
        return bool(request.user.is_active and request.user.is_staff and request.user.is_superuser)

    def has_change_permission(self, request, obj=None):
        return self.has_add_permission(request)


admin.site.register(ProductCategory, StockDictionaryAdmin)
admin.site.register(StoragePlace, StockDictionaryAdmin)


class LotForm(forms.ModelForm):
    class Meta:
        model = Lot
        fields = ("category", "name", "manufacturer", "catalog_number", "unit",
                  "storage_place", "initial_quantity", "expiry", "minimum")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "initial_quantity" in self.fields:
            self.fields["initial_quantity"].required = True
            self.fields["initial_quantity"].initial = Decimal("0")
        for field, cls in (("category", ProductCategory), ("storage_place", StoragePlace)):
            if field in self.fields:
                current_id = getattr(self.instance, field + "_id", None)
                self.fields[field].required = True
                self.fields[field].queryset = cls.objects.filter(Q(active=True) | Q(pk=current_id))
                self.fields[field].empty_label = "Выберите значение"
        if self.instance.pk and "unit" in self.fields:
            if (self.instance.quantity != 0 or self.instance.reserved != 0
                    or Movement.objects.filter(lot_id=self.instance.pk).exists()):
                self.fields["unit"].disabled = True


from .stock_entry import StockEntryMixin


@admin.register(Lot)
class LotAdmin(StockEntryMixin, CaptureAdmin):
    capture_actor = False
    form = LotForm
    list_display = (
        "name", "category", "manufacturer", "catalog_number", "initial_quantity",
        "quantity", "reserved", "free_qty", "unit", "storage_place", "expiry",
        "released", "low_stock",
    )
    list_filter = ("category", "storage_place", "released", "expiry", "unit")
    search_fields = ("code", "name", "manufacturer", "catalog_number",
                     "storage_place__name", "location")
    list_select_related = ("category", "storage_place")
    readonly_fields = ("code", "quantity", "reserved", "free_qty", "released", "location")
    save_as = False

    def has_add_permission(self, request):
        return warehouse(request.user)

    def has_change_permission(self, request, obj=None):
        return warehouse(request.user)

    def get_readonly_fields(self, request, obj=None):
        fields = list(self.readonly_fields)

        if obj is not None:
            fields.extend(("initial_quantity", "category"))

        return tuple(dict.fromkeys(fields))

    def get_fieldsets(self, request, obj=None):
        main_fields = ("name", "manufacturer", "catalog_number")

        category = getattr(request, "stock_entry_category", None)

        if obj is None and category is not None:
            if category.name == "Буфер":
                main_fields = ("name", "manufacturer")

        if obj is not None:
            main_fields = ("category",) + main_fields

        sections = [
            (None, {"fields": main_fields}),
            ("Количество и хранение", {"fields": (
                "initial_quantity", "unit", "storage_place", "expiry", "minimum",
            )}),
        ]
        if obj is not None:
            sections.append(("Остатки (изменяются складскими операциями)", {"fields": (
                "quantity", "reserved", "free_qty", "released", "code",
            )}))
            if not obj.storage_place_id and obj.location:
                sections.append(("Данные до обновления", {"fields": ("location",)}))
        return sections

    def save_model(self, request, obj, form, change):
        if not warehouse(request.user):
            raise PermissionDenied
        with transaction.atomic():
            if change:
                current = Lot.objects.select_for_update().get(pk=obj.pk)
                moved = Movement.objects.filter(lot_id=obj.pk).exists()
                if obj.unit != current.unit and (moved or current.quantity or current.reserved):
                    raise ValidationError("После складских операций единицу учёта менять нельзя.")
                for field in ("quantity", "reserved", "released", "code", "initial_quantity"):
                    setattr(obj, field, getattr(current, field))
            else:
                if obj.initial_quantity is None or obj.initial_quantity < 0:
                    raise ValidationError("Укажите количество не меньше нуля.")
                obj.quantity = Decimal("0")
                obj.reserved = Decimal("0")
            super().save_model(request, obj, form, change)
            if not change and obj.initial_quantity > 0:
                movement = Movement.objects.create(
                    actor=request.user, actual_day=timezone.localdate(),
                    lot=obj, kind="receipt", qty=obj.initial_quantity,
                    note="Начальное поступление при добавлении продукта на склад",
                )
                audit(request.user, movement, "Начальное поступление из карточки продукта")
                obj.refresh_from_db(fields=["quantity", "reserved"])

    @admin.display(description="Свободно")
    def free_qty(self, obj):
        return obj.free

    @admin.display(description="Ниже минимума", boolean=True)
    def low_stock(self, obj):
        return obj.free < obj.minimum

@admin.register(Movement)
class MovementAdmin(CaptureAdmin):
    list_display = (
        "actual_day", "lot", "kind", "qty",
        "task", "preparation", "actor"
    )
    list_filter = ("kind", "actual_day", "actor", "task__project")
    search_fields = ("lot__code", "lot__name", "note")
    readonly_fields = ("actor", "created_at", "preparation")

    def has_add_permission(self, request):
        return warehouse(request.user)


class ComponentInline(admin.TabularInline):
    model = Component
    extra = 1

    fields = (
        "recipe_component",
        "lot",
        "qty",
    )

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_staff

    def can_edit(self, request, obj):
        return bool(
            request.user.is_active
            and request.user.is_staff
            and obj is not None
            and not obj.processed
            and obj.kind == "Приготовление реактива"
            and (
                obj.actor_id == request.user.pk
                or lead(request.user)
            )
        )

    def has_add_permission(self, request, obj=None):
        return self.can_edit(request, obj)

    def has_change_permission(self, request, obj=None):
        return self.can_edit(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self.can_edit(request, obj)

    def get_formset(self, request, obj=None, **kwargs):
        from .models import PreparationProtocolComponent
        from django.db.models import F

        recipe_id = obj.recipe_id if obj is not None else None

        class ComponentForm(forms.ModelForm):
            class Meta:
                model = Component
                fields = (
                    "recipe_component",
                    "lot",
                    "qty",
                )

            def __init__(self, *args, **form_kwargs):
                super().__init__(*args, **form_kwargs)
                editable_fields = {
                    "recipe_component",
                    "lot",
                    "qty",
                }

                if not editable_fields.issubset(self.fields):
                    return

                recipe_components = (
                    PreparationProtocolComponent.objects
                    .filter(protocol_id=recipe_id)
                    .order_by("position", "pk")
                )

                self.fields["recipe_component"].queryset = (
                    recipe_components
                )
                self.fields["recipe_component"].required = True

                lots = Lot.objects.filter(
                    allowed_recipe_components__protocol_id=recipe_id,
                    released=True,
                    quantity__gt=F("reserved"),
                ).filter(
                    Q(expiry__isnull=True)
                    | Q(expiry__gte=timezone.localdate())
                ).distinct()

                selected_id = self.instance.recipe_component_id

                if self.is_bound:
                    raw_id = self.data.get(
                        self.add_prefix("recipe_component")
                    )
                    selected_id = (
                        int(raw_id)
                        if str(raw_id or "").isdigit()
                        else None
                    )

                if selected_id:
                    lots = lots.filter(
                        allowed_recipe_components__pk=selected_id
                    )

                self.fields["lot"].queryset = lots.order_by(
                    "name", "expiry", "pk"
                )

                self.fields["lot"].label_from_instance = (
                    lambda lot: (
                        f"{lot.name} | {lot.code} | "
                        f"доступно: {lot.free} {lot.unit} | "
                        f"годен до: {lot.expiry or 'не указан'}"
                    )
                )

                self.fields["qty"].help_text = (
                    "Фактический расход в единице выбранной "
                    "складской партии."
                )

        kwargs["form"] = ComponentForm

        return super().get_formset(request, obj, **kwargs)

@admin.action(description="Допустить выбранные готовые партии")
def approve_preparations(modeladmin, request, queryset):
    if not lead(request.user):
        raise PermissionDenied
    ids = list(queryset.values_list("pk", flat=True)[:2])
    if len(ids) != 1:
        modeladmin.message_user(
            request, "Для допуска выберите ровно одно приготовление.",
            messages.WARNING,
        )
        return
    preparation = Preparation.objects.get(pk=ids[0])
    try:
        preparation.approve(request.user)
    except ValidationError as exc:
        modeladmin.message_user(
            request, "Допуск запрещён: " + " ".join(exc.messages),
            messages.ERROR,
        )
        return
    modeladmin.message_user(
        request, f"Партия {preparation.output.code} допущена к использованию.",
        messages.SUCCESS,
    )

@admin.action(
    description="Провести приготовление и оприходовать результат",
    permissions=["change"],
)
def complete_preparation(modeladmin, request, queryset):
    selected_ids = list(queryset.values_list("pk", flat=True)[:2])

    if len(selected_ids) != 1:
        modeladmin.message_user(
            request,
            "Для проведения выберите ровно одно приготовление.",
            level=messages.WARNING,
        )
        return

    try:
        with transaction.atomic():
            preparation = (
                Preparation.objects
                .select_for_update()
                .get(pk=selected_ids[0])
            )

            if not modeladmin.has_change_permission(
                request, preparation
            ):
                modeladmin.message_user(
                    request,
                    "Нет права на проведение этой записи, "
                    "либо она уже проведена.",
                    level=messages.ERROR,
                )
                return

            preparation.complete()
            preparation.refresh_from_db()

            audit(
                request.user,
                preparation,
                "Проведение приготовления: списание компонентов "
                "и приход готовой партии",
            )

            output_code = preparation.output.code

    except ValidationError as exc:
        modeladmin.message_user(
            request,
            "Приготовление не проведено: "
            + " ".join(exc.messages),
            level=messages.ERROR,
        )
        return

    modeladmin.message_user(
        request,
        f"Приготовление проведено. "
        f"Готовая партия: {output_code}. "
        "Партия ожидает допуска к использованию.",
        level=messages.SUCCESS,
    )

@admin.register(Preparation)
class PreparationAdmin(CaptureAdmin):
    list_display = (
        "actual_day",
        "recipe",
        "output",
        "actual_volume_ml",
        "actor",
        "processed",
        "approved_by",
    )

    list_filter = (
        "processed",
        "actual_day",
        "actor",
        "kind",
    )

    search_fields = (
        "output__code",
        "output__name",
        "recipe__title",
        "recipe__product__name",
        "protocol",
        "note",
    )

    list_select_related = (
        "recipe",
        "recipe__product",
        "output",
        "actor",
    )

    readonly_fields = (
        'kind',
        'protocol',
        'output',
        'qty',
        'actor',
        'created_at',
        'processed',
        'approved_by',
        'approved_at',
        'autoclaving_required',
        'approved_cycle',
        'recipe_calculation',
    )

    fields = (
        'actual_day',
        'recipe',
        'planned_volume_ml',
        'recipe_calculation',
        'actual_volume_ml',
        'output_category',
        'output_storage_place',
        'output_expiry',
        'note',
        'kind',
        'protocol',
        'output',
        'qty',
        'actor',
        'created_at',
        'processed',
        'approved_by',
        'approved_at',
        'autoclaving_required',
        'approved_cycle',
    )

    inlines = (ComponentInline,)
    actions = (
        complete_preparation,
        approve_preparations,
        export_csv,
    )

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not lead(request.user):
            actions.pop("approve_preparations", None)
        return actions

    def has_add_permission(self, request):
        return request.user.is_active and request.user.is_staff

    def has_change_permission(self, request, obj=None):
        if not request.user.is_active or not request.user.is_staff:
            return False

        if obj is None:
            return True

        if obj.processed:
            return False

        if obj.kind != "Приготовление реактива":
            return False

        return (
            obj.actor_id == request.user.pk
            or lead(request.user)
        )

    def has_delete_permission(self, request, obj=None):
        return False

    def get_form(self, request, obj=None, **kwargs):
        base = super().get_form(request, obj, **kwargs)

        class PreparationForm(base):
            def __init__(self, *args, **form_kwargs):
                super().__init__(*args, **form_kwargs)

                required_fields = (
                    "recipe",
                    "planned_volume_ml",
                    "actual_volume_ml",
                    "output_category",
                    "output_storage_place",
                )

                for name in required_fields:
                    if name in self.fields:
                        self.fields[name].required = True

        return PreparationForm

    def get_inline_instances(self, request, obj=None):
        if obj is None:
            return []

        return super().get_inline_instances(request, obj)

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))

        if obj is not None and obj.components.exists():
            fields.append("recipe")

        return tuple(dict.fromkeys(fields))

    def save_model(self, request, obj, form, change):
        if change:
            current = (
                Preparation.objects
                .select_for_update()
                .get(pk=obj.pk)
            )
            if not self.has_change_permission(request, current):
                raise PermissionDenied

        obj.kind = "Приготовление реактива"

        if not obj.recipe_id or obj.actual_volume_ml is None:
            raise ValidationError(
                "Укажите протокол и фактический объём."
            )

        obj.protocol = str(obj.recipe)[:240]
        obj.qty = obj.actual_volume_ml

        super().save_model(request, obj, form, change)

    @admin.display(description="Расчёт состава: план / факт")
    def recipe_calculation(self, obj):
        from .models import convert_preparation_quantity
        from django.utils.html import format_html, format_html_join

        if obj is None or not obj.pk or not obj.recipe_id:
            return "Выберите протокол и сохраните приготовление."
        if obj.planned_volume_ml is None:
            return "Укажите планируемый объём и сохраните запись."

        def number(value):
            text = format(value, ".8f")
            short = text.rstrip("0").rstrip(".")
            if Decimal(text) != value:
                return "≈ " + short
            return short

        actual = {}
        errors = {}
        unlinked = 0
        for entry in obj.components.select_related("lot", "recipe_component"):
            if not entry.recipe_component_id:
                unlinked += 1
                continue
            key = entry.recipe_component_id
            try:
                value = convert_preparation_quantity(
                    entry.qty, entry.lot.unit, entry.recipe_component.unit
                )
                actual[key] = actual.get(key, Decimal("0")) + value
            except ValidationError as exc:
                errors[key] = " ".join(exc.messages)

        rows = []
        for component in obj.recipe.recipe_components.order_by("position", "pk"):
            unit = component.get_unit_display()
            try:
                required = component.required_quantity(obj.planned_volume_ml)
                plan = (
                    "Довести до конечного объёма "
                    + number(obj.planned_volume_ml) + " мл"
                    if required is None else number(required) + " " + unit
                )
            except ValidationError as exc:
                required = None
                plan = "Ошибка рецептуры: " + " ".join(exc.messages)

            if component.pk in errors:
                fact = "Ошибка единиц: " + errors[component.pk]
                difference = "Не рассчитано"
            elif component.pk not in actual:
                fact = "Не указан"
                difference = "—"
            else:
                value = actual[component.pk]
                fact = number(value) + " " + unit
                if required is None:
                    difference = "—"
                else:
                    delta = value - required
                    difference = (
                        "Совпадает" if delta == 0
                        else ("+" if delta > 0 else "")
                        + number(delta) + " " + unit
                    )

            rows.append((
                component.reagent_name,
                component.source_concentration or "—",
                component.final_concentration or "—",
                plan, fact, difference,
            ))

        if not rows:
            return "В протоколе нет компонентов."

        body = format_html_join(
            "", "<tr><td>{}</td><td>{}</td><td>{}</td>"
            "<td>{}</td><td>{}</td><td>{}</td></tr>", rows
        )
        extra = (
            f" Строк расхода без компонента рецептуры: {unlinked}."
            if unlinked else ""
        )
        return format_html(
            '<div style="overflow-x:auto">'
            '<p>План на сохранённый объём: {} мл. '
            'Фактический выход: {} мл.</p>'
            '<table><thead><tr>'
            '<th>Компонент</th><th>Исходная концентрация</th>'
            '<th>Конечная концентрация</th><th>Плановый расход</th>'
            '<th>Фактический расход</th><th>Факт − план</th>'
            '</tr></thead><tbody>{}</tbody></table>'
            '<p>Концентрации показаны из протокола, не вычисляются. '
            'Отклонение информационное и само по себе не блокирует проведение. '
            '≈ означает округление только при отображении.{}</p>'
            '<p>После изменения объёма сохраните запись для пересчёта.</p>'
            '<h3>Порядок приготовления</h3>'
            '<div style="white-space:pre-wrap">{}</div></div>',
            number(obj.planned_volume_ml),
            number(obj.actual_volume_ml) if obj.actual_volume_ml is not None else "—",
            body, extra, obj.recipe.instructions,
        )



@admin.action(description="Утвердить выбранные отчёты")
def approve_reports(modeladmin, request, queryset):
    if not lead(request.user):
        raise PermissionDenied
    for pk in queryset.values_list("pk", flat=True):
        with transaction.atomic():
            report = Report.objects.select_for_update().get(pk=pk)
            if report.approved_at:
                continue
            report.approved_by = request.user
            report.approved_at = timezone.now()
            report.save(update_fields=["approved_by", "approved_at"])
            audit(request.user, report, "Утверждение квартального отчёта")


@admin.action(description="Выгрузить выбранные отчёты в JSON")
def export_reports(modeladmin, request, queryset):
    if not lead(request.user):
        raise PermissionDenied
    content = [
        {
            "year": r.year,
            "quarter": r.quarter,
            "created_at": r.created_at,
            "approved_at": r.approved_at,
            "payload": r.payload,
        }
        for r in queryset
    ]
    response = HttpResponse(
        json.dumps(
            content, cls=DjangoJSONEncoder,
            ensure_ascii=False, indent=2
        ),
        content_type="application/json; charset=utf-8",
    )
    response["Content-Disposition"] = 'attachment; filename="quarter-reports.json"'
    return response


@admin.register(Report)
class ReportAdmin(ReadOnlyAdmin):
    list_display = (
        "year", "quarter", "created_at",
        "approved_by", "approved_at", "sent_at", "open_report"
    )
    actions = [approve_reports, export_reports]
    list_filter = ("year", "quarter", "approved_at")

    def has_view_permission(self, request, obj=None):
        return lead(request.user)

    def get_urls(self):
        return [
            path(
                "<int:pk>/read/",
                self.admin_site.admin_view(self.read_report),
                name="lab_report_read",
            )
        ] + super().get_urls()

    @admin.display(description="Представление")
    def open_report(self, obj):
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">Читать / печать</a>',
            reverse("admin:lab_report_read", args=[obj.pk]),
        )

    def read_report(self, request, pk):
        if not lead(request.user):
            raise PermissionDenied
        report = self.get_object(request, str(pk))
        if report is None:
            from django.http import Http404
            raise Http404

        parts = [
            '<!doctype html><html lang="ru"><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width,initial-scale=1">',
            '<title>Квартальный отчёт</title>',
            '<style>'
            'body{font:16px/1.5 sans-serif;margin:24px;color:#202620}'
            'h1{font-size:28px}h2{font-size:20px;margin-top:36px}'
            'table{border-collapse:collapse;width:100%;font-size:13px}'
            'th,td{border:1px solid #ccc;padding:8px;text-align:left;'
            'vertical-align:top;overflow-wrap:anywhere}'
            'th{background:#eef2ee}.wrap{overflow-x:auto}'
            '@media print{body{margin:8mm}.wrap{overflow:visible}'
            'thead{display:table-header-group}tr{break-inside:avoid}}'
            '</style><body>',
            f'<h1>Отчёт: {escape(str(report))}</h1>',
            '<p>События относятся к отчётному периоду. '
            'Снимки задач и склада отражают момент формирования, '
            'а не ретроспективное состояние на конец квартала.</p>',
            '<p>Для сохранения в PDF используйте печать браузера.</p>',
        ]

        for heading, rows in report.payload.items():
            parts.append(f"<h2>{escape(heading)}</h2>")
            if not isinstance(rows, list):
                parts.append(f"<p>{escape(str(rows))}</p>")
                continue
            if not rows:
                parts.append("<p>Записей за период нет.</p>")
                continue

            parts.append('<div class="wrap"><table><thead><tr>')
            for key in rows[0]:
                parts.append(f"<th>{escape(str(key))}</th>")
            parts.append("</tr></thead><tbody>")

            for row in rows:
                parts.append("<tr>")
                for value in row.values():
                    text = "—" if value is None else str(value)
                    parts.append(f"<td>{escape(text)}</td>")
                parts.append("</tr>")

            parts.append("</tbody></table></div>")

        parts.append("</body></html>")
        return HttpResponse("".join(parts), content_type="text/html; charset=utf-8")


@admin.register(Audit)
class AuditAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "actor", "entity", "object_id", "detail")
    list_filter = ("actor", "entity")
    search_fields = ("detail", "object_id")

    def has_view_permission(self, request, obj=None):
        return lead(request.user)

from .models import BufferName


class BufferNameAdmin(StockDictionaryAdmin):
    list_display = (
        "name",
        "kind",
        "concentration",
        "requires_autoclaving",
        "shelf_life_days",
        "active",
    )

    list_filter = (
        "kind",
        "requires_autoclaving",
        "active",
    )

    search_fields = (
        "name",
        "concentration",
    )

    fields = (
        "name",
        "kind",
        "concentration",
        "storage_conditions",
        "shelf_life_days",
        "requires_autoclaving",
        "active",
        "sort_order",
    )


if not admin.site.is_registered(BufferName):
    admin.site.register(BufferName, BufferNameAdmin)


from .models import (
    PreparationProtocol,
    PreparationProtocolComponent,
)


class PreparationProtocolComponentInline(admin.StackedInline):
    model = PreparationProtocolComponent
    extra = 1

    fields = (
        "position",
        "reagent_name",
        "source_concentration",
        "quantity",
        "unit",
        "to_final_volume",
        "final_concentration",
        "allowed_lots",
        "note",
    )

    filter_horizontal = ("allowed_lots",)

    def has_add_permission(self, request, obj=None):
        if not lead(request.user):
            return False

        return obj is None or not obj.preparations.exists()

    def has_change_permission(self, request, obj=None):
        if not lead(request.user):
            return False

        return obj is None or not obj.preparations.exists()

    def has_delete_permission(self, request, obj=None):
        if not lead(request.user):
            return False

        return obj is None or not obj.preparations.exists()


@admin.register(PreparationProtocol)
class PreparationProtocolAdmin(CaptureAdmin):
    capture_actor = False

    list_display = (
        "title",
        "product",
        "version",
        "base_volume_ml",
        "active",
        "created_at",
    )

    list_filter = (
        "active",
        "product",
    )

    search_fields = (
        "title",
        "version",
        "product__name",
        "product__concentration",
    )

    list_select_related = ("product",)

    readonly_fields = ("created_at",)

    fields = (
        "product",
        "title",
        "version",
        "base_volume_ml",
        "instructions",
        "target_ph",
        "processing_notes",
        "active",
        "created_at",
    )

    inlines = (PreparationProtocolComponentInline,)

    def has_add_permission(self, request):
        return lead(request.user)

    def has_change_permission(self, request, obj=None):
        if not lead(request.user):
            return False

        return obj is None or not obj.preparations.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not lead(request.user):
            raise PermissionDenied

        if change:
            with transaction.atomic():
                current = (
                    PreparationProtocol.objects
                    .select_for_update()
                    .get(pk=obj.pk)
                )

                if current.preparations.exists():
                    raise ValidationError(
                        "Протокол уже используется в приготовлениях. "
                        "Для изменения состава создайте новую версию."
                    )

                super().save_model(
                    request, obj, form, change
                )
        else:
            super().save_model(
                request, obj, form, change
            )

from .models import Autoclaving, AutoclavingItem


class AutoclavingItemInline(admin.TabularInline):
    model = AutoclavingItem
    extra = 1
    fields = ("lot", "description", "quantity", "unit", "note")

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_staff

    def can_edit(self, request, obj):
        if not request.user.is_active or not request.user.is_staff:
            return False
        if obj is None:
            return True
        return (
            obj.status == Autoclaving.Status.DRAFT
            and (obj.actor_id == request.user.pk or lead(request.user))
        )

    def has_add_permission(self, request, obj=None):
        return self.can_edit(request, obj)

    def has_change_permission(self, request, obj=None):
        return self.can_edit(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self.can_edit(request, obj)


def finalize_autoclaving(modeladmin, request, queryset, target_status):
    ids = list(queryset.values_list("pk", flat=True)[:2])
    if len(ids) != 1:
        modeladmin.message_user(
            request, "Выберите ровно один цикл.", messages.WARNING
        )
        return

    try:
        with transaction.atomic():
            cycle = Autoclaving.objects.select_for_update().get(pk=ids[0])
            if not modeladmin.has_change_permission(request, cycle):
                raise ValidationError(
                    "Нет права завершить цикл либо он уже закрыт."
                )
            cycle.full_clean()
            items = list(cycle.items.all())
            if not items:
                raise ValidationError("Укажите состав загрузки.")
            for item in items:
                item.full_clean()
            if not cycle.control_result.strip():
                raise ValidationError("Запишите результат контроля цикла.")
            if target_status == Autoclaving.Status.COMPLETED:
                if (
                    cycle.temperature_c is None
                    or cycle.holding_time_min is None
                ):
                    raise ValidationError(
                        "Укажите температуру и время выдержки."
                    )
            elif not cycle.note.strip():
                raise ValidationError(
                    "В замечаниях опишите причину неуспешного цикла."
                )
            cycle.status = target_status
            cycle.save(update_fields=["status"])
            audit(
                request.user, cycle,
                "Закрытие цикла: " + cycle.get_status_display(),
            )
    except ValidationError as exc:
        modeladmin.message_user(
            request, " ".join(exc.messages), messages.ERROR
        )
        return

    modeladmin.message_user(
        request,
        "Цикл закрыт. Остатки и допуск складских партий не изменены.",
        messages.SUCCESS,
    )


@admin.action(description="Закрыть цикл: завершён", permissions=["change"])
def complete_autoclaving(modeladmin, request, queryset):
    finalize_autoclaving(
        modeladmin, request, queryset, Autoclaving.Status.COMPLETED
    )


@admin.action(description="Закрыть цикл: неуспешен", permissions=["change"])
def fail_autoclaving(modeladmin, request, queryset):
    finalize_autoclaving(
        modeladmin, request, queryset, Autoclaving.Status.FAILED
    )


@admin.register(Autoclaving)
class AutoclavingAdmin(CaptureAdmin):
    list_display = (
        "actual_day", "equipment", "cycle_number", "protocol",
        "temperature_c", "holding_time_min", "status", "actor",
    )
    list_filter = ("status", "actual_day", "equipment", "actor")
    search_fields = (
        "cycle_number", "equipment", "protocol", "control_result", "note",
    )
    readonly_fields = ("status", "actor", "created_at")
    fields = (
        "actual_day", "equipment", "cycle_number", "protocol",
        "temperature_c", "holding_time_min", "control_result", "note",
        "status", "actor", "created_at",
    )
    inlines = (AutoclavingItemInline,)
    actions = (complete_autoclaving, fail_autoclaving, export_csv)

    def has_add_permission(self, request):
        return request.user.is_active and request.user.is_staff

    def has_change_permission(self, request, obj=None):
        if not request.user.is_active or not request.user.is_staff:
            return False
        if obj is None:
            return True
        return (
            obj.status == Autoclaving.Status.DRAFT
            and (obj.actor_id == request.user.pk or lead(request.user))
        )

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if change:
            current = (
                Autoclaving.objects.select_for_update().get(pk=obj.pk)
            )
            if not self.has_change_permission(request, current):
                raise PermissionDenied
            obj.status = current.status
        else:
            obj.status = Autoclaving.Status.DRAFT
        super().save_model(request, obj, form, change)

# LABCRM_UI_STAGE1: presentation only; no permissions or stock changes.
from .admin_navigation import configure_admin as _configure_lab_admin
_configure_lab_admin(admin.site)
