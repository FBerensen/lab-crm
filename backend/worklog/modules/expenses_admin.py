from types import MethodType
from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction, IntegrityError
from django.forms.models import BaseInlineFormSet, inlineformset_factory
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template import engines
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join
from lab.models import WorkLog, Task, Sample, Movement, WorkExpenseLine, WorkExpenseRequest, lead
from lab.worklog6 import lock_pending, post_expense, record
from lab.worklog6_permissions import active, trainee, can_post, can_edit_pending


class WorkForm(forms.ModelForm):
    class Meta:
        model = WorkLog
        fields = "__all__"
    class Media:
        js = ("lab/js/worklog6.js",)
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        task = self.instance.task if self.instance.task_id else None
        if self.is_bound:
            value = self.data.get(self.add_prefix("task"))
            task = Task.objects.filter(pk=value).first() if value and str(value).isdigit() else None
        if "samples" in self.fields:
            self.fields["samples"].queryset = task.samples.filter(projects__pk=task.project_id) if task else Sample.objects.none()
            self.fields["samples"].widget.attrs["data-worklog-samples-url"] = reverse("admin:lab_worklog_task_samples")
            self.fields["samples"].help_text = "После выбора задачи список проб загружается автоматически."
    def clean(self):
        data = super().clean()
        task, samples = data.get("task"), data.get("samples")
        if samples:
            if task is None or samples.exclude(pk__in=task.samples.values("pk")).exists():
                raise ValidationError("Выбирайте обработанные пробы только из состава задачи.")
        return data


class ExpenseLineForm(forms.ModelForm):
    class Meta:
        model = WorkExpenseLine
        fields = ("lot", "qty", "mode")
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["lot"].label_from_instance = lambda obj: f"{obj} [{obj.unit}]"


class ExpenseSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        seen = set()
        for form in self.forms:
            data = getattr(form, "cleaned_data", {})
            if not data or data.get("DELETE") or not data.get("lot"):
                continue
            key = (data["lot"].pk, data.get("mode"))
            if key in seen:
                raise ValidationError("Один продукт и источник указывайте одной строкой с общим количеством.")
            seen.add(key)
            if data.get("mode") == "use_reserved" and not self.instance.task_id:
                raise ValidationError("Для расхода резерва сначала выберите задачу.")


class ExpenseInline(admin.TabularInline):
    model = WorkExpenseLine
    form = ExpenseLineForm
    formset = ExpenseSet
    fields = ("lot", "qty", "mode")
    extra = 1
    def has_view_permission(self, request, obj=None):
        return active(request.user)
    def has_add_permission(self, request, obj=None):
        return active(request.user) and obj is None
    def has_change_permission(self, request, obj=None):
        return False
    def has_delete_permission(self, request, obj=None):
        return active(request.user) and obj is None


PAGE = """
{% extends "admin/base_site.html" %}
{% block content %}<h1>{{ title }}</h1>
<p>Журнал №{{ work.pk }}: {{ work.operation }}. Дата: {{ work.actual_day }}. Автор: {{ work.actor }}.</p>
<p>Состояние: {{ state }}{% if posting.posted_by %}; провёл {{ posting.posted_by }}, {{ posting.posted_at }}{% endif %}</p>
<p>Количество вводится в единице учёта выбранного продукта. Резерв списывается только по задаче этой записи.</p>
{% if errors %}<ul class="errorlist">{% for error in errors %}<li>{{ error }}</li>{% endfor %}</ul>{% endif %}
{% if editable %}<form method="post">{% csrf_token %}
<input type="hidden" name="action" value="save">{{ formset.management_form }}{{ formset.non_form_errors }}
{% for form in formset %}<fieldset>{{ form.as_p }}</fieldset>{% endfor %}
<input type="submit" value="{% if automatic %}Сохранить и списать{% else %}Сохранить на подтверждение{% endif %}" class="default">
</form>{% else %}<table><tr><th>Продукт</th><th>Количество</th><th>Ед. учёта</th><th>Источник</th></tr>
{% for line in lines %}<tr><td>{{ line.lot }}</td><td>{{ line.qty }}</td><td>{{ line.lot.unit }}</td><td>{{ line.get_mode_display }}</td></tr>{% endfor %}</table>{% endif %}
{% if approvable %}<form method="post">{% csrf_token %}<input type="hidden" name="action" value="approve">
<input type="submit" value="Подтвердить сохранённый расход" class="default"></form>
<p>Подтверждение проводит только уже сохранённые строки. Изменения в форме выше сначала сохраните.</p>{% endif %}
{% if posting.status == 'posted' %}<p>Расход проведён. Повторное подтверждение не создаёт нового списания.
Редактирование проведённых строк недоступно.</p>{% endif %}
<p><a href="{{ journal_url }}">Открыть запись журнала</a> | <a href="{{ queue_url }}">Расходы по журналу</a></p>
{% endblock %}
"""


