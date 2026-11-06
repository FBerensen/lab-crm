from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
from unittest import skipUnless
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, close_old_connections, connections
from django.test import TestCase, TransactionTestCase, RequestFactory
from django.urls import reverse
from django.utils import timezone
from .models import Project, Task, Sample, Lot, Movement, WorkLog, WorkExpenseRequest, WorkExpenseLine, UNITS
from .worklog6 import post_expense
from .worklog6_permissions import can_post

class Fixtures:
    def setUp(self):
        super().setUp()
        self.assertTrue(str(connection.settings_dict["NAME"]).startswith("test_labcrm_6a_"), "Refusing to test on a non-dedicated database")
        User = get_user_model()
        self.leader = User.objects.create_user(username="leader6a", is_active=True, is_staff=True)
        self.executor = User.objects.create_user(username="executor6a", is_active=True, is_staff=True)
        self.student = User.objects.create_user(username="student6a", is_active=True, is_staff=True)
        self.stock = User.objects.create_user(username="stock6a", is_active=True, is_staff=True)
        for user, name in ((self.leader,"Руководители"),(self.executor,"Исполнители"),(self.student,"Стажёры"),(self.stock,"Склад")):
            group, _ = Group.objects.get_or_create(name=name)
            user.groups.add(group)
        ct = ContentType.objects.get_for_model(WorkExpenseRequest)
        self.permission, _ = Permission.objects.get_or_create(content_type=ct, codename="post_worklog_expense", defaults={"name":"Проводить расход по журналу работ"})
        Group.objects.get(name="Склад").permissions.add(self.permission)
        self.project = Project.objects.create(title="6A test", kind="Проект", manager=self.leader)
        self.task = Task.objects.create(title="6A task", project=self.project, owner=self.executor, reviewer=self.leader, due=timezone.localdate())
        self.sample = Sample.objects.create(series="QA", number="1", project=self.project, kind="ДНК")
        self.task.samples.add(self.sample)
        self.lot = Lot.objects.create(name="6A product", unit=UNITS[0], initial_quantity=0, quantity=Decimal("10"), released=True)
        self.work = WorkLog.objects.create(actor=self.executor, task=self.task, operation="6A operation", detail="Result")
        self.work.samples.add(self.sample)
    def line(self, lot=None, qty="2", mode="use_free", work=None):
        return WorkExpenseLine.objects.create(worklog=work or self.work, lot=lot or self.lot, qty=Decimal(qty), mode=mode)
    def grant(self, user):
        user.user_permissions.add(self.permission)
        for name in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
            if hasattr(user, name): delattr(user, name)
    def balance(self, lot=None):
        obj = lot or self.lot
        obj.refresh_from_db()
        return obj.quantity, obj.reserved
    def client_work(self, user):
        self.client.force_login(user)
        return self.client.post(reverse("admin:lab_worklog_add"), {
            "actual_day": timezone.localdate().isoformat(), "task": str(self.task.pk),
            "operation": "Client operation", "detail": "Result", "samples": [str(self.sample.pk)],
            "expense_lines-TOTAL_FORMS": "1", "expense_lines-INITIAL_FORMS": "0",
            "expense_lines-MIN_NUM_FORMS": "0", "expense_lines-MAX_NUM_FORMS": "1000",
            "expense_lines-0-lot": str(self.lot.pk), "expense_lines-0-qty": "2",
            "expense_lines-0-mode": "use_free", "_save": "Save",
        })

