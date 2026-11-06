from functools import wraps
from django import forms
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import connection,transaction
from django.db.models import Q
from django.db.models.signals import m2m_changed
from .models import (Project,Sample,SampleProjectMembership,Task,TaskEvent,WorkLog,
    LaboratoryProtocolRun,LaboratoryProtocolRunSample)

LOCK_KEY=82346011

def graph_lock():
    if connection.vendor!="postgresql" or not connection.in_atomic_block:
        raise RuntimeError("8A requires PostgreSQL and an atomic transaction")
    with connection.cursor() as cursor:cursor.execute("SELECT pg_advisory_xact_lock(%s)",[LOCK_KEY])

def normalize_projects(value):
    if isinstance(value,Project):values=[value.pk]
    elif value is None:values=[]
    elif isinstance(value,(int,str)):values=[value]
    elif hasattr(value,"values_list"):values=list(value.values_list("pk",flat=True))
    else:values=[getattr(v,"pk",v) for v in value]
    try:ids={int(v) for v in values}
    except (ValueError,TypeError):raise ValidationError("Некорректный список проектов.")
    if not ids or min(ids)<=0:raise ValidationError("Выберите хотя бы один проект.")
    if set(Project.objects.filter(pk__in=ids).values_list("pk",flat=True))!=ids:
        raise ValidationError("Один из проектов больше не существует.")
    return ids

def current_ids(sample_id):
    return set(SampleProjectMembership.objects.filter(sample_id=sample_id).values_list("project_id",flat=True))

def proposed_ids(sample):
    if hasattr(sample,"_pending_project_ids"):return set(sample._pending_project_ids)
    if sample._state.adding:return {sample.project_id} if sample.project_id else set()
    return current_ids(sample.pk)

def validate_candidate(sample,ids):
    ids=normalize_projects(ids)
    if sample.parent_id:
        if sample.parent_id==sample.pk:raise ValidationError("Образец не может ссылаться на себя.")
        if not current_ids(sample.parent_id).intersection(ids):
            raise ValidationError("У исходной и производной проб должен быть хотя бы один общий проект.")
    if sample.pk and not sample._state.adding:
        children=Sample.objects.filter(parent_id=sample.pk).exclude(projects__pk__in=ids)
        if children.exists():raise ValidationError("Изменение оставит производную пробу без общего проекта с исходной.")
        tasks=Task.objects.filter(Q(sample_id=sample.pk)|Q(samples__pk=sample.pk)).exclude(project_id__in=ids)
        if tasks.exists():raise ValidationError("Нельзя убрать проект, используемый связанной задачей.")
        if WorkLog.objects.filter(samples__pk=sample.pk,task__isnull=False).exclude(task__project_id__in=ids).exists():
            raise ValidationError("Проект используется в журнале работ этой пробы.")
        if LaboratoryProtocolRun.objects.filter(samples__pk=sample.pk).exclude(task__project_id__in=ids).exists():
            raise ValidationError("Проект используется в выполнении лабораторного протокола.")
    return ids

def validate_model_sample(sample):validate_candidate(sample,proposed_ids(sample))
def sample_has_project(sample_id,project_id):
    return bool(project_id and SampleProjectMembership.objects.filter(sample_id=sample_id,project_id=project_id).exists())

def project_names(sample):
    return "; ".join(p.title for p in sorted(sample.projects.all(),key=lambda p:(p.title,p.pk)))

def _write_memberships(sample,ids):
    existing=current_ids(sample.pk)
    SampleProjectMembership.objects.bulk_create([SampleProjectMembership(sample=sample,project_id=pk) for pk in sorted(ids-existing)])
    SampleProjectMembership.objects.filter(sample=sample).exclude(project_id__in=ids).delete()
    if hasattr(sample,"_prefetched_objects_cache"):sample._prefetched_objects_cache.pop("projects",None)

def build_sample(*,project,**kwargs):
    ids=normalize_projects(project)
    sample=Sample(project_id=min(ids),**kwargs)
    sample._pending_project_ids=ids
    return sample

class SampleProjectsField(forms.ModelMultipleChoiceField):
    def clean(self,value):
        if isinstance(value,Project):value=[value.pk]
        elif isinstance(value,(int,str)) and value!="":value=[value]
        return super().clean(value)

@transaction.atomic
def set_projects(sample_id,projects,user):
    from .sample_batch import can_edit_samples
    from .worklog6 import record
    if not can_edit_samples(user):raise PermissionDenied
    graph_lock();sample=Sample.objects.get(pk=sample_id)
    ids=validate_candidate(sample,normalize_projects(projects));before=current_ids(sample.pk)
    if before==ids:return False
    _write_memberships(sample,ids)
    record(user,sample,{"действие":"Изменение проектов образца","было":sorted(before),"стало":sorted(ids)})
    return True

