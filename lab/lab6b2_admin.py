from types import MethodType
from django import forms
from django.contrib import admin, messages
from django.contrib.admin.widgets import AdminDateWidget
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpResponse, JsonResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.template import engines
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from .models import (Task, WorkLog, LaboratoryProtocol, LaboratoryProtocolRun,
    LaboratoryProtocolRunSample, TaskMaterialPlan, lead)
from .worklog6 import record
from .worklog6_admin import WorkForm
from .lab6b2_services import staff, manages, start_run, close_run, plan_metrics, validate_journal_samples

class ReadMixin:
    def has_module_permission(self, request): return staff(request.user)
    def has_view_permission(self, request, obj=None): return staff(request.user)
    def has_delete_permission(self, request, obj=None): return False

class TemplateAdmin(ReadMixin, admin.ModelAdmin):
    list_display=("code","title","version","method_kind","active")
    search_fields=("code","title","version")
    readonly_fields=("created_by","created_at")
    def has_add_permission(self, request): return lead(request.user)
    def has_change_permission(self, request, obj=None): return lead(request.user)
    def save_model(self, request, obj, form, change):
        if not change: obj.created_by=request.user
        super().save_model(request,obj,form,change)
        record(request.user,obj,{"действие":"Изменение методики" if change else "Создание методики","поля":form.changed_data})

class RunForm(forms.ModelForm):
    class Meta:
        model=LaboratoryProtocolRun
        fields="__all__"
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        user=getattr(self,"request_user",None)
        if "task" in self.fields:
            qs=Task.objects.exclude(status="Завершено")
            if user and not lead(user): qs=qs.filter(owner=user)
            self.fields["task"].queryset=qs
        if "protocol" in self.fields and self.instance._state.adding:
            self.fields["protocol"].queryset=LaboratoryProtocol.objects.filter(active=True)

class RunSamples(admin.TabularInline):
    model=LaboratoryProtocolRunSample
    fields=("sample","note")
    extra=1
    def has_view_permission(self,request,obj=None): return staff(request.user)
    def has_add_permission(self,request,obj=None): return staff(request.user) and (obj is None or (obj.status=="draft" and manages(request.user,obj.task)))
    def has_change_permission(self,request,obj=None): return self.has_add_permission(request,obj)
    def has_delete_permission(self,request,obj=None): return self.has_add_permission(request,obj)

class ActionForm(forms.Form):
    day=forms.DateField(label="Дата выполнения действия",initial=timezone.localdate,widget=AdminDateWidget(format="%d.%m.%Y"),input_formats=["%d.%m.%Y","%Y-%m-%d"])
    result=forms.CharField(label="Результат для закрытия",required=False,widget=forms.Textarea)

ACTION_PAGE="""
{% extends "admin/base_site.html" %}{% block extrahead %}{{ block.super }}
<script src="{% url 'admin:jsi18n' %}"></script>{{ form.media }}{% endblock %}
{% block content %}<h1>{{ title }}</h1><p>{{ run }}. Состояние: {{ run.get_status_display }}.</p>
<p>Начало фиксирует версию методики и состав проб. Закрытие ничего не списывает и требует подтверждённого расхода.</p>
{% if error %}<p class="errornote">{{ error }}</p>{% endif %}
{% if allowed %}<form method="post">{% csrf_token %}{{ form.as_p }}
{% if run.status == 'draft' %}<button name="action" value="start">Начать выполнение</button>{% endif %}
{% if run.status == 'running' %}<button name="action" value="close">Закрыть выполнение</button>{% endif %}</form>{% endif %}
<p><a href="{{ card }}">Карточка выполнения</a> | <a href="{{ journal }}">Журнал выполнения</a> {% if run.status == 'running' %}| <a href="{{ add_journal }}">Добавить запись работы</a>{% endif %}</p>{% endblock %}
"""