class Stage6ATests(Fixtures, TestCase):
    def test_leader_and_stock_can_post(self):
        self.assertTrue(can_post(self.leader))
        self.assertTrue(can_post(self.stock))
    def test_executor_needs_permission(self):
        self.assertFalse(can_post(self.executor))
        self.grant(self.executor)
        self.assertTrue(can_post(self.executor))
    def test_trainee_overrides_permission_lead_and_superuser(self):
        self.grant(self.student)
        self.student.groups.add(Group.objects.get(name="Руководители"))
        self.student.is_superuser = True
        self.student.save()
        self.assertFalse(can_post(self.student))
    def test_inactive_cannot_post(self):
        self.leader.is_active = False
        self.assertFalse(can_post(self.leader))
    def test_pending_does_not_spend(self):
        self.line()
        self.assertEqual(self.balance(), (Decimal("10"), Decimal("0")))
        self.assertEqual(self.work.expense_request.status, "pending")
    def test_post_free_and_actor(self):
        line = self.line()
        self.assertTrue(post_expense(self.work.pk, self.leader))
        self.assertEqual(self.balance(), (Decimal("8"), Decimal("0")))
        move = Movement.objects.get(expense_line=line)
        self.assertEqual(move.actor_id, self.leader.pk)
        self.assertEqual(move.worklog_id, self.work.pk)
        self.assertEqual(move.task_id, self.task.pk)
        self.assertEqual(move.actual_day, self.work.actual_day)
    def test_repeat_post_is_noop(self):
        self.line()
        post_expense(self.work.pk, self.leader)
        self.assertFalse(post_expense(self.work.pk, self.leader))
        self.assertEqual(Movement.objects.filter(worklog=self.work).count(), 1)
        self.assertEqual(self.balance()[0], Decimal("8"))
    def test_without_permission_no_spend(self):
        self.line()
        with self.assertRaises(PermissionDenied): post_expense(self.work.pk, self.executor)
        self.assertEqual(self.balance()[0], Decimal("10"))
    def test_trainee_with_permission_cannot_confirm(self):
        self.line()
        self.grant(self.student)
        with self.assertRaises(PermissionDenied): post_expense(self.work.pk, self.student)
        self.assertEqual(self.balance()[0], Decimal("10"))
    def test_all_rows_rollback_on_insufficient_stock(self):
        second = Lot.objects.create(name="Second", unit=UNITS[0], initial_quantity=0, quantity=Decimal("1"))
        self.line()
        self.line(lot=second, qty="2")
        with self.assertRaises(ValidationError): post_expense(self.work.pk, self.leader)
        self.assertEqual(self.balance()[0], Decimal("10"))
        self.assertEqual(self.balance(second)[0], Decimal("1"))
        self.assertFalse(Movement.objects.filter(worklog=self.work).exists())
        self.assertEqual(WorkExpenseRequest.objects.get(worklog=self.work).status, "pending")
    def test_reserved_consumption_uses_task_reserve(self):
        Movement.objects.create(lot=self.lot, kind="reserve", qty=Decimal("3"), task=self.task, actor=self.leader, note="Reserve")
        self.line(mode="use_reserved")
        post_expense(self.work.pk, self.leader)
        self.assertEqual(self.balance(), (Decimal("8"), Decimal("1")))
    def test_other_task_reserve_cannot_be_used(self):
        other = Task.objects.create(title="Other", project=self.project, owner=self.executor, reviewer=self.leader, due=timezone.localdate())
        Movement.objects.create(lot=self.lot, kind="reserve", qty=Decimal("3"), task=other, actor=self.leader, note="Reserve")
        self.line(mode="use_reserved")
        with self.assertRaises(ValidationError): post_expense(self.work.pk, self.leader)
        self.assertEqual(self.balance(), (Decimal("10"), Decimal("3")))
    def test_posted_line_cannot_change_or_delete(self):
        line = self.line()
        post_expense(self.work.pk, self.leader)
        line.qty = Decimal("1")
        with self.assertRaises(ValidationError): line.save()
        with self.assertRaises(ValidationError): line.delete()
    def test_nonpositive_and_duplicate_lines_rejected(self):
        with self.assertRaises(ValidationError): self.line(qty="0")
        self.line()
        with self.assertRaises(ValidationError): self.line()
    def test_expired_product_rejected(self):
        from datetime import timedelta
        self.lot.expiry = timezone.localdate() - timedelta(days=1)
        self.lot.save()
        self.line()
        with self.assertRaises(ValidationError): post_expense(self.work.pk, self.leader)
        self.assertEqual(self.balance()[0], Decimal("10"))
    def test_admin_executor_auto_posts(self):
        self.grant(self.executor)
        response = self.client_work(self.executor)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.balance()[0], Decimal("8"))
        self.assertEqual(WorkLog.objects.filter(operation="Client operation").count(), 1)
    def test_admin_executor_without_permission_waits(self):
        response = self.client_work(self.executor)
        self.assertEqual(response.status_code, 302)
        work = WorkLog.objects.get(operation="Client operation")
        self.assertEqual(work.expense_request.status, "pending")
        self.assertEqual(self.balance()[0], Decimal("10"))
    def test_admin_trainee_always_waits_then_leader_confirms(self):
        self.grant(self.student)
        self.task.owner = self.student
        self.task.save()
        response = self.client_work(self.student)
        self.assertEqual(response.status_code, 302)
        work = WorkLog.objects.get(operation="Client operation")
        self.assertEqual(self.balance()[0], Decimal("10"))
        self.assertEqual(work.expense_request.status, "pending")
        post_expense(work.pk, self.leader)
        self.assertEqual(self.balance()[0], Decimal("8"))
    def test_admin_auto_failure_rolls_back_work_and_expenses(self):
        self.grant(self.executor)
        self.lot.quantity = Decimal("1")
        self.lot.save()
        response = self.client_work(self.executor)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(WorkLog.objects.filter(operation="Client operation").exists())
        self.assertEqual(self.balance()[0], Decimal("1"))
    def test_sample_endpoint_and_subset(self):
        request = RequestFactory().get("/", {"task": self.task.pk})
        request.user = self.executor
        response = admin.site._registry[WorkLog].task_samples_view(request)
        self.assertEqual(json.loads(response.content)["results"][0]["id"], self.sample.pk)
    def test_trainee_cannot_use_old_movement_add(self):
        self.student.groups.add(Group.objects.get(name="Склад"))
        request = RequestFactory().get("/")
        request.user = self.student
        self.assertFalse(admin.site._registry[Movement].has_add_permission(request))

@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locking")
class Stage6AConcurrencyTests(Fixtures, TransactionTestCase):
    def test_two_confirmations_create_one_set(self):
        self.line()
        work_id, user_id = self.work.pk, self.leader.pk
        def run():
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=user_id)
                return post_expense(work_id, user)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: run(), range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.balance()[0], Decimal("8"))
        self.assertEqual(Movement.objects.filter(worklog_id=work_id).count(), 1)
