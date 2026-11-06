from types import MethodType
from django import forms
from django.contrib import admin,messages
from django.core.exceptions import PermissionDenied,ValidationError,ObjectDoesNotExist
from django.db import transaction,IntegrityError
from django.http import HttpResponse,HttpResponseNotAllowed
from django.shortcuts import get_object_or_404,redirect
from django.template import engines
from django.urls import path,reverse
from django.utils.html import format_html,format_html_join
from .models import Preparation,PreparationCalculator,PreparationDraftSelection,PreparationDraftLine
from . import preparation_allocation as allocation
from .preparation_drafts import (staff,can_edit,unseal,revalidate,MetadataForm,create_draft,confirm_facts,
    ensure_facts_confirmed,fact_snapshot,digest,number)

CREATE_PAGE="""
{% extends "admin/base_site.html" %}{% load static %}{% block extrahead %}{{ block.super }}<link rel="stylesheet" href="{% static 'admin/css/widgets.css' %}"><script src="{% url 'admin:jsi18n' %}"></script>{{ form.media }}{% endblock %}
{% block content %}<h1>Создать черновик из подбора</h1>
<p>Будет сохранён план, не фактический расход. Готовая складская партия не создаётся; списаний и резервов нет.
В черновике фактический объём и строки расхода останутся пустыми.</p>
{% if error %}<p class="errornote">{{ error }}</p>{% endif %}
{% if result %}<p>{{ result.calculation.protocol }}; план {{ result.calculation.volume_text }} мл.</p>
{% for warning in result.warnings %}<p>{{ warning }}</p>{% endfor %}
<form method="post">{% csrf_token %}<input type="hidden" name="token" value="{{ token }}">{{ form.as_p }}
<button name="action" value="create">Подтвердить создание черновика</button></form>{% endif %}
<p><a href="{{ back }}">Вернуться к подбору 7Б</a></p>{% endblock %}
"""

FACT_PAGE="""
{% extends "admin/base_site.html" %}{% block content %}<h1>Подтверждение фактических данных</h1>
<p>Приготовление №{{ p.pk }}. {{ p.recipe }}. Дата: {{ p.actual_day }}. Фактический объём: {{ p.actual_volume_ml|default_if_none:"Не указан" }} мл.</p>
<p>Это подтверждение введённых фактов, не проведение склада и не допуск готовой партии.
Фактические отклонения от плана остаются информационными, как в действующем приготовлении.</p>
{% if error %}<p class="errornote">{{ error }}</p>{% endif %}
<table><tr><th>Компонент</th><th>Партия</th><th>Факт</th></tr>{% for c in components %}
<tr><td>{{ c.recipe_component|default:"Не указан" }}</td><td>{{ c.lot }}</td><td>{{ c.qty }} {{ c.lot.unit }}</td></tr>{% endfor %}</table>
{% if ready %}<form method="post">{% csrf_token %}{{ form.as_p }}<button type="submit">Подтвердить факт без списания</button></form>{% endif %}
<p><a href="{{ card }}">К карточке: ввести/исправить факт</a></p>{% endblock %}
"""

class FactForm(forms.Form):
    confirmed=forms.BooleanField(label="Подтверждаю, что дата, фактический объём и строки внесены по реально выполненной работе")
    expected_digest=forms.RegexField(regex=r"^[0-9a-f]{64}$",widget=forms.HiddenInput)

def creation_view(self,request):
    if not staff(request.user):raise PermissionDenied
    if request.method!="POST":return HttpResponseNotAllowed(["POST"])
    token=request.POST.get("token","");result=None;error=""
    form=MetadataForm(request.POST if request.POST.get("action")=="create" else None)
    try:
        if request.POST.get("action")=="create" and form.is_valid():
            p,created=create_draft(token,request.user,request.POST)
            self.message_user(request,"Создан черновик без фактического расхода." if created else "Этот черновик уже был создан; повторная запись не сделана.",messages.SUCCESS)
            return redirect(reverse("admin:lab_preparation_change",args=[p.pk]))
        result=revalidate(unseal(token,request.user))
    except (ValidationError,IntegrityError,ObjectDoesNotExist) as exc:
        error="; ".join(exc.messages) if isinstance(exc,ValidationError) else "Данные изменились или возник конфликт. Повторите подбор; частичного черновика не сохранено."
    context={**self.admin_site.each_context(request),"opts":PreparationCalculator._meta,"title":"Черновик приготовления",
        "form":form,"result":result,"error":error,"token":token,"back":reverse("admin:lab_preparationcalculator_allocation")}
    return HttpResponse(engines["django"].from_string(CREATE_PAGE).render(context,request))