class RunAdmin(ReadMixin,admin.ModelAdmin):
    form=RunForm
    inlines=(RunSamples,)
    list_display=("id","task","protocol","executor","status","started_on","finished_on","actions_link")
    list_filter=("status","protocol__method_kind")
    readonly_fields=("executor","status","started_on","finished_on","result","protocol_snapshot","created_by","created_at","closed_by","closed_at","actions_link")
    def has_add_permission(self,request): return staff(request.user)
    def has_change_permission(self,request,obj=None):
        return staff(request.user) and (obj is None or (obj.status!="closed" and manages(request.user,obj.task)))
    def get_readonly_fields(self,request,obj=None):
        return (*self.readonly_fields,"task","protocol") if obj else self.readonly_fields
    def get_form(self,request,obj=None,**kwargs):
        base=super().get_form(request,obj,**kwargs)
        class Bound(base): request_user=request.user
        return Bound
    @transaction.atomic
    def save_model(self,request,obj,form,change):
        if not manages(request.user,obj.task): raise PermissionDenied
        if not change: obj.created_by=request.user
        super().save_model(request,obj,form,change)
        record(request.user,obj,{"действие":"Карточка выполнения","поля":form.changed_data})
    def save_related(self,request,form,formsets,change):
        super().save_related(request,form,formsets,change)
        record(request.user,form.instance,{"действие":"Состав выполнения","пробы":list(form.instance.samples.values_list("pk",flat=True))})
    @admin.display(description="Действия")
    def actions_link(self,obj):
        if not obj or not obj.pk: return "После сохранения"
        return format_html('<a href="{}">Начать / закрыть / журнал</a>',reverse("admin:lab_run_actions",args=[obj.pk]))
    def get_urls(self):
        return [path("<int:object_id>/actions/",self.admin_site.admin_view(self.actions_view),name="lab_run_actions")]+super().get_urls()
    def actions_view(self,request,object_id):
        if not staff(request.user): raise PermissionDenied
        run=get_object_or_404(LaboratoryProtocolRun.objects.select_related("task","protocol"),pk=object_id)
        form=ActionForm(request.POST or None); error=""
        try:
            if request.method=="POST" and form.is_valid():
                action=request.POST.get("action")
                if action=="start": start_run(run.pk,request.user,form.cleaned_data["day"])
                elif action=="close": close_run(run.pk,request.user,form.cleaned_data["result"],form.cleaned_data["day"])
                else: raise ValidationError("Неизвестное действие.")
                self.message_user(request,"Действие выполнено. Списания при закрытии нет.",messages.SUCCESS)
                return redirect(request.path)
        except ValidationError as exc: error="; ".join(exc.messages)
        context={**self.admin_site.each_context(request),"opts":self.model._meta,"title":"Выполнение методики",
            "run":run,"form":form,"error":error,"allowed":manages(request.user,run.task) and run.status!="closed",
            "card":reverse("admin:lab_laboratoryprotocolrun_change",args=[run.pk]),
            "journal":reverse("admin:lab_worklog_changelist")+f"?laboratory_run__id__exact={run.pk}",
            "add_journal":reverse("admin:lab_worklog_add")+f"?task={run.task_id}&laboratory_run={run.pk}"}
        return HttpResponse(engines["django"].from_string(ACTION_PAGE).render(context,request))

class PlanForm(forms.ModelForm):
    class Meta:
        model=TaskMaterialPlan
        fields="__all__"
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        user=getattr(self,"request_user",None)
        if "task" in self.fields:
            qs=Task.objects.exclude(status="Завершено")
            if user and not lead(user):qs=qs.filter(owner=user)
            self.fields["task"].queryset=qs