def expense_view(self, request, object_id):
    if not active(request.user):
        raise PermissionDenied
    work = get_object_or_404(WorkLog, pk=object_id)
    FormSet = inlineformset_factory(WorkLog, WorkExpenseLine, form=ExpenseLineForm, formset=ExpenseSet,
        fields=("lot", "qty", "mode"), extra=1, can_delete=True)
    formset = FormSet(request.POST if request.method == "POST" and request.POST.get("action") == "save" else None,
                      instance=work, prefix="expenses")
    errors = []
    try:
        if request.method == "POST":
            action = request.POST.get("action")
            if action == "approve":
                created = post_expense(work.pk, request.user)
                self.message_user(request, "Расход проведён." if created else "Расход уже был проведён.", messages.SUCCESS)
                return redirect(request.path)
            if action != "save" or not can_edit_pending(request.user, work):
                raise PermissionDenied
            if formset.is_valid():
                with transaction.atomic():
                    lock_pending(work.pk)
                    before = list(work.expense_lines.order_by("pk").values("id", "lot_id", "qty", "mode"))
                    formset.save()
                    record(request.user, work, {"действие": "Сохранение строк расхода", "автор_работы": work.actor_id,
                        "было": before, "стало": list(work.expense_lines.order_by("pk").values("id", "lot_id", "qty", "mode"))})
                    if work.expense_lines.exists() and can_post(request.user):
                        post_expense(work.pk, request.user)
                self.message_user(request, "Расход сохранён и проведён." if can_post(request.user) and work.expense_lines.exists() else "Строки сохранены; остаток не изменён.", messages.SUCCESS)
                return redirect(request.path)
    except (ValidationError, IntegrityError) as exc:
        errors = exc.messages if isinstance(exc, ValidationError) else ["Конфликт сохранения. Ничего не проведено. Обновите страницу."]
    posting = WorkExpenseRequest.objects.filter(worklog=work).select_related("posted_by").first()
    posted = posting is not None and posting.status == "posted"
    context = {**self.admin_site.each_context(request), "opts": WorkLog._meta,
        "title": "Расход по записи журнала", "work": work, "posting": posting,
        "state": posting.get_status_display() if posting else "Расход не указан",
        "editable": not posted and can_edit_pending(request.user, work),
        "automatic": can_post(request.user), "approvable": not posted and can_post(request.user) and work.expense_lines.exists(),
        "formset": formset, "lines": work.expense_lines.select_related("lot").all(), "errors": errors,
        "journal_url": reverse("admin:lab_worklog_change", args=[work.pk]),
        "queue_url": reverse("admin:lab_workexpenserequest_changelist")}
    return HttpResponse(engines["django"].from_string(PAGE).render(context, request))


class RequestAdmin(admin.ModelAdmin):
    list_display = ("id", "worklog", "status", "posted_by", "posted_at", "open_expense")
    list_filter = ("status",)
    readonly_fields = ("worklog", "status", "posted_by", "posted_at", "created_at", "open_expense")
    fields = readonly_fields
    def has_module_permission(self, request):
        return active(request.user)
    def has_view_permission(self, request, obj=None):
        return active(request.user)
    def has_add_permission(self, request):
        return False
    def has_change_permission(self, request, obj=None):
        return False
    def has_delete_permission(self, request, obj=None):
        return False
    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("worklog", "posted_by")
        return qs if can_post(request.user) else qs.filter(worklog__actor=request.user)
    @admin.display(description="Расход")
    def open_expense(self, obj):
        return format_html('<a href="{}">Открыть</a>', reverse("admin:lab_worklog_expense", args=[obj.worklog_id]))


