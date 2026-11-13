from django.db.models import F
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from lab.models import (
    Project, Task, Sample, Lot, StoragePlace,
    PreparationProtocol, Preparation, Autoclaving,
    LaboratoryProtocol, LaboratoryProtocolRun, SourceBlock,
    WorkLog, ElectrophoresisGel, ElectrophoresisLane,
)


def _cors_json(data, status=200):
    resp = JsonResponse(data, status=status, json_dumps_params={"ensure_ascii": False})
    resp["Access-Control-Allow-Origin"] = "*"
    resp["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp["Access-Control-Allow-Headers"] = "Content-Type, X-CSRFToken"
    return resp


@require_http_methods(["GET"])
def healthz_view(request):
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return _cors_json({"status": "ok", "framework": "Django 5.2 + Vue 3"})


@require_http_methods(["GET"])
def api_overview(request):
    total_lots = Lot.objects.count()
    low_stock_lots = Lot.objects.filter(quantity__lt=F("reserved") + F("minimum")).count()
    return _cors_json({
        "project": "LabCRM — Молекулярная селекция и ДНК-паспортизация",
        "backend": "Django 5.2 (REST JSON API)",
        "frontend": "Vue 3 (Vite + Pinia + Vue Router)",
        "modules": [
            {
                "key": "warehouse",
                "title": "Склад и хранение",
                "folder": "lab/domains/warehouse/ ↔ frontend/src/modules/warehouse/",
                "count": total_lots,
                "subtitle": f"Партий на складе (ниже минимума: {low_stock_lots})",
                "places_count": StoragePlace.objects.filter(active=True).count(),
            },
            {
                "key": "solutions",
                "title": "Приготовление растворов",
                "folder": "lab/domains/solutions/ ↔ frontend/src/modules/solutions/",
                "count": Preparation.objects.count(),
                "subtitle": f"Протоколов СОП: {PreparationProtocol.objects.filter(active=True).count()}",
                "autoclaving_count": Autoclaving.objects.count(),
            },
            {
                "key": "dna_extraction",
                "title": "Выделение ДНК и ПЦР",
                "folder": "lab/domains/dna_extraction/ ↔ frontend/src/modules/dna_extraction/",
                "count": LaboratoryProtocolRun.objects.count(),
                "subtitle": f"Активных методик: {LaboratoryProtocol.objects.filter(active=True).count()}",
            },
            {
                "key": "electrophoresis",
                "title": "Форезы (Электрофорез)",
                "folder": "lab/domains/electrophoresis/ ↔ frontend/src/modules/electrophoresis/",
                "count": ElectrophoresisGel.objects.count(),
                "subtitle": f"Дорожек геля: {ElectrophoresisLane.objects.count()}",
            },
            {
                "key": "samples",
                "title": "Образцы, Паспорта ВИР и Блоки 96",
                "folder": "lab/domains/samples/ ↔ frontend/src/modules/samples/",
                "count": Sample.objects.count(),
                "subtitle": f"Планшетов A1–H12: {SourceBlock.objects.count()}",
            },
            {
                "key": "worklog",
                "title": "Проекты, Задачи и Журнал работ",
                "folder": "lab/domains/worklog/ ↔ frontend/src/modules/worklog/",
                "count": Task.objects.count(),
                "subtitle": f"Проектов: {Project.objects.count()} · Записей журнала: {WorkLog.objects.count()}",
            },
        ],
    })


@require_http_methods(["GET"])
def api_warehouse(request):
    lots = [
        {
            "id": l.pk,
            "code": l.code,
            "name": l.name,
            "category": l.category.name if l.category_id else "—",
            "unit": l.unit,
            "quantity": str(l.quantity),
            "reserved": str(l.reserved),
            "free": str(l.free),
            "minimum": str(l.minimum),
            "storage_place": l.storage_place.name if l.storage_place_id else l.location,
            "expiry": l.expiry.isoformat() if l.expiry else "бессрочно",
            "released": l.released,
            "expired": l.expired,
            "low_stock": l.free < l.minimum,
        }
        for l in Lot.objects.select_related("category", "storage_place").order_by("name", "pk")[:100]
    ]
    places = [
        {
            "id": p.pk,
            "name": p.name,
            "parent": p.parent.name if p.parent_id else None,
            "active": p.active,
        }
        for p in StoragePlace.objects.select_related("parent").order_by("sort_order", "name")
    ]
    return _cors_json({"lots": lots, "places": places})


@require_http_methods(["GET"])
def api_solutions(request):
    protocols = [
        {
            "id": p.pk,
            "title": p.title,
            "version": p.version,
            "product": str(p.product),
            "base_volume_ml": str(p.base_volume_ml),
            "target_ph": p.target_ph or "—",
            "requires_autoclaving": p.product.requires_autoclaving,
            "active": p.active,
        }
        for p in PreparationProtocol.objects.select_related("product").order_by("product__name", "title")
    ]
    preparations = [
        {
            "id": prep.pk,
            "actual_day": prep.actual_day.isoformat(),
            "protocol": prep.protocol,
            "planned_volume_ml": str(prep.planned_volume_ml) if prep.planned_volume_ml else None,
            "actual_volume_ml": str(prep.actual_volume_ml) if prep.actual_volume_ml else None,
            "qty": str(prep.qty),
            "processed": prep.processed,
            "autoclaving_required": prep.autoclaving_required,
            "autoclaving_done": prep.autoclaving_done,
            "approved": prep.approved_at is not None,
            "actor": prep.actor.username if prep.actor_id else "—",
            "output_code": prep.output.code if prep.output_id else None,
        }
        for prep in Preparation.objects.select_related("actor", "output").order_by("-actual_day", "-pk")[:100]
    ]
    return _cors_json({"protocols": protocols, "preparations": preparations})


@require_http_methods(["GET"])
def api_dna_extraction(request):
    protocols = [
        {
            "id": p.pk,
            "code": p.code,
            "title": p.title,
            "version": p.version,
            "method_kind": p.get_method_kind_display(),
            "active": p.active,
        }
        for p in LaboratoryProtocol.objects.order_by("title", "version")
    ]
    runs = [
        {
            "id": r.pk,
            "task": str(r.task),
            "protocol": str(r.protocol),
            "executor": r.executor.username if r.executor_id else "—",
            "status": r.get_status_display(),
            "status_code": r.status,
            "started_on": r.started_on.isoformat() if r.started_on else "—",
            "finished_on": r.finished_on.isoformat() if r.finished_on else "—",
            "samples_count": r.samples.count(),
            "result": r.result,
        }
        for r in LaboratoryProtocolRun.objects.select_related("task", "protocol", "executor").order_by("-created_at")[:100]
    ]
    return _cors_json({"protocols": protocols, "runs": runs})


@require_http_methods(["GET"])
def api_electrophoresis(request):
    gels = [
        {
            "id": g.pk,
            "actual_day": g.actual_day.isoformat(),
            "task": str(g.task),
            "laboratory_run": str(g.laboratory_run) if g.laboratory_run_id else "—",
            "buffer_lot": str(g.buffer_lot) if g.buffer_lot_id else "—",
            "agarose_percent": str(g.agarose_percent),
            "voltage_v": g.voltage_v,
            "duration_min": g.duration_min,
            "marker_name": g.marker_name or "—",
            "status": g.get_status_display(),
            "result_summary": g.result_summary,
            "actor": g.actor.username if g.actor_id else "—",
            "lanes": [
                {
                    "lane_number": lane.lane_number,
                    "sample": lane.sample.code if lane.sample_id else "Контроль/Маркер",
                    "is_control": lane.is_control,
                    "sample_volume_ul": str(lane.sample_volume_ul),
                    "band_size_bp": lane.band_size_bp or "—",
                    "quality": lane.get_quality_display(),
                    "quality_code": lane.quality,
                    "note": lane.note,
                }
                for lane in g.lanes.select_related("sample").order_by("lane_number")
            ],
        }
        for g in ElectrophoresisGel.objects.select_related("task", "laboratory_run", "buffer_lot", "actor").order_by("-actual_day", "-pk")[:50]
    ]
    return _cors_json({"gels": gels})


@require_http_methods(["GET"])
def api_samples(request):
    samples = [
        {
            "id": s.pk,
            "code": s.code,
            "series": s.series,
            "number": s.number,
            "kind": s.kind,
            "sample_type": s.get_sample_type_display() if s.sample_type else "—",
            "storage_place": s.storage_place.name if s.storage_place_id else (s.location or "—"),
            "vir_number": s.material_passport.vir_catalogue_number if s.material_passport_id else "—",
            "species": s.material_passport.species if s.material_passport_id else "—",
        }
        for s in Sample.objects.select_related("storage_place", "material_passport").order_by("series", "number")[:100]
    ]
    blocks = [
        {
            "id": b.pk,
            "name": b.name,
            "created_on": b.created_on.isoformat(),
            "actor": b.actor.username if b.actor_id else "—",
            "storage_place": b.storage_place.name if b.storage_place_id else "—",
            "dna_volume_ul": str(b.dna_volume_ul),
            "water_volume_ul": str(b.water_volume_ul),
            "wells_count": b.wells.count(),
        }
        for b in SourceBlock.objects.select_related("actor", "storage_place").order_by("-created_on", "-pk")[:50]
    ]
    return _cors_json({"samples": samples, "blocks": blocks})


@require_http_methods(["GET"])
def api_worklog(request):
    tasks = [
        {
            "id": t.pk,
            "title": t.title,
            "project": t.project.title,
            "owner": t.owner.username,
            "reviewer": t.reviewer.username,
            "stage": t.stage,
            "status": t.status,
            "due": t.due.isoformat(),
        }
        for t in Task.objects.select_related("project", "owner", "reviewer").order_by("-pk")[:100]
    ]
    logs = [
        {
            "id": w.pk,
            "actual_day": w.actual_day.isoformat(),
            "actor": w.actor.username,
            "task": str(w.task) if w.task_id else "—",
            "operation": w.operation,
            "detail": w.detail,
        }
        for w in WorkLog.objects.select_related("actor", "task").order_by("-actual_day", "-pk")[:100]
    ]
    return _cors_json({"tasks": tasks, "worklogs": logs})