REPORT="""
{% extends "admin/base_site.html" %}{% block content %}<h1>План и остаточная потребность задач</h1>
<p>План — общее количество на задачу. Списание выполняется только через журнал. Ожидающий расход показан отдельно и не прибавляется к плану.</p>
<table><tr><th>Задача</th><th>Продукт</th><th>План</th><th>Списано</th><th>Осталось</th><th>Превышение</th><th>Резерв задачи</th><th>Не покрыто резервом</th><th>На подтверждении</th><th>Предупреждения</th></tr>
{% for r in rows %}<tr><td>{{ r.plan.task }}</td><td>{{ r.plan.lot }} [{{ r.plan.unit_snapshot }}]</td><td>{{ r.plan.planned_qty }}</td>
<td>{{ r.spent }}</td><td>{{ r.remaining }}</td><td>{{ r.overrun }}</td><td>{{ r.own_reserve }}</td><td>{{ r.uncovered }}</td><td>{{ r.pending }}</td>
<td>{% if r.unit_mismatch %}Единица требует сверки.{% endif %}{% if r.pending_over_plan %}Ожидающий расход превышает остаток плана на {{ r.pending_over_plan }}.{% endif %}</td></tr>{% empty %}<tr><td colspan="10">Нет активных планов незавершённых задач.</td></tr>{% endfor %}</table>
<h2>Общая потребность в свободном остатке</h2>
<p>Резервы других задач не используются. Это прогноз без автоматического резервирования.</p>
<table><tr><th>Продукт</th><th>Не покрыто собственными резервами</th><th>Свободно доступно</th><th>Нехватка</th></tr>
{% for g in totals %}<tr><td>{{ g.lot }} [{{ g.lot.unit }}]</td><td>{{ g.need }}</td><td>{{ g.available }}</td><td>{% if g.invalid %}Нужна сверка единиц{% else %}{{ g.shortage }}{% endif %}</td></tr>{% endfor %}</table>
<p><a href="{{ plans_url }}">Редактирование планов</a> | <a href="{{ stock_url }}">Наличие и ожидающий расход</a></p>{% endblock %}
"""

class PlanAdmin(ReadMixin,admin.ModelAdmin):
    form=PlanForm
    list_display=("task","lot","planned_qty","unit_snapshot","active","updated_at")
    list_filter=("active","task__project","task__status")
    readonly_fields=("unit_snapshot","created_by","created_at","updated_by","updated_at","report_link")
    def has_add_permission(self,request):return staff(request.user)
    def has_change_permission(self,request,obj=None):return staff(request.user) and (obj is None or (obj.task.status!="Завершено" and manages(request.user,obj.task)))
    def get_readonly_fields(self,request,obj=None):return (*self.readonly_fields,"task","lot") if obj else self.readonly_fields
    def get_form(self,request,obj=None,**kwargs):
        base=super().get_form(request,obj,**kwargs)
        class Bound(base):request_user=request.user
        return Bound
    @transaction.atomic
    def save_model(self,request,obj,form,change):
        if not manages(request.user,obj.task):raise PermissionDenied
        before=TaskMaterialPlan.objects.filter(pk=obj.pk).values().first() if change else None
        if not change:obj.created_by=request.user
        obj.updated_by=request.user
        super().save_model(request,obj,form,change)
        record(request.user,obj,{"действие":"План задачи","было":before,"стало":TaskMaterialPlan.objects.filter(pk=obj.pk).values().get()})
    @admin.display(description="Расчёт")
    def report_link(self,obj):return format_html('<a href="{}">План и потребность</a>',reverse("admin:lab_material_plan_report"))
    def get_urls(self):return [path("summary/",self.admin_site.admin_view(self.summary_view),name="lab_material_plan_report")]+super().get_urls()
    def summary_view(self,request):
        if not staff(request.user):raise PermissionDenied
        if request.method!="GET":return HttpResponseNotAllowed(["GET"])
        from decimal import Decimal
        rows=plan_metrics(TaskMaterialPlan.objects.filter(active=True).exclude(task__status="Завершено").select_related("task","lot").order_by("task_id","lot_id"))
        totals={}
        for r in rows:
            lot=r["plan"].lot
            g=totals.setdefault(lot.pk,{"lot":lot,"need":Decimal("0"),"invalid":False})
            g["need"]+=r["uncovered"];g["invalid"]|=r["unit_mismatch"]
        for g in totals.values():
            lot=g["lot"];g["available"]=max(lot.free,Decimal("0")) if lot.released and not lot.expired else Decimal("0")
            g["shortage"]=max(g["need"]-g["available"],Decimal("0"))
        return HttpResponse(engines["django"].from_string(REPORT).render({**self.admin_site.each_context(request),"opts":self.model._meta,
            "rows":rows,"totals":list(totals.values()),"plans_url":reverse("admin:lab_taskmaterialplan_changelist"),"stock_url":reverse("admin:lab_stockavailability_changelist")},request))

