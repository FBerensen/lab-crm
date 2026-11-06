import csv,io
from types import MethodType
from django import forms
from django.contrib import admin,messages
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.http import HttpResponse
from django.template import engines
from django.urls import reverse
from .models import Sample,Project
from .sample_projects8a import (SampleProjectsField,current_ids,normalize_projects,graph_lock,
    project_names,add_projects)
from .sample_batch import can_edit_samples
from .worklog6 import record

PAGE="""{% extends "admin/base_site.html" %}{% block content %}<h1>Добавить проекты выбранным образцам</h1>
<p>Выбрано образцов: {{ count }}. Прежние проекты сохранятся. Копии образцов не создаются.</p>
<form method="post">{% csrf_token %}{% for pk in ids %}<input type="hidden" name="_selected_action" value="{{ pk }}">{% endfor %}
<input type="hidden" name="action" value="add_sample_projects_8a">{{ form.as_p }}<button name="apply_projects_8a" value="1">Добавить проекты</button></form>
<p><a href="{{ back }}">Отмена</a></p>{% endblock %}"""
class AddForm(forms.Form):
    projects=SampleProjectsField(label="Проекты",queryset=Project.objects.all(),widget=forms.SelectMultiple(attrs={"size":8}))

@admin.action(description="Добавить проекты выбранным образцам",permissions=["change"])
def add_sample_projects_8a(ma,request,queryset):
    if not can_edit_samples(request.user):raise PermissionDenied
    samples=list(queryset.order_by("pk"))
    if not samples or any(not ma.has_change_permission(request,s) for s in samples):raise PermissionDenied
    form=AddForm(request.POST if request.POST.get("apply_projects_8a")=="1" else None)
    if form.is_bound and form.is_valid():
        try:
            changed=add_projects([s.pk for s in samples],form.cleaned_data["projects"],request.user)
            ma.message_user(request,f"Проекты добавлены. Изменено образцов: {changed}.",messages.SUCCESS)
            return None
        except ValidationError as exc:form.add_error(None,"; ".join(exc.messages))
    return HttpResponse(engines["django"].from_string(PAGE).render({**ma.admin_site.each_context(request),"opts":Sample._meta,
        "title":"Добавить проекты","form":form,"ids":[s.pk for s in samples],"count":len(samples),"back":reverse("admin:lab_sample_changelist")},request))

@admin.action(description="Экспорт CSV")
def export_samples_csv(ma,request,queryset):
    from .admin import safe_cell
    fields=list(Sample._meta.fields);stream=io.StringIO();writer=csv.writer(stream)
    writer.writerow(["Проекты" if f.name=="project" else str(f.verbose_name) for f in fields])
    for obj in queryset.prefetch_related("projects"):
        writer.writerow([safe_cell(project_names(obj) if f.name=="project" else getattr(obj,f.name)) for f in fields])
    response=HttpResponse(stream.getvalue(),content_type="text/csv; charset=utf-8")
    response["Content-Disposition"]='attachment; filename="labcrm-samples.csv"'
    return response

def install(site):
    ma=site._registry[Sample]
    if getattr(ma,"_sample_projects8a_installed",False):return
    base=ma.form
    class ProjectsForm(base):
        project=SampleProjectsField(label="Проекты",queryset=Project.objects.all(),widget=forms.SelectMultiple(attrs={"size":8}),help_text="Один образец может участвовать в нескольких проектах. Выберите хотя бы один.")
        class Meta(base.Meta):
            model=Sample
            exclude=tuple(dict.fromkeys((*(base._meta.exclude or ()),"project","projects")))
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            if self.instance.pk:self.initial["project"]=sorted(current_ids(self.instance.pk))
            names=list(self.fields)
            if "project" in names:
                names.remove("project");index=names.index("number")+1 if "number" in names else 0
                names.insert(index,"project");self.order_fields(names)
        def clean(self):
            data=super().clean()
            if data.get("project") is not None:
                ids=normalize_projects(data["project"]);self.instance._pending_project_ids=ids
                if self.instance._state.adding:self.instance.project_id=min(ids)
            return data
    ma.form=ProjectsForm
    old_queryset,old_save,old_related,old_changelist=ma.get_queryset,ma.save_model,ma.save_related,ma.changelist_view
    def queryset(self,request):return old_queryset(request).prefetch_related("projects")
    def display_projects(self,obj):return project_names(obj)
    display_projects.short_description="Проекты"
    @transaction.atomic
    def save_model(self,request,obj,form,change):
        if not (self.has_change_permission(request,obj) if change else self.has_add_permission(request)):raise PermissionDenied
        graph_lock();form._projects8a_before=sorted(current_ids(obj.pk)) if change else []
        return old_save(request,obj,form,change)
    def save_related(self,request,form,formsets,change):
        old_related(request,form,formsets,change)
        after=sorted(current_ids(form.instance.pk));before=getattr(form,"_projects8a_before",[])
        if before!=after:record(request.user,form.instance,{"действие":"Проекты образца","было":before,"стало":after})
    def changelist(self,request,extra_context=None):
        params=request.GET.copy()
        for old,new in (("project__id__exact","projects__id__exact"),("project__exact","projects__id__exact")):
            if old in params and new not in params:params.setlist(new,params.getlist(old));params.pop(old)
        request.GET=params
        return old_changelist(request,extra_context=extra_context)
    ma.get_queryset=MethodType(queryset,ma);ma.save_model=MethodType(save_model,ma);ma.save_related=MethodType(save_related,ma)
    ma.changelist_view=MethodType(changelist,ma);ma.projects_display_8a=MethodType(display_projects,ma)
    ma.list_display=tuple("projects_display_8a" if x=="project" else x for x in ma.list_display)
    ma.list_filter=tuple("projects" if x=="project" else x for x in ma.list_filter)
    ma.search_fields=tuple("projects__title" if x=="project__title" else x for x in ma.search_fields)
    if isinstance(ma.list_select_related,(tuple,list)):ma.list_select_related=tuple(x for x in ma.list_select_related if x!="project")
    ma.actions=tuple(export_samples_csv if x=="export_csv" or getattr(x,"__name__",None)=="export_csv" else x for x in (ma.actions or ()))+(add_sample_projects_8a,)
    ma._sample_projects8a_installed=True
