import csv
import io
import json
from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from .models import (
    Audit,
    BufferName,
    Component,
    Lot,
    Movement,
    Preparation,
    ProductCategory,
    Project,
    Report,
    Sample,
    StoragePlace,
    Task,
    TaskEvent,
    WorkLog,
    lead,
    warehouse,
)
from .sample_batch import SampleBatchMixin
from .stock_entry import StockEntryMixin
from .task_samples import TaskSamplesMixin

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
        f
        for f in queryset.model._meta.fields
        if f.name not in ("payload",)
    ]
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow([str(f.verbose_name) for f in fields])
    for obj in queryset.iterator():
        writer.writerow([safe_cell(getattr(obj, f.name)) for f in fields])
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
        return bool(request.user.is_active and request.user.is_staff)

    def has_view_permission(self, request, obj=None):
        return bool(request.user.is_active and request.user.is_staff)

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False


class CaptureAdmin(ReadOnlyAdmin):
    capture_actor = True
    audit_in_save_model = True

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

    def changeform_view(
        self, request, object_id=None, form_url="", extra_context=None
    ):
        try:
            with transaction.atomic():
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
        if self.audit_in_save_model:
            audit(
                request.user,
                obj,
                "Изменение: " + ", ".join(form.changed_data)
                if change
                else "Создание записи",
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


@admin.register(Sample)
class SampleAdmin(SampleBatchMixin, CaptureAdmin):
    capture_actor = False
    change_list_template = "admin/lab/sample/change_list.html"

    list_display = (
        "series_and_number_col",
        "series",
        "number",
        "project",
        "kind",
        "parent",
        "storage_place",
        "location",
    )
    list_filter = ("project", "kind", "storage_place")
    search_fields = (
        "series",
        "number",
        "location",
        "storage_place__name",
        "project__title",
    )
    readonly_fields = ("series_and_number_col",)
    ordering = ("series", "number")

    fieldsets = (
        (
            "Идентификация образца/пробы",
            {
                "fields": (
                    "series_and_number_col",
                    "series",
                    "number",
                    "project",
                    "kind",
                )
            },
        ),
        (
            "Исходный образец/проба (укажите серию и номер либо выберите из списка)",
            {
                "fields": (
                    "parent_series",
                    "parent_number",
                    "parent",
                )
            },
        ),
        (
            "Хранение и примечание",
            {
                "fields": (
                    "storage_place",
                    "location",
                    "note",
                )
            },
        ),
    )

    @admin.display(description="Серия и номер", ordering="series")
    def series_and_number_col(self, obj):
        if not obj or not obj.series or not obj.number:
            return "Формируется автоматически из полей «Серия» и «Номер»"
        return obj.code


class TaskMovementInlineForm(forms.ModelForm):
    class Meta:
        model = Movement
        fields = ("lot", "kind", "qty", "note")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "kind" in self.fields:
            self.fields["kind"].choices = [
                ("reserve", "Резервирование под задачу"),
                ("use_free", "Расход свободного остатка по задаче"),
                ("use_reserved", "Расход из резерва задачи"),
                ("release", "Снятие резерва"),
            ]
            self.fields["kind"].initial = "use_free"
        if "note" in self.fields:
            self.fields["note"].required = False
            self.fields["note"].widget = forms.TextInput(
                attrs={"placeholder": "Основание / комментарий к расходу"}
            )


class TaskMovementInline(admin.TabularInline):
    model = Movement
    form = TaskMovementInlineForm
    extra = 1
    verbose_name = "Расход / резерв реактива по задаче"
    verbose_name_plural = "Расход и резерв пластика, реактивов и буферов по задаче"
    readonly_fields = ("actual_day", "actor", "created_at")

    def has_add_permission(self, request, obj=None):
        return bool(request.user.is_active and request.user.is_staff)

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Task)
class TaskAdmin(TaskSamplesMixin, CaptureAdmin):
    capture_actor = False
    inlines = [TaskMovementInline]

    list_display = (
        "id",
        "title",
        "project",
        "owner",
        "reviewer",
        "stage",
        "status",
        "due",
        "overdue",
    )
    list_filter = ("status", "stage", "project", "owner")
    search_fields = (
        "title",
        "blocker",
        "next_action",
        "samples__series",
        "samples__number",
    )
    readonly_fields = (
        "status",
        "result",
        "blocker",
        "next_action",
        "next_actor",
        "expected",
        "changed_at",
        "parent",
    )

    def save_formset(self, request, form, formset, change):
        if formset.model is Movement:
            instances = formset.save(commit=False)
            for inst in instances:
                inst.actor = request.user
                inst.actual_day = timezone.localdate()
                inst.task = form.instance
                if not inst.note.strip():
                    inst.note = f"Расход по задаче #{form.instance.pk}: {form.instance.title}"
                inst.save()
                audit(request.user, inst, "Складская операция из карточки задачи")
            formset.save_m2m()
        else:
            super().save_formset(request, form, formset, change)

    @admin.display(description="Просрочена", boolean=True)
    def overdue(self, obj):
        return obj.status != "Завершено" and obj.due < timezone.localdate()


