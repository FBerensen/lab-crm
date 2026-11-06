from collections import defaultdict
from decimal import Decimal
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from .models import (Task, TaskEvent, WorkLog, WorkExpenseLine, LaboratoryProtocol,
    LaboratoryProtocolRun, TaskMaterialPlan, Movement, lead)
from .worklog6 import record

ZERO = Decimal("0")

def staff(user):
    return bool(user and user.is_active and user.is_staff)

def manages(user, task):
    return staff(user) and (lead(user) or task.owner_id == user.pk)

def lock_run(run_id):
    task_id = LaboratoryProtocolRun.objects.values_list("task_id", flat=True).get(pk=run_id)
    Task.objects.select_for_update().get(pk=task_id)
    return LaboratoryProtocolRun.objects.select_for_update().select_related("task", "protocol").get(pk=run_id)

def lock_journal(worklog_id, for_expense=True):
    row = WorkLog.objects.values("task_id", "laboratory_run_id").get(pk=worklog_id)
    if row["task_id"]: Task.objects.select_for_update().get(pk=row["task_id"])
    if row["laboratory_run_id"]: LaboratoryProtocolRun.objects.select_for_update().get(pk=row["laboratory_run_id"])
    work = WorkLog.objects.select_for_update().get(pk=worklog_id)
    if for_expense: ensure_journal_open(work)
    return work

def ensure_journal_open(work):
    validate_journal(work)
    validate_journal_samples(work, set(work.samples.values_list("pk", flat=True)))
    if work.task_id and Task.objects.get(pk=work.task_id).status == "Завершено":
        raise ValidationError("Новый расход завершённой задачи запрещён.")
    if work.laboratory_run_id and LaboratoryProtocolRun.objects.get(pk=work.laboratory_run_id).status == "closed":
        raise ValidationError("Новый расход закрытого выполнения запрещён.")

def validate_journal(work):
    if work.laboratory_run_id:
        run = work.laboratory_run
        if work.task_id != run.task_id: raise ValidationError("Задача журнала и выполнения различаются.")
        if run.status == "draft": raise ValidationError("Сначала начните выполнение методики.")
        if work._state.adding and run.status == "closed" and not work.correction_of_id:
            raise ValidationError("Закрытое выполнение допускает только отдельное исправление текста без расхода.")
        if work.correction_of_id and work.correction_of.laboratory_run_id != work.laboratory_run_id:
            raise ValidationError("Исправление должно относиться к тому же выполнению.")

def validate_journal_samples(work, sample_ids):
    if not sample_ids: return
    if not work.task_id or not set(sample_ids).issubset(set(work.task.samples.filter(projects__pk=work.task.project_id).values_list("pk", flat=True))):
        raise ValidationError("Пробы журнала не входят в задачу.")
    if work.laboratory_run_id and not set(sample_ids).issubset(set(work.laboratory_run.samples.values_list("pk", flat=True))):
        raise ValidationError("Пробы журнала не входят в выполнение.")

def has_unposted_expenses(journals):
    return WorkExpenseLine.objects.filter(worklog__in=journals).exclude(worklog__expense_request__status="posted").exists()

def validate_task_event(event):
    if event.action == "approve":
        if event.task.laboratory_runs.exclude(status="closed").exists():
            raise ValidationError("Есть незакрытые выполнения лабораторных методик.")
        if has_unposted_expenses(WorkLog.objects.filter(task_id=event.task_id)):
            raise ValidationError("Есть расход журнала без подтверждения. Закрытие задачи запрещено.")
    if event.action == "transfer" and event.task.laboratory_runs.exclude(status="closed").exists():
        raise ValidationError("Перед передачей задачи закройте её выполнения методик.")

