from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

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


class LabCrmAcceptanceTests(TestCase):
    """Автоматические тесты по всем пунктам приёмки и ТЗ Lab CRM (п. 1–11)."""

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

    def test_roles_and_permissions(self):
        self.assertTrue(lead(self.lead_user))
        self.assertTrue(warehouse(self.lead_user))
        self.assertTrue(warehouse(self.warehouse_user))
        self.assertFalse(lead(self.warehouse_user))
        self.assertFalse(lead(self.worker1))
        self.assertFalse(warehouse(self.worker1))

    def test_sample_parent_series_and_number_split(self):
        """Проверка п. 2–5 ТЗ: ввод исходного образца по двум полям 'серия' и 'номер'."""
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

    def test_task_lifecycle_and_new_stages(self):
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

        # Чужой сотрудник не может принять задачу
        with self.assertRaises(ValidationError):
            TaskEvent.objects.create(
                task=task, actor=self.worker2, action="accept"
            )

        # Исполнитель принимает задачу в работу
        TaskEvent.objects.create(
            task=task,
            actor=self.worker1,
            action="accept",
            stage="Real-Time ПЦР",
        )
        task.refresh_from_db()
        self.assertEqual(task.status, "В работе")
        self.assertEqual(task.stage, "Real-Time ПЦР")

        # Остановка без обязательных данных отклоняется
        with self.assertRaises(ValidationError):
            TaskEvent.objects.create(
                task=task,
                actor=self.worker1,
                action="block",
                note="Не хватает праймеров",
            )

        # Корректная остановка задачи
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

        # Возобновление работы
        TaskEvent.objects.create(
            task=task, actor=self.worker1, action="resume"
        )
        task.refresh_from_db()
        self.assertEqual(task.status, "В работе")
        self.assertEqual(task.blocker, "")

        # Передача на проверку
        TaskEvent.objects.create(
            task=task,
            actor=self.worker1,
            action="submit",
            note="ДНК выделена, чистота 1.85",
        )
        task.refresh_from_db()
        self.assertEqual(task.status, "На проверке")

        # Другой руководитель не может утвердить вместо назначенного проверяющего
        with self.assertRaises(ValidationError):
            TaskEvent.objects.create(
                task=task, actor=self.lead2_user, action="approve"
            )

        # Назначенный проверяющий утверждает результат
        TaskEvent.objects.create(
            task=task, actor=self.lead_user, action="approve"
        )
        task.refresh_from_db()
        self.assertEqual(task.status, "Завершено")

        # Повтор создаёт новую связанную задачу
        TaskEvent.objects.create(
            task=task,
            actor=self.lead_user,
            action="repeat",
            expected=today + timedelta(days=7),
        )
        repeated = Task.objects.get(parent=task)
        self.assertEqual(repeated.status, "Назначено")
        self.assertEqual(repeated.samples.count(), 2)

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
        """Проверка п. 6–11 ТЗ: приготовление буфера (0.1–100000 + ед. изм.),
        расход в журнале работ и задачах, отображение на странице баланса и статистики мест хранения."""
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

        # Количество меньше 0.1 отклоняется валидатором (п. 9 ТЗ)
        bad_prep = Preparation(
            actor=self.lead_user,
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
            actor=self.lead_user,
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

        # Списание приготовленного буфера в Журнале работ по дням с привязкой к задаче (п. 11 ТЗ)
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
            actor=self.worker1,
            actual_day=today,
            lot=output_lot,
            kind="use_free",
            qty=Decimal("0.3000"),
            worklog=worklog,
            note="Списание в журнал работ за день",
        )

        output_lot.refresh_from_db()
        self.assertEqual(output_lot.quantity, Decimal("1.2000"))
        # Проверяем, что движение автоматически привязалось к задаче из WorkLog
        self.assertEqual(
            Movement.objects.filter(worklog=worklog, task=task).count(), 1
        )

        # Проверяем работу новых страниц в админке:
        # 1) Страница баланса и оповещений (/admin/lab/lot/stock-balance/)
        # 2) Страница статистики проб по месту хранения (/admin/lab/storageplace/<pk>/samples-stats/)
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