@admin.register(TaskEvent)
class TaskEventAdmin(CaptureAdmin):
    list_display = ("created_at", "task", "action", "actor", "target")
    list_filter = ("action", "actor", "task__project")
    search_fields = ("note", "task__title")
    readonly_fields = ("actor", "created_at")

    def has_add_permission(self, request):
        return bool(request.user.is_active and request.user.is_staff)


class WorkLogMovementInlineForm(forms.ModelForm):
    class Meta:
        model = Movement
        fields = ("lot", "kind", "qty", "note")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "kind" in self.fields:
            self.fields["kind"].choices = [
                ("use_free", "Расход со склада (свободный остаток)"),
                ("use_reserved", "Расход из резерва задачи"),
            ]
            self.fields["kind"].initial = "use_free"
        if "note" in self.fields:
            self.fields["note"].required = False
            self.fields["note"].widget = forms.TextInput(
                attrs={"placeholder": "Комментарий (необязательно)"}
            )


class WorkLogMovementInline(admin.TabularInline):
    model = Movement
    fk_name = "worklog"
    form = WorkLogMovementInlineForm
    extra = 1
    verbose_name = "Списание материала за день"
    verbose_name_plural = (
        "Списание пластика, реактивов, расходников и буферов в журнал работ"
    )

    def has_add_permission(self, request, obj=None):
        return bool(request.user.is_active and request.user.is_staff)

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WorkLog)
class WorkLogAdmin(CaptureAdmin):
    list_display = (
        "id",
        "actual_day",
        "actor",
        "task",
        "operation",
        "created_at",
        "correction_of",
    )
    list_filter = ("actual_day", "actor", "task__project")
    search_fields = ("operation", "detail")
    readonly_fields = ("actor", "created_at")
    date_hierarchy = "actual_day"
    inlines = [WorkLogMovementInline]

    def has_add_permission(self, request):
        return bool(request.user.is_active and request.user.is_staff)

    def get_inline_instances(self, request, obj=None):
        if obj is not None:
            return []
        return super().get_inline_instances(request, obj)

    def save_formset(self, request, form, formset, change):
        if formset.model is Movement:
            instances = formset.save(commit=False)
            worklog = form.instance
            for inst in instances:
                inst.actor = request.user
                inst.actual_day = worklog.actual_day
                inst.worklog = worklog
                if worklog.task_id:
                    inst.task_id = worklog.task_id
                if not inst.note.strip():
                    inst.note = f"Журнал работ ({worklog.actual_day}): {worklog.operation}"
                inst.save()
                audit(request.user, inst, "Списание материала из журнала работ по дням")
            formset.save_m2m()
        else:
            super().save_formset(request, form, formset, change)


class StockDictionaryAdmin(CaptureAdmin):
    capture_actor = False
    list_display = ("name", "active", "sort_order")
    list_filter = ("active",)
    search_fields = ("name",)
    fields = ("name", "active", "sort_order")

    def has_add_permission(self, request):
        return warehouse(request.user)

    def has_change_permission(self, request, obj=None):
        return warehouse(request.user)