@transaction.atomic
def add_projects(sample_ids,projects,user):
    from .sample_batch import can_edit_samples
    if not can_edit_samples(user):raise PermissionDenied
    graph_lock();extra=normalize_projects(projects)
    ids={int(v) for v in sample_ids};samples=list(Sample.objects.filter(pk__in=ids).order_by("pk"))
    if {s.pk for s in samples}!=ids:raise ValidationError("Один из образцов недоступен или удалён.")
    changed=0
    for sample in samples:changed+=set_projects(sample.pk,current_ids(sample.pk)|extra,user)
    return changed

def check_task(task):
    if not task.project_id:return
    ids={task.sample_id} if task.sample_id else set()
    if task.pk and not task._state.adding:
        ids.update(task.samples.values_list("pk",flat=True))
    if ids and Sample.objects.filter(pk__in=ids).exclude(projects__pk=task.project_id).exists():
        raise ValidationError("Проект задачи должен входить в проекты каждой выбранной пробы.")
    if task.pk and not task._state.adding:
        for work in WorkLog.objects.filter(task_id=task.pk):
            if work.samples.exclude(projects__pk=task.project_id).exists():raise ValidationError("Новый проект не соответствует пробам журнала.")
        for run in LaboratoryProtocolRun.objects.filter(task_id=task.pk):
            if run.samples.exclude(projects__pk=task.project_id).exists():raise ValidationError("Новый проект не соответствует пробам выполнения.")

def projects_changed(sender,instance,action,reverse,pk_set,**kwargs):
    if action not in {"pre_add","pre_remove","pre_clear"}:return
    graph_lock()
    if reverse:
        sample_ids=set(pk_set) if pk_set is not None else set(SampleProjectMembership.objects.filter(project_id=instance.pk).values_list("sample_id",flat=True))
        for sample in Sample.objects.filter(pk__in=sample_ids):
            old=current_ids(sample.pk)
            new=old|{instance.pk} if action=="pre_add" else old-{instance.pk}
            validate_candidate(sample,new)
    else:
        old=current_ids(instance.pk)
        new=old|set(pk_set or []) if action=="pre_add" else (set() if action=="pre_clear" else old-set(pk_set or []))
        validate_candidate(instance,new)

def linked_samples_changed(sender,instance,action,reverse,pk_set,**kwargs):
    if action not in {"pre_add","pre_remove","pre_clear"}:return
    graph_lock()
    if action!="pre_add":return
    if sender is Task.samples.through:
        if reverse:
            for task in Task.objects.filter(pk__in=pk_set):
                if not sample_has_project(instance.pk,task.project_id):raise ValidationError("Проба не входит в проект задачи.")
        elif Sample.objects.filter(pk__in=pk_set).exclude(projects__pk=instance.project_id).exists():
            raise ValidationError("Проба не входит в проект задачи.")
    # Existing journal/run services retain their content validators.

def install_models():
    if getattr(Sample,"_projects8a_models",False):return
    old_save=Sample.save
    @wraps(old_save)
    def sample_save(self,*args,**kwargs):
        with transaction.atomic():
            graph_lock();adding=self._state.adding
            if not adding:
                original=Sample.objects.values_list("project_id",flat=True).get(pk=self.pk)
                if self.project_id!=original:raise ValidationError("Техническое поле project не редактируется; изменяйте список проектов.")
            ids=validate_candidate(self,proposed_ids(self))
            result=old_save(self,*args,**kwargs)
            _write_memberships(self,ids)
            if hasattr(self,"_pending_project_ids"):del self._pending_project_ids
            return result
    Sample.save=sample_save
    Sample.project_names=property(project_names)
    for model in (Task,TaskEvent,WorkLog,LaboratoryProtocolRun,LaboratoryProtocolRunSample):
        previous=model.save
        def make_save(previous,model):
            @wraps(previous)
            def save(self,*args,**kwargs):
                with transaction.atomic():
                    graph_lock()
                    if model is Task:check_task(self)
                    if model is LaboratoryProtocolRunSample:self.full_clean()
                    return previous(self,*args,**kwargs)
            return save
        model.save=make_save(previous,model)
    m2m_changed.connect(projects_changed,sender=Sample.projects.through,weak=False,dispatch_uid="sample_projects_8a")
    for model in (Task,WorkLog,LaboratoryProtocolRun):
        m2m_changed.connect(linked_samples_changed,sender=model.samples.through,weak=False,dispatch_uid="sample_projects_8a_"+model.__name__)
    Sample._projects8a_models=True
