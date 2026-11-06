import json
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone
from .models import Audit, Lot, Movement, WorkLog, WorkExpenseRequest
from .worklog6_permissions import can_post


def record(user, obj, detail):
    Audit.objects.create(actor=user, entity=obj._meta.label, object_id=str(obj.pk),
        detail=json.dumps(detail, cls=DjangoJSONEncoder, ensure_ascii=False))


def lock_pending(worklog_id):
    if not worklog_id:
        raise ValidationError("Сначала сохраните запись журнала.")
    from .lab6b2_services import lock_journal
    worklog = lock_journal(worklog_id, for_expense=True)
    request, _ = WorkExpenseRequest.objects.get_or_create(worklog=worklog)
    request = WorkExpenseRequest.objects.select_for_update().get(pk=request.pk)
    if request.status == "posted":
        raise ValidationError("Проведённый расход нельзя менять. Нужна отдельная корректировка.")
    return worklog, request


@transaction.atomic
def post_expense(worklog_id, user):
    if not can_post(user):
        raise PermissionDenied("Нет права проводить расход; стажёру всегда требуется подтверждение.")
    from .lab6b2_services import lock_journal
    worklog = lock_journal(worklog_id, for_expense=False)
    request, _ = WorkExpenseRequest.objects.get_or_create(worklog=worklog)
    request = WorkExpenseRequest.objects.select_for_update().get(pk=request.pk)
    if request.status == "posted":
        return False
    from .lab6b2_services import ensure_journal_open
    ensure_journal_open(worklog)
    lines = list(worklog.expense_lines.select_for_update().order_by("lot_id", "pk"))
    if not lines:
        raise ValidationError("В записи нет строк расхода.")
    # All lots are locked in the same order before Movement.save takes its own locks.
    list(Lot.objects.select_for_update().filter(pk__in={line.lot_id for line in lines}).order_by("pk"))
    if worklog.actual_day > timezone.localdate():
        raise ValidationError("Дата работы не может быть будущей.")
    selected = set(worklog.samples.values_list("pk", flat=True))
    if selected:
        allowed = set(worklog.task.samples.filter(projects__pk=worklog.task.project_id).values_list("pk", flat=True)) if worklog.task_id else set()
        if not selected.issubset(allowed):
            raise ValidationError("Обработанные пробы не соответствуют задаче.")
    movement_ids = []
    for line in lines:
        line.full_clean()
        movement = Movement(
            lot_id=line.lot_id, kind=line.mode, qty=line.qty,
            task_id=worklog.task_id, actual_day=worklog.actual_day, actor=user,
            worklog=worklog, expense_line=line,
            note=f"Журнал №{worklog.pk}; строка расхода №{line.pk}; {worklog.operation}",
        )
        movement.save()
        record(user, movement, {"действие": "Расход по журналу", "журнал": worklog.pk,
            "автор_работы": worklog.actor_id, "строка": line.pk, "количество": line.qty})
        movement_ids.append(movement.pk)
    request.status, request.posted_by, request.posted_at = "posted", user, timezone.now()
    request.save(update_fields=["status", "posted_by", "posted_at"])
    record(user, request, {"действие": "Проведение расхода", "журнал": worklog.pk,
        "автор_работы": worklog.actor_id, "операции": movement_ids})
    return True


def validate_movement_link(movement):
    has_work = bool(movement.worklog_id)
    has_line = bool(movement.expense_line_id)
    if has_work != has_line:
        raise ValidationError("Для расхода по журналу нужны и запись, и строка расхода.")
    if not has_line:
        return
    if not can_post(movement.actor):
        raise ValidationError("Нет разрешения на проведение расхода по журналу.")
    line = movement.expense_line
    work = line.worklog
    if (movement.worklog_id != line.worklog_id or movement.lot_id != line.lot_id
            or movement.qty != line.qty or movement.kind != line.mode
            or movement.task_id != work.task_id or movement.actual_day != work.actual_day):
        raise ValidationError("Складская операция не соответствует строке журнала.")
    if work.expense_request.status != "pending":
        raise ValidationError("Расход уже проведён.")
