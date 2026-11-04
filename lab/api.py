from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from .models import Lot, Movement, Project, Sample, StoragePlace, Task


def _require_staff(user):
    if not (user and user.is_authenticated and user.is_active and user.is_staff):
        raise PermissionDenied


@login_required(login_url="/admin/login/")
def dashboard_view(request):
    """Отдельная HTML-страница визуализации (фронтенд на Vue.js из папки frontend/)."""
    _require_staff(request.user)
    return render(request, "frontend/dashboard.html")


@login_required(login_url="/admin/login/")
def api_dashboard_data(request):
    """JSON API эндпоинт (/api/v1/dashboard/) для визуализации на Vue.js."""
    _require_staff(request.user)
    today = timezone.localdate()
    soon_date = today + timedelta(days=30)

    # 1. Баланс склада и оповещения
    lots = list(
        Lot.objects.select_related("category", "storage_place").order_by(
            "name", "code"
        )
    )
    movements = Movement.objects.values(
        "lot_id", "kind", "task_id", "worklog_id", "preparation_id"
    ).annotate(total_qty=Sum("qty"))

    stats_by_lot = {
        lot.pk: {
            "receipt_total": Decimal("0"),
            "used_on_tasks": Decimal("0"),
            "used_on_preparations": Decimal("0"),
            "writeoff_total": Decimal("0"),
        }
        for lot in lots
    }

    for m in movements:
        st = stats_by_lot.get(m["lot_id"])
        if st is None:
            continue
        qty = m["total_qty"] or Decimal("0")
        kind = m["kind"]
        if kind == "receipt":
            st["receipt_total"] += qty
        elif kind in ("use_reserved", "use_free"):
            if m["preparation_id"] and not m["task_id"] and not m["worklog_id"]:
                st["used_on_preparations"] += qty
            else:
                st["used_on_tasks"] += qty
        elif kind == "writeoff":
            st["writeoff_total"] += qty

    stock_items = []
    alerts_count = 0
    for lot in lots:
        st = stats_by_lot[lot.pk]
        is_low = lot.free < lot.minimum or lot.free <= 0
        is_expiring_soon = bool(
            lot.expiry and not lot.expired and today <= lot.expiry <= soon_date
        )
        if lot.expired or is_low or is_expiring_soon:
            alerts_count += 1

        if lot.expired:
            status = "expired"
            status_label = f"Просрочен ({lot.expiry})"
        elif not lot.released:
            status = "pending"
            status_label = "Ожидает допуска"
        elif lot.free <= 0:
            status = "empty"
            status_label = "Нет в наличии"
        elif lot.free < lot.minimum:
            status = "low"
            status_label = "Ниже минимума"
        else:
            status = "ok"
            status_label = "В норме"

        stock_items.append(
            {
                "id": lot.pk,
                "code": lot.code,
                "name": lot.name,
                "category": lot.category.name if lot.category else "—",
                "unit": lot.unit,
                "storage_place": (
                    lot.storage_place.name
                    if lot.storage_place
                    else (lot.location or "—")
                ),
                "receipt_total": float(st["receipt_total"]),
                "used_on_tasks": float(st["used_on_tasks"]),
                "used_on_preparations": float(st["used_on_preparations"]),
                "writeoff_total": float(st["writeoff_total"]),
                "reserved": float(lot.reserved),
                "quantity": float(lot.quantity),
                "free": float(lot.free),
                "minimum": float(lot.minimum),
                "expiry": lot.expiry.isoformat() if lot.expiry else None,
                "status": status,
                "status_label": status_label,
            }
        )

    # 2. Статистика по местам хранения и пробам
    places = StoragePlace.objects.filter(active=True).order_by("sort_order", "name")
    storage_stats = []
    for p in places:
        qs = Sample.objects.filter(Q(storage_place_id=p.pk) | Q(location=p.name))
        by_kind = list(
            qs.values("kind").annotate(count=Count("id")).order_by("-count")
        )
        storage_stats.append(
            {
                "id": p.pk,
                "name": p.name,
                "total_samples": qs.count(),
                "by_kind": by_kind,
            }
        )

    samples_by_kind = list(
        Sample.objects.values("kind")
        .annotate(count=Count("id"))
        .order_by("-count", "kind")
    )

    # 3. Задачи и этапы (до ПЦР, Real-Time, ФА, Секвенирования)
    tasks_qs = (
        Task.objects.select_related("project", "owner", "reviewer")
        .prefetch_related("samples")
        .order_by("due", "-id")[:50]
    )
    tasks_data = [
        {
            "id": t.pk,
            "title": t.title,
            "project": t.project.title,
            "owner": t.owner.username,
            "stage": t.stage,
            "status": t.status,
            "due": t.due.isoformat(),
            "overdue": t.status != "Завершено" and t.due < today,
            "samples_count": t.samples.count(),
        }
        for t in tasks_qs
    ]

    summary = {
        "projects_count": Project.objects.count(),
        "samples_count": Sample.objects.count(),
        "active_tasks_count": Task.objects.exclude(status="Завершено").count(),
        "overdue_tasks_count": Task.objects.exclude(status="Завершено")
        .filter(due__lt=today)
        .count(),
        "lots_count": len(lots),
        "alerts_count": alerts_count,
    }

    return JsonResponse(
        {
            "summary": summary,
            "stock_balance": stock_items,
            "storage_stats": storage_stats,
            "samples_by_kind": samples_by_kind,
            "tasks": tasks_data,
        }
    )
