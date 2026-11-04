import json
from datetime import date, datetime, time

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from .models import (
    Task, TaskEvent, WorkLog, Lot, Movement, Preparation, Report
)


def table(queryset, columns):
    values = queryset.values(*columns.keys())
    return [
        {label: row[field] for field, label in columns.items()}
        for row in values
    ]


def build_report(year, quarter):
    if quarter not in (1, 2, 3, 4):
        raise ValidationError("Неверный квартал.")

    start = date(year, (quarter - 1) * 3 + 1, 1)
    end = (
        date(year + 1, 1, 1)
        if quarter == 4
        else date(year, quarter * 3 + 1, 1)
    )
    if end > timezone.localdate():
        raise ValidationError("Квартал ещё не завершён.")

    begin_dt = timezone.make_aware(datetime.combine(start, time.min))
    end_dt = timezone.make_aware(datetime.combine(end, time.min))

    payload = {
        "Период": f"{start.isoformat()} — {end.isoformat()} (конец не включён)",
        "Снимок сформирован": timezone.now().isoformat(),

        "События задач за квартал": table(
            TaskEvent.objects.filter(
                created_at__gte=begin_dt, created_at__lt=end_dt
            ).order_by("created_at"),
            {
                "created_at": "Когда внесено",
                "task_id": "Задача",
                "task__project__title": "Проект",
                "actor__username": "Сотрудник",
                "action": "Действие",
                "note": "Результат / причина",
                "next_action": "Следующее действие",
                "target__username": "Ответственный",
                "expected": "Ожидаемая дата",
            }
        ),

        "Работы по дате выполнения": table(
            WorkLog.objects.filter(
                actual_day__gte=start, actual_day__lt=end
            ).order_by("actual_day", "created_at"),
            {
                "id": "Запись",
                "actual_day": "Дата работы",
                "created_at": "Дата внесения",
                "actor__username": "Исполнитель",
                "task_id": "Задача",
                "operation": "Работа",
                "detail": "Результат",
                "correction_of_id": "Исправляет запись",
            }
        ),

        "Движение материалов по дате выполнения": table(
            Movement.objects.filter(
                actual_day__gte=start, actual_day__lt=end
            ).order_by("actual_day", "created_at"),
            {
                "actual_day": "Дата",
                "created_at": "Дата внесения",
                "actor__username": "Сотрудник",
                "lot__code": "Партия",
                "lot__name": "Материал",
                "kind": "Операция",
                "qty": "Количество",
                "lot__unit": "Единица",
                "task_id": "Задача",
                "preparation_id": "Подготовка",
                "note": "Основание",
            }
        ),

        "Приготовление и автоклавирование": table(
            Preparation.objects.filter(
                actual_day__gte=start, actual_day__lt=end
            ).order_by("actual_day"),
            {
                "actual_day": "Дата",
                "actor__username": "Исполнитель",
                "kind": "Вид подготовки",
                "output__code": "Готовая партия",
                "qty": "Количество",
                "output__unit": "Единица",
                "protocol": "Протокол",
                "approved_by__username": "Кто допустил",
                "approved_at": "Дата допуска",
            }
        ),

        "Задачи на момент формирования — НЕ на конец квартала": table(
            Task.objects.order_by("project_id", "id"),
            {
                "id": "Задача",
                "project__title": "Проект",
                "title": "Название",
                "owner__username": "Исполнитель",
                "stage": "Этап",
                "status": "Состояние",
                "due": "Срок",
                "blocker": "Причина остановки",
                "next_action": "Следующее действие",
                "next_actor__username": "Кто устраняет",
                "expected": "Ожидаемая дата",
            }
        ),

        "Склад на момент формирования — НЕ на конец квартала": table(
            Lot.objects.order_by("name", "code"),
            {
                "code": "Партия",
                "name": "Материал",
                "unit": "Единица",
                "quantity": "Физический остаток",
                "reserved": "Резерв",
                "minimum": "Минимум свободного остатка",
                "location": "Хранение",
                "expiry": "Годен до",
                "released": "Допущена",
            }
        ),
    }

    serializable = json.loads(
        json.dumps(payload, cls=DjangoJSONEncoder, ensure_ascii=False)
    )
    report, _ = Report.objects.get_or_create(
        year=year, quarter=quarter,
        defaults={"payload": serializable},
    )
    return report


def send_approved_reports():
    if not settings.EMAIL_HOST or not settings.REPORT_RECIPIENTS:
        return

    ids = list(
        Report.objects.filter(
            approved_at__isnull=False, sent_at__isnull=True
        ).values_list("pk", flat=True)
    )

    for pk in ids:
        with transaction.atomic():
            report = Report.objects.select_for_update().get(pk=pk)
            if not report.approved_at or report.sent_at:
                continue

            message = EmailMessage(
                subject=f"Лаборатория: отчёт {report}",
                body=(
                    "Утверждённый квартальный отчёт приложен в JSON.\n"
                    "События за период и текущие снимки разделены."
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=settings.REPORT_RECIPIENTS,
            )
            message.attach(
                f"lab-{report.year}-q{report.quarter}.json",
                json.dumps(report.payload, ensure_ascii=False, indent=2),
                "application/json",
            )
            if message.send(fail_silently=False) != 1:
                raise RuntimeError("Почтовый сервер не принял сообщение.")

            report.sent_at = timezone.now()
            report.save(update_fields=["sent_at"])
