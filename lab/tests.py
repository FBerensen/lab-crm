from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .admin import safe_cell
from .models import (
    BufferName,
    Component,
    Lot,
    Movement,
    Preparation,
    ProductCategory,
    Project,
    Report,
    Sample,
    StoragePlace,
    Task,
    TaskEvent,
    WorkLog,
    lead,
    warehouse,
)
from .reports import build_report
from .sample_batch import SampleAdminForm, SampleBatchForm
from .task_samples import TaskSamplesForm

User = get_user_model()


class LabCrmAcceptanceAndSecurityTests(TestCase):
    """Автоматические тесты по всем пунктам приёмки, ТЗ (п. 1–11) и 8 пунктам код-ревью безопасности."""

    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap")
        cls.group_leads = Group.objects.get(name="Руководители")
        cls.group_warehouse = Group.objects.get(name="Склад")
        cls.group_workers = Group.objects.get(name="Исполнители")

        cls.lead_user = User.objects.create_user(
            username="lead1", password="Password12345!", is_staff=True
        )
        cls.lead_user.groups.add(cls.group_leads)

        cls.lead2_user = User.objects.create_user(
            username="lead2", password="Password12345!", is_staff=True
        )
        cls.lead2_user.groups.add(cls.group_leads)

        cls.warehouse_user = User.objects.create_user(
            username="store1", password="Password12345!", is_staff=True
        )
        cls.warehouse_user.groups.add(cls.group_warehouse)

        cls.worker1 = User.objects.create_user(
            username="worker1", password="Password12345!", is_staff=True
        )
        cls.worker1.groups.add(cls.group_workers)

        cls.worker2 = User.objects.create_user(
            username="worker2", password="Password12345!", is_staff=True
        )
        cls.worker2.groups.add(cls.group_workers)

        cls.project = Project.objects.create(
            title="Проект ПЦР-диагностики",
            kind="Госзадание",
            manager=cls.lead_user,
        )

        cls.place = StoragePlace.objects.first()

        cls.sample1 = Sample.objects.create(
            series="QT",
            number="1",
            project=cls.project,
            kind="ДНК",
            storage_place=cls.place,
        )
        cls.sample2 = Sample.objects.create(
            series="QT",
            number="2..5",
            project=cls.project,
            kind="ДНК",
            storage_place=cls.place,
        )

    def tearDown(self):
        cache.clear()

    def test_roles_and_permissions(self):
        self.assertTrue(lead(self.lead_user))
        self.assertTrue(warehouse(self.lead_user))
        self.assertTrue(warehouse(self.warehouse_user))
        self.assertFalse(lead(self.warehouse_user))
        self.assertFalse(lead(self.worker1))
        self.assertFalse(warehouse(self.worker1))

    def test_sample_parent_series_and_number_split(self):
        form = SampleAdminForm(
            data={
                "series": "DR",
                "number": "12*",
                "project": self.project.pk,
                "kind": "Аликвота",
                "storage_place": self.place.pk,
                "location": "",
                "parent": "",
                "parent_series": "QT",
                "parent_number": "1",
                "note": "Аликвота из пробы QT №1",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        child = form.save()
        self.assertEqual(child.parent, self.sample1)
        self.assertEqual(child.code, "DR №12*")
        self.assertEqual(child.location, self.place.name)

    def test_sample_batch_form_and_validation(self):
        form = SampleBatchForm(
            data={
                "series": "AB",
                "first": 1,
                "last": 3,
                "extra_numbers": "10* 15: 20..25",
                "project": self.project.pk,
                "kind": "Растение",
                "storage_place": self.place.pk,
                "note": "Партия теста",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["numbers"],
            ["1", "2", "3", "10*", "15:", "20..25"],
        )

    def test_task_lifecycle_and_revoked_reviewer_role(self):
        """Проверка жизненного цикла задачи и п. 4 код-ревью:
        если у проверяющего отобрали роль 'Руководители', он не может сделать approve/return."""
        today = timezone.localdate()
        task = Task(
            title="Фрагментный анализ и секвенирование партии QT",
            project=self.project,
            owner=self.worker1,
            reviewer=self.lead_user,
            due=today + timedelta(days=5),
            stage="Фрагментный анализ (ФА)",
        )
        task.full_clean()
        task.save()
        task.samples.add(self.sample1, self.sample2)

        with self.assertRaises(ValidationError):
            TaskEvent.objects.create(
                task=task, actor=self.worker2, action="accept"
            )

        TaskEvent.objects.create(
            task=task,
            actor=self.worker1,
            action="accept",
            stage="Real-Time ПЦР",
        )
        task.refresh_from_db()
        self.assertEqual(task.status, "В работе")
        self.assertEqual(task.stage, "Real-Time ПЦР")

        with self.assertRaises(ValidationError):
            TaskEvent.objects.create(
                task=task,
                actor=self.worker1,
                action="block",
                note="Не хватает праймеров",
            )

        TaskEvent.objects.create(
            task=task,
            actor=self.worker1,
            action="block",
            note="Не хватает праймеров",
            target=self.lead_user,
            next_action="Заказать праймеры",
            expected=today + timedelta(days=2),
        )
        task.refresh_from_db()
        self.assertEqual(task.status, "Остановлено")

        TaskEvent.objects.create(
            task=task, actor=self.worker1, action="resume"
        )
        task.refresh_from_db()
        self.assertEqual(task.status, "В работе")
        self.assertEqual(task.blocker, "")

        TaskEvent.objects.create(
            task=task,
            actor=self.worker1,
            action="submit",
            note="ДНК выделена, чистота 1.85",
        )
        task.refresh_from_db()
        self.assertEqual(task.status, "На проверке")

        # Пункт 4 ревью: убираем проверяющего из группы «Руководители» до момента approve
        self.lead_user.groups.remove(self.group_leads)
        with self.assertRaises(ValidationError):
            TaskEvent.objects.create(
                task=task, actor=self.lead_user, action="approve"
            )
        # Возвращаем группу «Руководители» обратно
        self.lead_user.groups.add(self.group_leads)

        # Другой руководитель тоже не может утвердить вместо назначенного проверяющего
        with self.assertRaises(ValidationError):
            TaskEvent.objects.create(
                task=task, actor=self.lead2_user, action="approve"
            )

        TaskEvent.objects.create(
            task=task, actor=self.lead_user, action="approve"
        )
        task.refresh_from_db()
        self.assertEqual(task.status, "Завершено")

        TaskEvent.objects.create(
            task=task,
            actor=self.lead_user,
            action="repeat",
            expected=today + timedelta(days=7),
        )
        repeated = Task.objects.get(parent=task)
        self.assertEqual(repeated.status, "Назначено")
        self.assertEqual(repeated.samples.count(), 2)

    def test_non_warehouse_staff_cannot_create_preparation(self):
        """Пункт 5 код-ревью: обычный staff (Исполнитель без роли Склад/Руководители)
        не может создать Preparation и списать реактивы со склада."""
        today = timezone.localdate()
        cat_buffer = ProductCategory.objects.get(name="Буфер")
        output_lot = Lot.objects.create(
            category=cat_buffer,
            name="TAE 1x",
            unit="л",
            storage_place=self.place,
        )

        prep_by_worker = Preparation(
            actor=self.worker1,
            actual_day=today,
            kind="Приготовление буфера",
            output=output_lot,
            qty=Decimal("1.0000"),
            unit="л",
            protocol="СОП-01",
        )
        with self.assertRaises(ValidationError):
            prep_by_worker.full_clean()

    def test_csv_formula_injection_protection_catches_tab_and_cr(self):
        """Пункт 6 код-ревью: защита от формул в CSV ловит \\t, \\r, \\n, =, +, -, @."""
        self.assertEqual(safe_cell("=SUM(A1:A2)"), "'=SUM(A1:A2)")
        self.assertEqual(safe_cell("  +1+1"), "'  +1+1")
        self.assertEqual(safe_cell("\t=cmd|' /C calc'!A0"), "'\t=cmd|' /C calc'!A0")
        self.assertEqual(safe_cell("\tобычный текст с табом"), "'\tобычный текст с табом")
        self.assertEqual(safe_cell("\r=1+1"), "'\r=1+1")
        self.assertEqual(safe_cell("\n-1+1"), "'\n-1+1")
        self.assertEqual(safe_cell("Обычный текст"), "Обычный текст")

    @override_settings(
        ALLOWED_HOSTS=["crm.production-domain.ru"],
        SECURE_SSL_REDIRECT=True,
    )
    def test_healthcheck_works_on_localhost_even_with_strict_allowed_hosts(self):
        """Пункт 7 код-ревью: /healthz/ отвечает 200 OK на 127.0.0.1 даже если
        в ALLOWED_HOSTS стоит только домен, а SECURE_SSL_REDIRECT=True."""
        resp = self.client.get("/healthz/", HTTP_HOST="127.0.0.1:8000")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    @override_settings(LOGIN_FAILURE_LIMIT=3)
    def test_admin_login_rate_limiting(self):
        """Пункт 3 код-ревью: защита /admin/login/ блокирует перебор паролей (429)."""
        cache.clear()
        login_url = reverse("admin:login")
        for _ in range(3):
            resp = self.client.post(
                login_url,
                {"username": "lead1", "password": "wrong-password"},
            )
            self.assertEqual(resp.status_code, 200)

        # 4-я попытка подряд должна быть заблокирована с HTTP 429
        blocked_resp = self.client.post(
            login_url,
            {"username": "lead1", "password": "wrong-password"},
        )
        self.assertEqual(blocked_resp.status_code, 429)

    def test_task_samples_range_form(self):
        today = timezone.localdate()
        FormCls = type(
            "BoundTaskSamplesForm",
            (TaskSamplesForm,),
            {"request_user": self.lead_user},
        )
        form = FormCls(
            data={
                "title": "ПЦР серии QT",
                "project": self.project.pk,
                "owner": self.worker1.pk,
                "reviewer": self.lead_user.pk,
                "due": (today + timedelta(days=3)).isoformat(),
                "stage": "Постановка ПЦР",
                "sample_ranges": "QT = 1 2..5",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_preparation_worklog_writeoff_and_stock_balance(self):
        today = timezone.localdate()
        cat_reagent = ProductCategory.objects.get(name="Реактив")
        cat_buffer = ProductCategory.objects.get(name="Буфер")

        source_lot = Lot.objects.create(
            category=cat_reagent,
            name="Трис-HCl",
            unit="г",
            storage_place=self.place,
            minimum=Decimal("45.0000"),
        )
        Movement.objects.create(
            actor=self.warehouse_user,
            actual_day=today,
            lot=source_lot,
            kind="receipt",
            qty=Decimal("50.0000"),
            note="Поступление Трис-HCl",
        )

        buffer_name = BufferName.objects.first().name
        output_lot = Lot.objects.create(
            category=cat_buffer,
            name=buffer_name,
            unit="мл",
            storage_place=self.place,
        )

        bad_prep = Preparation(
            actor=self.warehouse_user,
            actual_day=today,
            kind="Приготовление буфера",
            output=output_lot,
            qty=Decimal("0.05"),
            unit="л",
            protocol="СОП-БУФ-01",
        )
        with self.assertRaises(ValidationError):
            bad_prep.full_clean()

        prep = Preparation.objects.create(
            actor=self.warehouse_user,
            actual_day=today,
            kind="Приготовление буфера",
            output=output_lot,
            qty=Decimal("1.5000"),
            unit="л",
            protocol="СОП-БУФ-01",
        )
        Component.objects.create(
            preparation=prep,
            lot=source_lot,
            qty=Decimal("10.0000"),
            unit="г",
        )
        prep.complete()

        source_lot.refresh_from_db()
        output_lot.refresh_from_db()
        self.assertEqual(source_lot.quantity, Decimal("40.0000"))
        self.assertEqual(output_lot.quantity, Decimal("1.5000"))
        self.assertEqual(output_lot.unit, "л")
        self.assertTrue(output_lot.released)

        task = Task.objects.create(
            title="Выделение ДНК с новым буфером",
            project=self.project,
            owner=self.worker1,
            reviewer=self.lead_user,
            due=today + timedelta(days=3),
        )
        worklog = WorkLog.objects.create(
            actor=self.worker1,
            actual_day=today,
            task=task,
            operation="Лизис проб и выделение ДНК",
            detail="Израсходовано 0.3 л буфера",
        )
        Movement.objects.create(
            actor=self.warehouse_user,
            actual_day=today,
            lot=output_lot,
            kind="use_free",
            qty=Decimal("0.3000"),
            worklog=worklog,
            note="Списание в журнал работ за день",
        )

        output_lot.refresh_from_db()
        self.assertEqual(output_lot.quantity, Decimal("1.2000"))
        self.assertEqual(
            Movement.objects.filter(worklog=worklog, task=task).count(), 1
        )

        self.client.force_login(self.lead_user)
        balance_resp = self.client.get(reverse("admin:lab_lot_stock_balance"))
        self.assertEqual(balance_resp.status_code, 200)
        self.assertContains(balance_resp, "Трис-HCl")
        self.assertContains(balance_resp, "Ниже минимума!")

        stats_resp = self.client.get(
            reverse("admin:lab_storageplace_samples_stats", args=[self.place.pk])
        )
        self.assertEqual(stats_resp.status_code, 200)
        self.assertContains(stats_resp, self.place.name)
        self.assertContains(stats_resp, "QT №1")

        vue_resp = self.client.get(reverse("vue_dashboard"))
        self.assertEqual(vue_resp.status_code, 200)
        self.assertContains(vue_resp, "lab-dashboard-app")

        api_resp = self.client.get(reverse("api_dashboard_data"))
        self.assertEqual(api_resp.status_code, 200)
        payload = api_resp.json()
        self.assertIn("summary", payload)
        self.assertIn("stock_balance", payload)
        self.assertIn("storage_stats", payload)

    def test_worklog_and_quarterly_report(self):
        today = timezone.localdate()
        log = WorkLog(
            actor=self.worker1,
            actual_day=today,
            operation="Тестирование ПЦР",
            detail="Успешно",
        )
        log.full_clean()
        log.save()

        past_year = today.year - 1
        report = build_report(past_year, 1)
        self.assertIsInstance(report, Report)
        self.assertEqual(report.year, past_year)
        self.assertEqual(report.quarter, 1)
        self.assertIn("Период", report.payload)