def install(site):
    ma = site._registry[WorkLog]
    if getattr(ma, "_stage6a_installed", False):
        return
    old_related, old_urls, old_form, old_queryset = ma.save_related, ma.get_urls, ma.get_form, ma.get_queryset
    ma.form = WorkForm
    ma.inlines = tuple(ma.inlines) + (ExpenseInline,)
    ma.filter_horizontal = tuple(name for name in ma.filter_horizontal if name != "samples")
    ma.readonly_fields = tuple(ma.readonly_fields) + ("expense_link",)
    def link(self, obj):
        if obj is None or not obj.pk:
            return "При сохранении расход спишется автоматически при наличии разрешения; иначе потребуется подтверждение. Для стажёра подтверждение обязательно."
        return format_html('<a href="{}">Строки расхода / подтверждение</a>', reverse("admin:lab_worklog_expense", args=[obj.pk]))
    link.short_description = "Расход"
    ma.expense_link = MethodType(link, ma)
    def related(self, request, form, formsets, change):
        old_related(request, form, formsets, change)
        work = form.instance
        if work.expense_lines.exists():
            record(request.user, work, {"действие": "Строки расхода при сохранении работы",
                "строки": list(work.expense_lines.order_by("pk").values("id", "lot_id", "qty", "mode"))})
            if can_post(request.user):
                post_expense(work.pk, request.user)
    def task_samples(self, request):
        if not active(request.user):
            raise PermissionDenied
        value = request.GET.get("task", "")
        if not value:
            return JsonResponse({"results": []})
        task = get_object_or_404(Task, pk=value) if value.isdigit() else None
        if task is None or (task.owner_id != request.user.pk and not lead(request.user)):
            raise PermissionDenied
        return JsonResponse({"results": [{"id": s.pk, "text": s.code} for s in task.samples.filter(projects__pk=task.project_id).order_by("series", "number")]})
    ma.task_samples_view = MethodType(task_samples, ma)
    def urls(self):
        return [path("task-samples/", site.admin_view(self.task_samples_view), name="lab_worklog_task_samples"),
            path("<int:object_id>/expense/", site.admin_view(self.expense_view), name="lab_worklog_expense")] + old_urls()
    def get_form(self, request, obj=None, **kwargs):
        base = old_form(request, obj, **kwargs)
        class Bound(base):
            def __init__(self, *args, **kw):
                super().__init__(*args, **kw)
                if not self.is_bound and not self.instance.task_id:
                    value = request.GET.get("task")
                    if value and value.isdigit():
                        task = Task.objects.filter(pk=value).first()
                        if task and "samples" in self.fields:
                            self.fields["samples"].queryset = task.samples.filter(projects__pk=task.project_id)
                            self.initial["task"] = task.pk
        return Bound
    ma.save_related = MethodType(related, ma)
    ma.expense_view = MethodType(expense_view, ma)
    ma.get_urls = MethodType(urls, ma)
    ma.get_form = MethodType(get_form, ma)
    def state(self, obj):
        try:
            return obj.expense_request.get_status_display()
        except WorkExpenseRequest.DoesNotExist:
            return "Расход не указан"
    state.short_description = "Расход"
    ma.expense_state = MethodType(state, ma)
    ma.list_display = tuple(ma.list_display) + ("expense_state",)
    ma.get_queryset = MethodType(lambda self, request: old_queryset(request).select_related("expense_request"), ma)
    ma._stage6a_installed = True
    if not site.is_registered(WorkExpenseRequest):
        site.register(WorkExpenseRequest, RequestAdmin)
    movement_admin = site._registry[Movement]
    previous_add = movement_admin.has_add_permission
    movement_admin.readonly_fields = tuple(movement_admin.readonly_fields) + ("worklog", "expense_line")
    movement_admin.has_add_permission = MethodType(lambda self, request: not trainee(request.user) and previous_add(request), movement_admin)
    movement_admin.list_display = tuple(movement_admin.list_display) + ("worklog",)
    task_admin = site._registry[Task]
    def summary(self, obj):
        if obj is None or not obj.pk:
            return "После сохранения задачи."
        url = reverse("admin:lab_movement_changelist")
        return format_html('<a href="{}?task__id__exact={}">Открыть складские операции задачи (включая расход по журналу)</a>', url, obj.pk)
    summary.short_description = "Складские операции"
    task_admin.work_expense_summary = MethodType(summary, task_admin)
    task_admin.readonly_fields = tuple(task_admin.readonly_fields) + ("work_expense_summary",)
