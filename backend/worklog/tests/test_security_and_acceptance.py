from decimal import Decimal
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, RequestFactory, override_settings
from django.utils import timezone

from lab.admin import safe_cell
from lab.models import (
    Project, Task, TaskEvent, Lot, Preparation, BufferName,
    PreparationProtocol, PreparationProtocolComponent, Component,
    ProductCategory, StoragePlace, ElectrophoresisGel, ElectrophoresisLane, Sample,
)


class SecurityAndModularChecklistTests(TestCase):
    def setUp(self):
        call_command("bootstrap")
        User = get_user_model()
        self.lead_user = User.objects.create_user(username="check_lead", password="StrongPassword123!", is_staff=True)
        self.warehouse_user = User.objects.create_user(username="check_stock", password="StrongPassword123!", is_staff=True)
        self.employee_user = User.objects.create_user(username="check_emp", password="StrongPassword123!", is_staff=True)

        self.lead_group = Group.objects.get(name="Руководители")
        self.warehouse_group = Group.objects.get(name="Склад")
        self.employee_group = Group.objects.get(name="Исполнители")

        self.lead_user.groups.add(self.lead_group)
        self.warehouse_user.groups.add(self.warehouse_group)
        self.employee_user.groups.add(self.employee_group)

    def test_issue_4_removed_lead_cannot_approve_task(self):
        project = Project.objects.create(title="P1", kind="Проект", manager=self.lead_user)
        task = Task.objects.create(
            title="T1", project=project, owner=self.employee_user,
            reviewer=self.lead_user, due=timezone.localdate()
        )
        TaskEvent.objects.create(task=task, actor=self.employee_user, action="accept")
        TaskEvent.objects.create(task=task, actor=self.employee_user, action="submit", note="Готово")

        self.lead_user.groups.remove(self.lead_group)

        with self.assertRaises(ValidationError):
            TaskEvent.objects.create(task=task, actor=self.lead_user, action="approve")
        task.refresh_from_db()
        self.assertEqual(task.status, "На проверке")

    def test_issue_5_regular_staff_cannot_create_preparation_or_writeoff_stock(self):
        req = RequestFactory().get("/admin/lab/preparation/add/")
        req.user = self.employee_user
        prep_admin = admin.site._registry[Preparation]
        self.assertFalse(prep_admin.has_add_permission(req))

        req.user = self.warehouse_user
        self.assertTrue(prep_admin.has_add_permission(req))

        cat = ProductCategory.objects.create(name="Реактивы-Тест")
        place = StoragePlace.objects.create(name="Шкаф-Тест")
        buf = BufferName.objects.create(name="TAE 1x Тест")
        proto = PreparationProtocol.objects.create(product=buf, title="СОП-1", base_volume_ml=100, instructions="mix")
        comp_spec = PreparationProtocolComponent.objects.create(protocol=proto, reagent_name="H2O", quantity=100, unit="ml")
        lot = Lot.objects.create(name="H2O", unit="мл", quantity=500, released=True, category=cat, storage_place=place)
        comp_spec.allowed_lots.add(lot)

        prep = Preparation.objects.create(
            actor=self.employee_user, actual_day=timezone.localdate(),
            kind="Приготовление реактива", protocol="СОП-1", recipe=proto,
            planned_volume_ml=100, actual_volume_ml=100, qty=100,
            output_category=cat, output_storage_place=place,
        )
        Component.objects.create(preparation=prep, recipe_component=comp_spec, lot=lot, qty=Decimal("100"))

        with self.assertRaises(ValidationError):
            prep.complete(user=self.employee_user)
        lot.refresh_from_db()
        self.assertEqual(lot.quantity, Decimal("500"))

        prep.complete(user=self.warehouse_user)
        lot.refresh_from_db()
        self.assertEqual(lot.quantity, Decimal("400"))

    def test_issue_6_csv_formula_protection_catches_tab_and_cr(self):
        for payload in ("=1+1", "+1+1", "-1+1", "@SUM(A1)", "\t=1+1", "\r=1+1", "\n=1+1", "\tcmd", "\rcmd", "  \t=SUM(1)"):
            escaped = safe_cell(payload)
            self.assertTrue(escaped.startswith("'"), f"Failed to escape {payload!r}")

    @override_settings(ALLOWED_HOSTS=["crm.lab.local"])
    def test_issue_7_healthz_works_with_strict_domain_host(self):
        response = self.client.get("/healthz/", HTTP_HOST="crm.lab.local", HTTP_X_FORWARDED_PROTO="https")
        self.assertEqual(response.status_code, 200)

    def test_electrophoresis_domain_and_api_endpoints(self):
        project = Project.objects.create(title="Форез-Проект", kind="Проект", manager=self.lead_user)
        task = Task.objects.create(title="Форез-Задача", project=project, owner=self.employee_user, reviewer=self.lead_user, due=timezone.localdate())
        sample = Sample.objects.create(series="EL", number="1", project=project, kind="ДНК")
        gel = ElectrophoresisGel.objects.create(actor=self.employee_user, task=task, agarose_percent=Decimal("1.50"), voltage_v=120, duration_min=30)
        lane = ElectrophoresisLane.objects.create(gel=gel, lane_number=1, sample=sample, band_size_bp="450")
        self.assertEqual(gel.lanes.count(), 1)
        self.assertEqual(lane.band_size_bp, "450")
        res = self.client.get("/api/electrophoresis/")
        self.assertEqual(res.status_code, 200)