@admin.register(BufferName)
class BufferNameAdmin(StockDictionaryAdmin):
    """Справочник названий буферов с возможностью добавления и удаления позиций администратором."""

    def has_delete_permission(self, request, obj=None):
        return warehouse(request.user)


admin.site.register(ProductCategory, StockDictionaryAdmin)


@admin.register(StoragePlace)
class StoragePlaceAdmin(StockDictionaryAdmin):
    change_list_template = "admin/lab/storageplace/change_list.html"
    samples_stats_template = "admin/lab/storageplace/samples_stats.html"
    list_display = ("name", "active", "sort_order", "samples_stats_link")

    def get_urls(self):
        return [
            path(
                "samples-stats/",
                self.admin_site.admin_view(self.samples_stats_view),
                name="lab_storageplace_all_samples_stats",
            ),
            path(
                "<int:pk>/samples-stats/",
                self.admin_site.admin_view(self.samples_stats_view),
                name="lab_storageplace_samples_stats",
            ),
        ] + super().get_urls()

    @admin.display(description="Статистика образцов/проб")
    def samples_stats_link(self, obj):
        count = Sample.objects.filter(
            Q(storage_place_id=obj.pk) | Q(location=obj.name)
        ).count()
        url = reverse("admin:lab_storageplace_samples_stats", args=[obj.pk])
        return format_html(
            '<a href="{}">Статистика проб ({}) →</a>',
            url,
            count,
        )

    def samples_stats_view(self, request, pk=None):
        if not self.has_view_permission(request):
            raise PermissionDenied

        selected_place = None
        samples_qs = Sample.objects.select_related(
            "project", "parent", "storage_place"
        ).order_by("series", "number")

        if pk is not None:
            selected_place = StoragePlace.objects.filter(pk=pk).first()
            if selected_place is None:
                raise Http404
            samples_qs = samples_qs.filter(
                Q(storage_place_id=selected_place.pk)
                | Q(location=selected_place.name)
            )

        total_samples = samples_qs.count()
        by_kind = list(
            samples_qs.values("kind")
            .annotate(count=Count("id"))
            .order_by("-count", "kind")
        )
        by_project = list(
            samples_qs.values("project__title")
            .annotate(count=Count("id"))
            .order_by("-count", "project__title")
        )
        by_series = list(
            samples_qs.values("series")
            .annotate(count=Count("id"))
            .order_by("-count", "series")
        )

        places = StoragePlace.objects.order_by("sort_order", "name")
        by_place = [
            {
                "pk": p.pk,
                "name": p.name,
                "count": Sample.objects.filter(
                    Q(storage_place_id=p.pk) | Q(location=p.name)
                ).count(),
            }
            for p in places
        ]

        context = {
            **self.admin_site.each_context(request),
            "title": (
                f"Статистика образцов/проб: {selected_place.name}"
                if selected_place
                else "Статистика по загруженным образцам/пробам в местах хранения"
            ),
            "opts": self.model._meta,
            "selected_place": selected_place,
            "total_samples": total_samples,
            "by_place": by_place,
            "by_kind": by_kind,
            "by_project": by_project,
            "by_series": by_series,
            "samples": list(samples_qs[:200]),
        }
        return TemplateResponse(request, self.samples_stats_template, context)