@transaction.atomic
def start_run(run_id, user, day=None):
    run = lock_run(run_id)
    if not manages(user, run.task): raise PermissionDenied
    if run.status == "running": return False
    if run.status != "draft" or run.task.status != "В работе":
        raise ValidationError("Начало доступно черновику, когда задача принята в работу.")
    day = day or timezone.localdate()
    if day > timezone.localdate(): raise ValidationError("Начало не может быть будущим.")
    protocol = LaboratoryProtocol.objects.select_for_update().get(pk=run.protocol_id)
    if not protocol.active: raise ValidationError("Версия методики отключена.")
    ids = set(run.samples.values_list("pk", flat=True))
    allowed = set(run.task.samples.filter(projects__pk=run.task.project_id).values_list("pk", flat=True))
    if not ids.issubset(allowed): raise ValidationError("Состав проб не соответствует задаче.")
    snapshot = {name:getattr(protocol, name) for name in ("code","title","version","method_kind","instructions")}
    LaboratoryProtocolRun.objects.filter(pk=run.pk).update(status="running", started_on=day, protocol_snapshot=snapshot)
    record(user, run, {"действие":"Начало выполнения", "дата":day, "снимок":snapshot, "пробы":sorted(ids)})
    return True

@transaction.atomic
def close_run(run_id, user, result, day=None):
    run = lock_run(run_id)
    if not manages(user, run.task): raise PermissionDenied
    if run.status == "closed": return False
    if run.status != "running": raise ValidationError("Выполнение ещё не начато.")
    day = day or timezone.localdate()
    if not run.started_on or day < run.started_on or day > timezone.localdate(): raise ValidationError("Некорректная дата завершения.")
    if not result.strip(): raise ValidationError("Укажите результат выполнения.")
    journals = WorkLog.objects.filter(laboratory_run=run)
    if has_unposted_expenses(journals): raise ValidationError("Расход выполнения ещё не подтверждён.")
    LaboratoryProtocolRun.objects.filter(pk=run.pk).update(status="closed", finished_on=day, result=result.strip(), closed_by=user, closed_at=timezone.now())
    record(user, run, {"действие":"Закрытие выполнения без списания", "результат":result.strip(), "дата":day})
    return True


def plan_metrics(plans):
    plans = list(plans)
    tasks = {p.task_id for p in plans}; lots = {p.lot_id for p in plans}
    from .models import Lot
    current_lots = Lot.objects.in_bulk(lots)
    spent = defaultdict(lambda:ZERO); own = defaultdict(lambda:ZERO); pending=defaultdict(lambda:ZERO)
    for row in Movement.objects.filter(task_id__in=tasks, lot_id__in=lots, kind__in=("use_free","use_reserved","reserve","release")).values("task_id","lot_id","kind").annotate(total=Sum("qty")):
        key=(row["task_id"],row["lot_id"]); kind=row["kind"]; qty=row["total"]
        if kind in ("use_free","use_reserved"): spent[key]+=qty
        if kind == "reserve": own[key]+=qty
        if kind in ("release","use_reserved"): own[key]-=qty
    for row in WorkExpenseLine.objects.filter(worklog__task_id__in=tasks,lot_id__in=lots,worklog__expense_request__status="pending").values("worklog__task_id","lot_id").annotate(total=Sum("qty")):
        pending[(row["worklog__task_id"],row["lot_id"])]+=row["total"]
    reserve_totals=defaultdict(lambda:ZERO)
    for row in Movement.objects.filter(lot_id__in=lots,kind__in=("reserve","release","use_reserved")).values("lot_id","kind").annotate(total=Sum("qty")):
        reserve_totals[row["lot_id"]]+=row["total"] if row["kind"]=="reserve" else -row["total"]
    result=[]
    for p in plans:
        key=(p.task_id,p.lot_id); lot=current_lots[p.lot_id]
        p.lot=lot
        usable=lot.released and not lot.expired and p.unit_snapshot==lot.unit and reserve_totals[lot.pk]==lot.reserved
        remaining=max(p.planned_qty-spent[key],ZERO)
        reserve=max(own[key],ZERO) if usable else ZERO
        result.append({"plan":p,"spent":spent[key],"remaining":remaining,"overrun":max(spent[key]-p.planned_qty,ZERO),
            "own_reserve":own[key],"covered":min(remaining,reserve),"uncovered":max(remaining-reserve,ZERO),
            "pending":pending[key],"pending_over_plan":max(pending[key]-remaining,ZERO),"unit_mismatch":p.unit_snapshot!=lot.unit})
    return result