def facts_view(self,request,object_id):
    if request.method not in ("GET","POST"):return HttpResponseNotAllowed(["GET","POST"])
    p=get_object_or_404(Preparation,pk=object_id)
    if not can_edit(request.user,p):raise PermissionDenied
    if not PreparationDraftSelection.objects.filter(preparation=p).exists():raise PermissionDenied
    error="";ready=False;value=""
    try:value=digest(fact_snapshot(p));ready=True
    except ValidationError as exc:error="; ".join(exc.messages)
    form=FactForm(request.POST if request.method=="POST" else None,initial={"expected_digest":value})
    if request.method=="POST" and form.is_valid():
        try:
            changed=confirm_facts(p.pk,request.user,confirmed=form.cleaned_data["confirmed"],expected_digest=form.cleaned_data["expected_digest"])
            self.message_user(request,"Факт подтверждён без списания." if changed else "Этот факт уже подтверждён.",messages.SUCCESS)
            return redirect(reverse("admin:lab_preparation_change",args=[p.pk]))
        except (ValidationError,IntegrityError) as exc:
            error="; ".join(exc.messages) if isinstance(exc,ValidationError) else "Конфликт. Обновите страницу подтверждения."
    return HttpResponse(engines["django"].from_string(FACT_PAGE).render({**self.admin_site.each_context(request),"opts":Preparation._meta,
        "title":"Подтверждение факта","p":p,"form":form,"error":error,"ready":ready,
        "components":p.components.select_related("lot","recipe_component").all(),"card":reverse("admin:lab_preparation_change",args=[p.pk])},request))

class PlanLines(admin.TabularInline):
    model=PreparationDraftLine
    fields=("recipe_component","lot","planned_qty","unit_snapshot","manual_amount")
    readonly_fields=fields;extra=0;can_delete=False
    def has_view_permission(self,request,obj=None):return staff(request.user)
    def has_add_permission(self,request,obj=None):return False
    def has_change_permission(self,request,obj=None):return False
    def has_delete_permission(self,request,obj=None):return False

class SelectionAdmin(admin.ModelAdmin):
    list_display=("id","preparation","created_by","created_at","facts_confirmed_by","facts_confirmed_at")
    readonly_fields=tuple(f.name for f in PreparationDraftSelection._meta.concrete_fields)
    fields=readonly_fields
    inlines=(PlanLines,);actions=None
    def has_module_permission(self,request):return staff(request.user)
    def has_view_permission(self,request,obj=None):return staff(request.user)
    def has_add_permission(self,request):return False
    def has_change_permission(self,request,obj=None):return False
    def has_delete_permission(self,request,obj=None):return False