class JournalForm(WorkForm):
    class Media:
        js=("lab/js/lab6b2.js",)
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        task_id=self.instance.task_id
        if self.is_bound:
            value=self.data.get(self.add_prefix("task"));task_id=int(value) if value and str(value).isdigit() else None
        elif self.initial.get("task"):task_id=self.initial["task"]
        if "laboratory_run" in self.fields:
            self.fields["laboratory_run"].widget.attrs["data-runs-url"]=reverse("admin:lab_worklog_task_runs")
            self.fields["laboratory_run"].widget.attrs["data-run-samples-url"]=reverse("admin:lab_worklog_run_samples")
            self.fields["laboratory_run"].queryset=LaboratoryProtocolRun.objects.filter(task_id=task_id).exclude(status="draft") if task_id else LaboratoryProtocolRun.objects.none()
    def clean(self):
        data=super().clean();run=data.get("laboratory_run");samples=data.get("samples")
        if run:
            if not data.get("task") or run.task_id!=data["task"].pk:raise ValidationError("Выполнение относится к другой задаче.")
            if samples and samples.exclude(pk__in=run.samples.values("pk")).exists():raise ValidationError("Пробы не входят в выполнение.")
        return data

def install(site):
    for model,cls in ((LaboratoryProtocol,TemplateAdmin),(LaboratoryProtocolRun,RunAdmin),(TaskMaterialPlan,PlanAdmin)):
        if not site.is_registered(model):site.register(model,cls)
    journal_admin=site._registry[WorkLog]
    journal_admin.form=JournalForm
    old_urls=journal_admin.get_urls
    def run_choices(self,request):
        if not staff(request.user):raise PermissionDenied
        value=request.GET.get("task","")
        if not value:return JsonResponse({"results":[]})
        task=get_object_or_404(Task,pk=value) if value.isdigit() else None
        if task is None or not manages(request.user,task):raise PermissionDenied
        return JsonResponse({"results":[{"id":r.pk,"text":str(r)} for r in task.laboratory_runs.exclude(status="draft").select_related("protocol")]})
    def run_samples(self,request):
        if not staff(request.user):raise PermissionDenied
        value=request.GET.get("run","")
        run=get_object_or_404(LaboratoryProtocolRun,pk=value) if value.isdigit() else None
        if run is None or not manages(request.user,run.task):raise PermissionDenied
        return JsonResponse({"results":[{"id":s.pk,"text":s.code} for s in run.samples.all()]})
    journal_admin.run_choices_view=MethodType(run_choices,journal_admin)
    journal_admin.run_samples_view=MethodType(run_samples,journal_admin)
    def urls(self):
        return [path("task-runs/",site.admin_view(self.run_choices_view),name="lab_worklog_task_runs"),
            path("run-samples/",site.admin_view(self.run_samples_view),name="lab_worklog_run_samples")]+old_urls()
    journal_admin.get_urls=MethodType(urls,journal_admin)
    ta=site._registry[Task]
    if not hasattr(ta,"laboratory_links"):
        def links(self,obj):
            if not obj or not obj.pk:return "После сохранения"
            return format_html('<a href="{}?task__id__exact={}">Выполнения</a> | <a href="{}?task__id__exact={}">План</a> | <a href="{}">Расчёт потребности</a>',
                reverse("admin:lab_laboratoryprotocolrun_changelist"),obj.pk,reverse("admin:lab_taskmaterialplan_changelist"),obj.pk,reverse("admin:lab_material_plan_report"))
        links.short_description="Методики и план"
        ta.laboratory_links=MethodType(links,ta)
        ta.readonly_fields=(*ta.readonly_fields,"laboratory_links")