class LotForm(forms.ModelForm):
    class Meta:
        model = Lot
        fields = (
            "category",
            "name",
            "manufacturer",
            "catalog_number",
            "unit",
            "storage_place",
            "initial_quantity",
            "expiry",
            "minimum",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "initial_quantity" in self.fields:
            self.fields["initial_quantity"].required = True
            self.fields["initial_quantity"].initial = Decimal("0")
        for field, cls in (
            ("category", ProductCategory),
            ("storage_place", StoragePlace),
        ):
            if field in self.fields:
                current_id = getattr(self.instance, field + "_id", None)
                self.fields[field].required = True
                self.fields[field].queryset = cls.objects.filter(
                    Q(active=True) | Q(pk=current_id)
                )
                self.fields[field].empty_label = "Выберите значение"
        if self.instance.pk and "unit" in self.fields:
            if (
                self.instance.quantity != 0
                or self.instance.reserved != 0
                or Movement.objects.filter(lot_id=self.instance.pk).exists()
            ):
                self.fields["unit"].disabled = True


@admin.register(Lot)
class LotAdmin(StockEntryMixin, CaptureAdmin):
    capture_actor = False
    form = LotForm
    list_display = (
        "name",
        "category",
        "manufacturer",
        "catalog_number",
        "initial_quantity",
        "quantity",
        "reserved",
        "free_qty",
        "unit",
        "storage_place",
        "expiry",
        "released",
        "low_stock",
    )
    list_filter = ("category", "storage_place", "released", "expiry", "unit")
    search_fields = (
        "code",
        "name",
        "manufacturer",
        "catalog_number",
        "storage_place__name",
        "location",
    )
    list_select_related = ("category", "storage_place")
    readonly_fields = (
        "code",
        "quantity",
        "reserved",
        "free_qty",
        "released",
        "location",
    )
    save_as = False

    def has_add_permission(self, request):
        return warehouse(request.user)

    def has_change_permission(self, request, obj=None):
        return warehouse(request.user)

    def get_readonly_fields(self, request, obj=None):
        fields = list(self.readonly_fields)

        if obj is not None:
            fields.append("initial_quantity")

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
            (
                "Количество и хранение",
                {
                    "fields": (
                        "initial_quantity",
                        "unit",
                        "storage_place",
                        "expiry",
                        "minimum",
                    )
                },
            ),
        ]
        if obj is not None:
            sections.append(
                (
                    "Остатки (изменяются складскими операциями)",
                    {
                        "fields": (
                            "quantity",
                            "reserved",
                            "free_qty",
                            "released",
                            "code",
                        )
                    },
                )
            )
            if not obj.storage_place_id and obj.location:
                sections.append(
                    ("Данные до обновления", {"fields": ("location",)})
                )
        return sections

    def save_model(self, request, obj, form, change):
        if not warehouse(request.user):
            raise PermissionDenied
        with transaction.atomic():
            if change:
                current = Lot.objects.select_for_update().get(pk=obj.pk)
                moved = Movement.objects.filter(lot_id=obj.pk).exists()
                if obj.unit != current.unit and (
                    moved or current.quantity or current.reserved
                ):
                    raise ValidationError(
                        "После складских операций единицу учёта менять нельзя."
                    )
                for field in (
                    "quantity",
                    "reserved",
                    "released",
                    "code",
                    "initial_quantity",
                ):
                    setattr(obj, field, getattr(current, field))
            else:
                if obj.initial_quantity is None or obj.initial_quantity < 0:
                    raise ValidationError(
                        "Укажите количество не меньше нуля."
                    )
                obj.quantity = Decimal("0")
                obj.reserved = Decimal("0")
            super().save_model(request, obj, form, change)
            if not change and obj.initial_quantity > 0:
                movement = Movement.objects.create(
                    actor=request.user,
                    actual_day=timezone.localdate(),
                    lot=obj,
                    kind="receipt",
                    qty=obj.initial_quantity,
                    note="Начальное поступление при добавлении продукта на склад",
                )
                audit(
                    request.user,
                    movement,
                    "Начальное поступление из карточки продукта",
                )
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
        "actual_day",
        "lot",
        "kind",
        "qty",
        "task",
        "worklog",
        "preparation",
        "actor",
    )
    list_filter = ("kind", "actual_day", "actor", "task__project")
    search_fields = ("lot__code", "lot__name", "note")
    readonly_fields = ("actor", "created_at", "preparation", "worklog")

    def has_add_permission(self, request):
        return warehouse(request.user)


class ComponentInline(admin.TabularInline):
    model = Component
    fields = ("lot", "qty", "unit")
    extra = 1
    min_num = 1
    validate_min = True