def install(site):
    calc_admin=site._registry[PreparationCalculator]
    prep_admin=site._registry[Preparation]
    if getattr(prep_admin,"_drafts7v_installed",False):return
    old_calc_urls=calc_admin.get_urls
    calc_admin.create_draft_view=MethodType(creation_view,calc_admin)
    def calculator_urls(self):
        return [path("draft/",site.admin_view(self.create_draft_view),name="lab_preparationcalculator_create_draft")]+old_calc_urls()
    calc_admin.get_urls=MethodType(calculator_urls,calc_admin)
    anchor="{% if result %}<h2>Результат проверки</h2>"
    addition="""{% if result %}<h2>Результат проверки</h2>
{% if draft7v %}<form method="post" action="{% url 'admin:lab_preparationcalculator_create_draft' %}">{% csrf_token %}
<input type="hidden" name="token" value="{{ draft7v }}"><button name="action" value="review">Создать черновик из плана</button></form>
<p>На следующем экране укажите параметры и подтвердите создание. Неполный растворитель останется неопределённым.</p>{% endif %}"""
    if allocation.PAGE.count(anchor)!=1:raise RuntimeError("Unexpected 7B template")
    allocation.PAGE=allocation.PAGE.replace(anchor,addition,1)
    if not site.is_registered(PreparationDraftSelection):site.register(PreparationDraftSelection,SelectionAdmin)
    old_form,old_save,old_readonly,old_urls,old_actions=prep_admin.get_form,prep_admin.save_model,prep_admin.get_readonly_fields,prep_admin.get_urls,prep_admin.get_actions
    capture=next(cls for cls in type(prep_admin).__mro__ if cls.__name__=="CaptureAdmin")
    def summary(self,obj):
        if obj is None or not obj.pk:return "Для черновиков из 7Б план появится после создания."
        selection=PreparationDraftSelection.objects.filter(preparation=obj).first()
        if selection is None:return "Обычное приготовление; существующий порядок работы."
        state="Факт не подтверждён"
        if obj.processed:state="Проведено"
        elif selection.facts_confirmed_by_id:
            try:ensure_facts_confirmed(obj);state="Факт подтверждён и актуален"
            except ValidationError:state="Факт/рецептура изменены: требуется повторное подтверждение"
        lines=format_html_join("","<tr><td>{}</td><td>{}</td><td>{}</td></tr>",[(r.recipe_component.reagent_name,str(r.lot),(number(r.planned_qty)+" "+r.unit_snapshot) if r.planned_qty is not None else "Не определено") for r in selection.lines.select_related("recipe_component","lot")])
        return format_html('<p>План: {} мл. {}. Плановые строки не являются фактическим расходом.</p><table><tr><th>Компонент</th><th>Партия</th><th>План</th></tr>{}</table><p>Факт введите вручную в строках исходных партий. Если растворитель не выбран в плане, его факт всё равно нужен до проведения. Дату выполнения уточните по реальной работе.</p>',selection.planned_volume_ml,state,lines)
    summary.short_description="Сохранённый план 7В"
    def controls(self,obj):
        if obj is None or not obj.pk or not PreparationDraftSelection.objects.filter(preparation=obj).exists():return "—"
        if obj.processed:return "Приготовление проведено"
        return format_html('<a href="{}">Проверить и подтвердить введённый факт (без списания)</a>',reverse("admin:lab_preparation_confirm_facts",args=[obj.pk]))
    controls.short_description="Подтверждение факта"
    def qty_display(self,obj):
        if obj is None:return "После сохранения"
        if PreparationDraftSelection.objects.filter(preparation=obj).exists():
            return "Фактический выход не указан; служебное количество не является выходом партии." if obj.actual_volume_ml is None else str(obj.actual_volume_ml)+" мл; состояние подтверждения смотрите в блоке плана."
        return obj.qty
    qty_display.short_description="Количество готовой партии"
    prep_admin.draft_summary=MethodType(summary,prep_admin);prep_admin.draft_controls=MethodType(controls,prep_admin);prep_admin.draft_qty_display=MethodType(qty_display,prep_admin)
    prep_admin.readonly_fields=(*prep_admin.readonly_fields,"draft_summary","draft_controls","draft_qty_display")
    if prep_admin.fields is not None:prep_admin.fields=tuple("draft_qty_display" if f=="qty" else f for f in prep_admin.fields)+("draft_summary","draft_controls")
    def get_readonly(self,request,obj=None):
        fields=list(old_readonly(request,obj))
        if obj is not None and PreparationDraftSelection.objects.filter(preparation=obj).exists():fields.extend(["recipe","planned_volume_ml"])
        return tuple(dict.fromkeys(fields))
    def get_form(self,request,obj=None,**kwargs):
        base=old_form(request,obj,**kwargs)
        draft=obj is not None and PreparationDraftSelection.objects.filter(preparation=obj).exists()
        class Bound(base):
            def __init__(self,*args,**kw):
                super().__init__(*args,**kw)
                if draft and "actual_volume_ml" in self.fields:self.fields["actual_volume_ml"].required=False
        return Bound
    @transaction.atomic
    def save_model(self,request,obj,form,change):
        selection=PreparationDraftSelection.objects.filter(preparation_id=obj.pk).first() if obj.pk else None
        if selection is not None and obj.actual_volume_ml is None:
            current=Preparation.objects.select_for_update().get(pk=obj.pk)
            if not self.has_change_permission(request,current):raise PermissionDenied
            if obj.recipe_id!=selection.protocol_id or obj.planned_volume_ml!=selection.planned_volume_ml:raise ValidationError("План черновика менять нельзя; создайте новый подбор.")
            obj.kind="Приготовление реактива";obj.protocol=str(obj.recipe)[:240];obj.qty=selection.planned_volume_ml
            return capture.save_model(self,request,obj,form,change)
        return old_save(request,obj,form,change)
    def urls(self):
        return [path("<int:object_id>/confirm-facts/",site.admin_view(self.confirm_facts_view),name="lab_preparation_confirm_facts")]+old_urls()
    def actions(self,request):
        values=old_actions(request)
        if "complete_preparation" in values:
            original,name,label=values["complete_preparation"]
            def guarded(modeladmin,request,queryset):
                try:
                    for p in queryset:ensure_facts_confirmed(p)
                except ValidationError as exc:
                    modeladmin.message_user(request,"; ".join(exc.messages),messages.ERROR);return
                return original(modeladmin,request,queryset)
            values["complete_preparation"]=(guarded,name,label)
        return values
    prep_admin.get_readonly_fields=MethodType(get_readonly,prep_admin);prep_admin.get_form=MethodType(get_form,prep_admin)
    prep_admin.save_model=MethodType(save_model,prep_admin);prep_admin.confirm_facts_view=MethodType(facts_view,prep_admin)
    prep_admin.get_urls=MethodType(urls,prep_admin);prep_admin.get_actions=MethodType(actions,prep_admin)
    prep_admin._drafts7v_installed=True