@admin.action(description="Допустить выбранные готовые партии")
def approve_preparations(modeladmin, request, queryset):
    if not lead(request.user):
        raise PermissionDenied

    for pk in queryset.values_list("pk", flat=True):
        with transaction.atomic():
            p = Preparation.objects.select_for_update().get(pk=pk)
            if not p.processed or p.approved_at:
                modeladmin.message_user(
                    request,
                    f"{p}: уже допущена либо ещё не проведена.",
                    messages.WARNING,
                )
                continue
            if p.actor_id == request.user.pk:
                modeladmin.message_user(
                    request,
                    f"{p}: собственную подготовку допускает другой руководитель.",
                    messages.ERROR,
                )
                continue

            lot = Lot.objects.select_for_update().get(pk=p.output_id)
            if lot.expired:
                modeladmin.message_user(
                    request, f"{p}: срок годности истёк.", messages.ERROR
                )
                continue

            p.approved_by = request.user
            p.approved_at = timezone.now()
            p.save(update_fields=["approved_by", "approved_at"])
            lot.released = True
            lot.save(update_fields=["released"])
            audit(request.user, p, "Допуск готовой партии")


@admin.register(Preparation)
class PreparationAdmin(CaptureAdmin):
    change_list_template = "admin/lab/preparation/change_list.html"
    list_display = (
        "actual_day",
        "kind",
        "output",
        "qty",
        "unit",
        "actor",
        "processed",
        "approved_by",
        "approved_at",
    )
    list_filter = ("kind", "actual_day", "actor", "processed", "unit")
    search_fields = ("output__code", "output__name", "protocol", "note")
    readonly_fields = (
        "actor",
        "created_at",
        "processed",
        "approved_by",
        "approved_at",
    )
    inlines = [ComponentInline]
    actions = [approve_preparations, export_csv]

    def add_view(self, request, form_url="", extra_context=None):
        extra = dict(extra_context or {})
        extra.setdefault("title", "Добавить общелабораторную работу")
        return super().add_view(request, form_url=form_url, extra_context=extra)

    def has_add_permission(self, request):
        return bool(request.user.is_active and request.user.is_staff)

    def get_inline_instances(self, request, obj=None):
        if obj is not None:
            return []
        return super().get_inline_instances(request, obj)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if not change:
            form.instance.complete()


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
            content,
            cls=DjangoJSONEncoder,
            ensure_ascii=False,
            indent=2,
        ),
        content_type="application/json; charset=utf-8",
    )
    response["Content-Disposition"] = (
        'attachment; filename="quarter-reports.json"'
    )
    return response


@admin.register(Report)
class ReportAdmin(ReadOnlyAdmin):
    report_template = "admin/lab/report/read_report.html"
    list_display = (
        "year",
        "quarter",
        "created_at",
        "approved_by",
        "approved_at",
        "sent_at",
        "open_report",
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
            raise Http404

        sections = []
        for heading, rows in report.payload.items():
            if not isinstance(rows, list):
                sections.append(
                    {
                        "heading": heading,
                        "is_table": False,
                        "text": str(rows),
                    }
                )
            elif not rows:
                sections.append(
                    {
                        "heading": heading,
                        "is_table": True,
                        "headers": [],
                        "rows": [],
                    }
                )
            else:
                headers = [str(key) for key in rows[0].keys()]
                table_rows = [
                    [
                        "—" if value is None else str(value)
                        for value in row.values()
                    ]
                    for row in rows
                ]
                sections.append(
                    {
                        "heading": heading,
                        "is_table": True,
                        "headers": headers,
                        "rows": table_rows,
                    }
                )

        return TemplateResponse(
            request,
            self.report_template,
            {"report": report, "sections": sections},
        )


@admin.register(Audit)
class AuditAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "actor", "entity", "object_id", "detail")
    list_filter = ("actor", "entity")
    search_fields = ("detail", "object_id")

    def has_view_permission(self, request, obj=None):
        return lead(request.user)
